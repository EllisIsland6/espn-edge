"""accounts: tenant_id NOT NULL — the last column to contract

THE TABLE THIS IS ABOUT
-----------------------
`accounts` holds the ESPN `swid` and the Fernet-encrypted `espn_s2`. The Phase
36 audit's first finding was that it is the most sensitive table in the schema
and had no tenant column, and therefore no row-level security policy at all.
Revision 0004 gave it one, nullable, and backfilled. This closes that window —
the last of the three, after `raw_cache` at 0012 and `leagues` at 0013.

What a nullable tenant means here, concretely: a credential row with no tenant
is invisible to every policy and therefore to everyone. It fails closed, but
silently, and the thing that goes quiet is a stored ESPN credential.

WHAT THIS REVISION DOES *NOT* DO
--------------------------------
No constraint swap. `leagues` needed one, because its old global
`uq_league_season` made two tenants holding the same ESPN league impossible to
INSERT. `accounts` was measured before this was written and carries **no
unique constraint at all** — only a primary key and the `tenant_id` index — so
there is nothing to scope and nothing to drop.

That absence is worth stating rather than passing over: `swid` is not unique,
so two rows may hold the same ESPN credential, within a tenant or across
tenants. Whether that should be constrained is a product decision nobody has
made, and this revision is not the place to make it.

THE CASCADE QUESTION, MEASURED
------------------------------
Unlike `raw_cache`, this table has a child: `leagues.account_id`. A batch
rebuild drops and recreates the table, and Phase 36 measured what that does
under enforced foreign keys with `ON DELETE CASCADE` — nine child tables
emptied, exit zero, `PRAGMA foreign_key_check` clean either way.

So it was checked rather than assumed: `leagues.account_id` is **NO ACTION**,
not CASCADE, in both the models and the migrated schema. Nothing can cascade
from this rebuild. And two nets remain either way — `alembic/env.py` turns
foreign keys off for the migration and runs `PRAGMA foreign_key_check`
afterwards, raising on any dangling reference.

Revision ID: 0014
Revises: 0013
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None

#: The tenant orphaned rows are assigned to, matching 0003, 0012 and 0013.
BACKFILL_SLUG = "default"


def _catch_up_backfill() -> None:
    """Re-run 0004's backfill for rows written during the rollback window.

    This is the point of splitting a contract from its expand: between 0004 and
    this revision the old application code is still running and still inserting
    accounts with no tenant. Those rows are private-operator rows by the same
    argument 0004 used, and they belong to the same tenant. Without this the
    NOT NULL fails on a production database for a reason that looks like a
    migration bug.

    Deliberately inlined rather than imported from 0004: alembic loads
    revisions by path, not as an importable package, and a cross-revision
    import is a failure that only shows up under `alembic upgrade head` from a
    clean checkout.
    """
    bind = op.get_bind()
    accounts = sa.table("accounts", sa.column("tenant_id", sa.Integer))
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Integer),
        sa.column("slug", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    orphans = bind.execute(
        sa.select(sa.func.count())
        .select_from(accounts)
        .where(accounts.c.tenant_id.is_(None))
    ).scalar_one()
    if not orphans:
        return

    tenant_id = bind.execute(
        sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
    ).scalar_one_or_none()
    if tenant_id is None:
        bind.execute(
            tenants.insert().values(slug=BACKFILL_SLUG, created_at=sa.func.now())
        )
        tenant_id = bind.execute(
            sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
        ).scalar_one()

    bind.execute(
        accounts.update()
        .where(accounts.c.tenant_id.is_(None))
        .values(tenant_id=tenant_id)
    )


def _assert_no_cascade_into_accounts() -> None:
    """The claim that makes this rebuild safe, checked at runtime.

    `leagues.account_id` is NO ACTION today. If a future revision makes it — or
    any new child — `ON DELETE CASCADE`, this rebuild becomes the Phase 36
    defect: rows vanish and `PRAGMA foreign_key_check` stays clean. Better to
    fail loudly on the next database than to leave the safety of this revision
    resting on a sentence written today.
    """
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for (name,) in bind.exec_driver_sql(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        for row in bind.exec_driver_sql(f"PRAGMA foreign_key_list({quoted})"):
            # row: (id, seq, table, from, to, on_update, on_delete, match)
            if row[2] == "accounts" and str(row[6]).upper().startswith("CASCADE"):
                raise RuntimeError(
                    f"{name}.{row[3]} now cascades from accounts. Rebuilding "
                    "accounts would delete its rows silently -- the Phase 36 "
                    "defect. Review this revision before continuing."
                )


def _count(table: str) -> int:
    return (
        op.get_bind()
        .execute(sa.select(sa.func.count()).select_from(sa.table(table)))
        .scalar_one()
    )


def upgrade() -> None:
    _catch_up_backfill()
    _assert_no_cascade_into_accounts()
    before_accounts, before_leagues = _count("accounts"), _count("leagues")

    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.alter_column(
            "tenant_id", existing_type=sa.Integer(), nullable=False
        )

    after_accounts, after_leagues = _count("accounts"), _count("leagues")
    if (after_accounts, after_leagues) != (before_accounts, before_leagues):
        raise RuntimeError(
            "the accounts rebuild changed a row count: accounts "
            f"{before_accounts} -> {after_accounts}, leagues "
            f"{before_leagues} -> {after_leagues}. The migration is refusing to "
            "commit a database it cannot account for."
        )


def downgrade() -> None:
    """Unconditionally possible, unlike 0012's and 0013's.

    Widening a NOT NULL to nullable cannot violate anything: every existing row
    already has a value. There is no collision to count and nothing for an
    operator to decide, which is why this has no refusal path and the other two
    do.
    """
    before_accounts, before_leagues = _count("accounts"), _count("leagues")
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.alter_column(
            "tenant_id", existing_type=sa.Integer(), nullable=True
        )
    after_accounts, after_leagues = _count("accounts"), _count("leagues")
    if (after_accounts, after_leagues) != (before_accounts, before_leagues):
        raise RuntimeError(
            "the accounts rebuild changed a row count: accounts "
            f"{before_accounts} -> {after_accounts}, leagues "
            f"{before_leagues} -> {after_leagues}."
        )
