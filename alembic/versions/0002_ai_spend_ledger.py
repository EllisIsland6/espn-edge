"""ai spend ledger

The one additive revision the phase contract calls for: two new tables, one
index, no change to any existing table. `ai_spend_months` is the atomic counter
the UTC-month ceiling is enforced against and `ai_spend_entries` is the audit
trail behind it; see api/services/spend.py for why the ceiling cannot be a SUM
and why every amount is an integer number of micro-dollars.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table('ai_spend_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reservation', sa.String(), nullable=False),
    sa.Column('month', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('reserved_micro_usd', sa.Integer(), nullable=False),
    sa.Column('settled_micro_usd', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('reservation', name='uq_ai_spend_entries_reservation')
    )
    with op.batch_alter_table('ai_spend_entries', schema=None) as batch_op:
        batch_op.create_index('ix_ai_spend_entries_month_state', ['month', 'state'], unique=False)

    op.create_table('ai_spend_months',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('month', sa.String(), nullable=False),
    sa.Column('committed_micro_usd', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('month', name='uq_ai_spend_months_month')
    )


def downgrade() -> None:
    """Refused. Dropping the ledger resets the UTC-month spend ceiling.

    The generated downgrade dropped both tables, and `alembic downgrade -1`
    followed by `upgrade head` was measured granting a fresh $5 in a month that
    was already spent, repeatable indefinitely -- from any shell in the
    container, with no argument and no confirmation, because `env.py` reads the
    URL from `Settings`. A migration that reverses a spend bound is a spend
    bound anyone with a shell can reset, so this one does not reverse.

    Removing the ledger deliberately is a deliberate act: drop the tables by
    hand, with the ceiling reset understood and recorded.
    """
    raise NotImplementedError(
        "the AI spend ledger is not reversible: dropping it resets the "
        "UTC-month ceiling (Phase 31 unit 4)"
    )
