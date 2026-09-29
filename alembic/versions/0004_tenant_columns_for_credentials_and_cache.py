"""tenant columns for the two tables the audit found unprotected

Phase 36's attack suite passed 13/13 and still missed these. An attack suite
can only probe tables the policies already cover, so the tables with no policy
were exactly the ones it could not test. The audit that followed found three,
and two of them are the most sensitive in the schema:

  - `accounts` holds the ESPN `swid` and the Fernet-encrypted `espn_s2`. It
    reaches no league, so there was no path to scope it BY -- it needed a
    tenant column of its own.
  - `raw_cache` holds raw ESPN payloads for private leagues.

(The third was the AI spend ledger, which is a decision rather than a missing
column; see 0005.)

Expand only: both columns are nullable, backfilled to the single tenant, and
reversible. The contract that makes them NOT NULL rides with the one for
`leagues.tenant_id` in `alembic/pending/`.

**A known hole this revision does not close.** `raw_cache`'s primary key stays
`key` alone, so two tenants cannot hold the same cache key: the second one's
INSERT fails on the primary key, and a uniqueness error is not something
row-level security hides. That tells tenant B that tenant A holds that key,
and since a key carries a league id and a hashed SWID, it is an enumeration
oracle. The fix is a composite `(tenant_id, key)` primary key, and it cannot
land here -- while half the rows share a NULL tenant the composite would not
be unique either. It belongs with the contract step, and it is recorded rather
than left to be discovered.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | None = None
depends_on: str | None = None

BACKFILL_SLUG = "default"


def upgrade() -> None:
    for table in ("accounts", "raw_cache"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f'fk_{table}_tenant_id', 'tenants', ['tenant_id'], ['id']
            )
            batch_op.create_index(f'ix_{table}_tenant_id', ['tenant_id'], unique=False)

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    tenants = sa.table('tenants', sa.column('id', sa.Integer), sa.column('slug', sa.String))
    tenant_id = bind.execute(
        sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
    ).scalar_one_or_none()
    if tenant_id is None:
        # 0003 creates it. If it is gone, something removed it deliberately and
        # inventing another one here would quietly split ownership in two.
        raise RuntimeError(
            f"the {BACKFILL_SLUG!r} tenant created by revision 0003 is missing; "
            "refusing to invent a second one to backfill against"
        )

    for table in ("accounts", "raw_cache"):
        target = sa.table(table, sa.column('tenant_id', sa.Integer))
        bind.execute(
            target.update().where(target.c.tenant_id.is_(None)).values(tenant_id=tenant_id)
        )


def downgrade() -> None:
    for table in ("accounts", "raw_cache"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(f'ix_{table}_tenant_id')
            batch_op.drop_constraint(f'fk_{table}_tenant_id', type_='foreignkey')
            batch_op.drop_column('tenant_id')
