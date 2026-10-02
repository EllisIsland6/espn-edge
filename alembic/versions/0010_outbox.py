"""transactional outbox

Side effects that must happen exactly once after a state change commits.

Without it there are three options and all three are wrong. Write the change
then notify: the process dies in between, the change happened and nobody was
told. Notify then write: the change rolls back and you have told people about
something that did not happen -- worse, because you cannot un-tell. Both in
one transaction: impossible across a process boundary without a distributed
transaction.

The outbox makes the notification *part of* the state change. The row is
written in the same transaction as the business write, so it commits with it
or not at all, and a relay delivers it afterwards.

What this buys is **at-least-once**, not exactly-once. A relay that delivers
and dies before marking will deliver again. Exactly-once across a process
boundary is not available, so `dedupe_key` is carried for the receiver to
recognise a repeat -- making the duplicate cheap rather than pretending it
cannot happen.

One index, for the only query the relay issues: undelivered and due.

No row-level security policy, for the same reason as `jobs`: one relay process
drains for every tenant, so a policy on the scan would have to be bypassed to
function. The tenant travels in the row for the receiver's benefit.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('topic', sa.String(), nullable=False),
        sa.Column('dedupe_key', sa.String(), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.create_index('ix_outbox_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index(
            'ix_outbox_undelivered', ['delivered_at', 'available_at'], unique=False
        )


def downgrade() -> None:
    """Reverses, and discards anything not yet delivered.

    Stated rather than guarded. Reversing "add an outbox" means the pending
    notifications go with it, and a downgrade that refuses is a downgrade
    nobody can use.
    """
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_index('ix_outbox_undelivered')
        batch_op.drop_index('ix_outbox_tenant_id')
    op.drop_table('outbox')
