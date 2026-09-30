"""durable job queue

Replaces in-process scheduling. APScheduler keeps its queue in memory, so a
restart loses whatever had not run and a second process runs everything twice.
This table is the queue instead: a crash loses at most one attempt's work, and
the row is the only thing that decides what happens next.

Two indexes, each for one query the worker actually issues: claiming the next
job (`state`, `available_at`) and reclaiming expired leases (`state`,
`lease_expires_at`). Neither is speculative.

Idempotency is unique per TENANT, not globally. Two tenants syncing the same
ESPN league are two jobs, and a global key would silently collapse them into
one -- exactly the mistake `uq_league_season` made, which Phase 36 had to
measure before anyone believed it.

`tenant_id` is nullable, like every other tenant column in this schema during
the expand window. A job with no tenant is invisible under row-level security,
which fails closed.

No RLS policy here. The worker claims across tenants by design -- one process
serves everyone -- so a policy would have to be bypassed to work at all.
Enforcement for jobs is the tenant binding the worker sets before it *runs*
one, not a predicate on the claim.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('idempotency_key', sa.String(), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_owner', sa.String(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_jobs_tenant_key'),
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_jobs_claimable', ['state', 'available_at'], unique=False)
        batch_op.create_index('ix_jobs_lease', ['state', 'lease_expires_at'], unique=False)


def downgrade() -> None:
    """Reverses, and discards queued work.

    Dropping the table throws away whatever had not run. That is the honest
    meaning of reversing "add a queue" and it is stated rather than guarded --
    the alternative is a downgrade that refuses, which is a downgrade nobody
    can use.
    """
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_lease')
        batch_op.drop_index('ix_jobs_claimable')
        batch_op.drop_index('ix_jobs_tenant_id')
    op.drop_table('jobs')
