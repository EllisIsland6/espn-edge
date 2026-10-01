"""durable schedules

Recurring intents, materialised into `jobs` rows. Replaces the in-process
scheduler, which had the same problem as the in-process queue: a restart loses
the schedule and a second process runs everything twice.

**Intervals, not cron.** A cron parser is a dependency and a parsing surface,
and the only recurrence this application needs is "every N minutes from an
anchor" -- which is also the only shape that yields deterministic slot
boundaries with no timezone library. Narrowed deliberately. A real cron spec
would be an additive column, not a rewrite.

`anchor_at` + `interval_seconds` defines a fixed grid of slots, and that grid
is what makes materialisation idempotent: the job's idempotency key is derived
from (schedule, slot), so any number of schedulers covering the same window
compute the same keys and the unique constraint on `jobs` collapses them into
one job each. **No leader election, no advisory lock, no primary scheduler --
the arithmetic does it.** That is the property Phase 39's acceptance names as
"three schedulers materialize one window".

`next_run_at` is a cursor rather than the truth: it stops a scheduler
rescanning history every tick. If it is wrong the slot arithmetic still
produces the right keys, so the worst case is wasted effort, not duplicated
work.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'schedules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('interval_seconds', sa.Integer(), nullable=False),
        sa.Column('anchor_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_schedules_tenant_name'),
    )
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.create_index('ix_schedules_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('ix_schedules_due', ['enabled', 'next_run_at'], unique=False)


def downgrade() -> None:
    """Reverses. Jobs already materialised are left alone -- they are real
    work now, and deleting them because their schedule went away would be a
    surprise rather than a rollback."""
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_index('ix_schedules_due')
        batch_op.drop_index('ix_schedules_tenant_id')
    op.drop_table('schedules')
