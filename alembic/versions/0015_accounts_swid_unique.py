"""accounts: UNIQUE (tenant_id, swid) — one credential per tenant

THE DECISION, AND WHY IT HAS THIS SHAPE
---------------------------------------
0014 recorded that `accounts` carried no unique constraint at all, so `swid`
was not unique and two rows could hold the same ESPN credential, and that
whether to constrain it was "a product decision nobody has made". It has now
been made: it should be unique.

Scoped to the tenant, not global, and that is not a detail. A **global**
`UNIQUE (swid)` on a tenant-scoped table re-opens the enumeration oracle this
sprint spent two revisions closing: a colliding INSERT raises `23505`, which
row-level security does not hide, so one tenant learns another holds that
credential. 0012 closed exactly that for `raw_cache` and 0013 removed it from
`leagues`. `UNIQUE (tenant_id, swid)` keeps the guarantee inside the tenant,
where a collision is the tenant's own row and tells it nothing it did not
already know.

THE RESTORE COLLISION, MEASURED BEFORE THIS WAS WRITTEN
-------------------------------------------------------
The recovery bundle substitutes a placeholder for every account's `swid`. That
placeholder used to be ONE constant, so a tenant holding two accounts restored
to two identical `(tenant_id, swid)` pairs — and this constraint would have
turned the one path that exists for the worst day into a path that refuses.

Measured, not predicted: a two-account tenant round-tripped through
`build_logical_bundle` + `restore_bundle_to_scratch` produced
`['{REAUTH-REQUIRED}', '{REAUTH-REQUIRED}']`. No test had two accounts in one
tenant, which is why the suite would not have caught it. There is one now
(`test_two_accounts_in_one_tenant_survive_a_restore`), and the placeholder is
per-row distinct as of recovery format v3.

So the ORDER matters for anyone replaying this: the placeholder had to become
distinct before this constraint could exist.

WHAT AN EXISTING DATABASE MIGHT HOLD
------------------------------------
Nothing prevented duplicates until now, so a real database may already have
them. This refuses before touching anything, reporting the number of colliding
groups and the affected account ids — never the swid itself, which is a
credential identifier. An operator who hits that refusal has to decide which
row to keep; a migration is not the place to decide it for them.

THE CASCADE QUESTION
--------------------
Same as 0014's, and checked the same way at run time: `accounts` has a child
(`leagues.account_id`), a batch rebuild drops and recreates the table, and
Phase 36 measured that a cascading child empties silently with
`PRAGMA foreign_key_check` clean either way. `leagues.account_id` is NO ACTION
today; this asserts it rather than trusting a sentence.

Revision ID: 0015
Revises: 0014
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None

#: Named, so the downgrade can drop it by name and so SQLAlchemy can reflect
#: it. 0012 learned what an unnamed constraint costs four revisions later.
CONSTRAINT_NAME = "uq_account_tenant_swid"


def _refuse_existing_duplicates() -> None:
    """Refuse before the rebuild if the data already violates the constraint.

    The alternative is an opaque `IntegrityError` from the middle of a table
    rebuild, which tells an operator nothing about which rows are involved.

    The swid is NOT reported. It is a credential identifier, and a migration
    that prints one into a terminal, a log or a CI transcript has leaked it.
    Account ids are enough to find the rows and carry nothing on their own.
    """
    bind = op.get_bind()
    accounts = sa.table(
        "accounts",
        sa.column("id", sa.Integer),
        sa.column("tenant_id", sa.Integer),
        sa.column("swid", sa.String),
    )
    grouped = (
        sa.select(
            accounts.c.tenant_id,
            accounts.c.swid,
            sa.func.count().label("n"),
            sa.func.group_concat(accounts.c.id).label("ids")
            if bind.dialect.name == "sqlite"
            else sa.func.string_agg(sa.cast(accounts.c.id, sa.String), ",").label("ids"),
        )
        .group_by(accounts.c.tenant_id, accounts.c.swid)
        .having(sa.func.count() > 1)
    )
    collisions = bind.execute(grouped).fetchall()
    if not collisions:
        return
    detail = "; ".join(
        f"tenant {row.tenant_id}: {row.n} rows (account ids {row.ids})"
        for row in collisions
    )
    raise RuntimeError(
        f"{len(collisions)} tenant(s) already hold a duplicate swid, so "
        f"{CONSTRAINT_NAME} cannot be created: {detail}. Decide which row to "
        "keep and remove the others, then re-run. The swid values are "
        "deliberately not printed -- they are credential identifiers."
    )


def _assert_no_cascade_into_accounts() -> None:
    """0014's check, inlined again.

    Alembic loads revisions by path rather than as an importable package, so a
    cross-revision import is a failure that only appears under
    `alembic upgrade head` from a clean checkout. 0014 says the same.
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


def _guard_counts(before: tuple[int, int]) -> None:
    after = (_count("accounts"), _count("leagues"))
    if after != before:
        raise RuntimeError(
            "the accounts rebuild changed a row count: accounts "
            f"{before[0]} -> {after[0]}, leagues {before[1]} -> {after[1]}. The "
            "migration is refusing to commit a database it cannot account for."
        )


def upgrade() -> None:
    _refuse_existing_duplicates()
    _assert_no_cascade_into_accounts()
    before = (_count("accounts"), _count("leagues"))
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["tenant_id", "swid"])
    _guard_counts(before)


def downgrade() -> None:
    """Unconditionally possible, like 0014's and unlike 0012's and 0013's.

    Dropping a constraint cannot violate anything: every row that satisfied the
    stricter rule satisfies the looser one. There is no collision to count and
    nothing for an operator to decide.
    """
    _assert_no_cascade_into_accounts()
    before = (_count("accounts"), _count("leagues"))
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
    _guard_counts(before)
