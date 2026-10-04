"""raw_cache: a composite (tenant_id, key) primary key

CLOSES A MEASURED ENUMERATION ORACLE
------------------------------------
`raw_cache`'s primary key was `key` alone, so two tenants could not hold the
same cache key: the second INSERT failed on the primary key, and **a uniqueness
error is not something row-level security hides.** That told tenant B that
tenant A holds that key, and a key carries a league id and a hashed SWID.

Revision 0004 recorded the hole in its own docstring and said why it could not
close it there: while half the rows share a NULL tenant, a composite key "would
not be unique either". Measured before this revision was written
(`.venv/phaseC/probe_oracle.py`), and the real answer is worse than that
sentence:

    today, pk = (key)                     tenant 1 accepted, tenant 2 REFUSED
                                          -> "UNIQUE constraint failed: raw_cache.key"
    pk = (tenant_id, key), NOT NULL       both accepted; a repeat within one
                                          tenant still refused
    pk = (tenant_id, key), NULLABLE       TWO rows with NULL tenant and the
                                          same key BOTH accepted

So with a nullable column the composite is not merely "not unique" -- SQLite
treats the NULLs as distinct and the primary key stops being a key at all,
while PostgreSQL refuses a nullable column in a primary key outright. The two
engines disagree, which is the exact shape of defect this project keeps
finding. **`NOT NULL` here is load-bearing, not hygiene**, and
`tests/test_raw_cache_tenant_key.py` removes it to prove that.

WHY THIS IS WRITTEN OUT BY HAND ON SQLITE
-----------------------------------------
Changing a primary key means rebuilding the table, and Phase 36 measured what
a rebuild can do: `alembic upgrade head` emptied all nine of `leagues`'
cascading children and exited zero, with `PRAGMA foreign_key_check` clean
either way. Nothing references `raw_cache` -- checked, not assumed, and
asserted below -- so no cascade can fire here. But the row count is still
compared before and after and the migration refuses to finish if it changed.
A migration that loses rows must say so itself; discovering it later from a
backup is not a plan.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | None = None
depends_on: str | None = None

#: The tenant orphaned rows are assigned to, matching 0003 and the parked
#: contract revision. Rows written before tenancy existed are private-operator
#: rows by the same argument 0003 used.
BACKFILL_SLUG = "default"


def _rows(bind) -> int:
    return bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("raw_cache"))
    ).scalar_one()


def _backfill(bind) -> None:
    """Give every tenantless row the default tenant, or there is no key.

    A composite key over a nullable column is not a key (see the module
    docstring), so this is a precondition of the change rather than tidying.
    """
    cache = sa.table("raw_cache", sa.column("tenant_id", sa.Integer))
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Integer),
        sa.column("slug", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    orphans = bind.execute(
        sa.select(sa.func.count())
        .select_from(cache)
        .where(cache.c.tenant_id.is_(None))
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
        cache.update().where(cache.c.tenant_id.is_(None)).values(tenant_id=tenant_id)
    )


def _assert_nothing_references_raw_cache(bind) -> None:
    """The claim that makes a rebuild safe here, checked at runtime.

    If a future revision adds a child table with `ON DELETE CASCADE`, the
    rebuild below becomes the Phase 36 defect again. Better to fail loudly on
    the next database than to leave the safety of this revision resting on a
    sentence written today.
    """
    if bind.dialect.name != "sqlite":
        return
    for (name,) in bind.exec_driver_sql(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        for row in bind.exec_driver_sql(f"PRAGMA foreign_key_list({quoted})"):
            if row[2] == "raw_cache":
                raise RuntimeError(
                    f"{name} now references raw_cache; rebuilding it could fire "
                    "ON DELETE CASCADE, which is the Phase 36 defect. Review "
                    "this revision before continuing."
                )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill(bind)
    _assert_nothing_references_raw_cache(bind)
    before = _rows(bind)

    if bind.dialect.name == "sqlite":
        # Written out rather than left to batch mode. A rebuild is the one
        # operation this project has measured losing data silently, so every
        # step is visible and the row count is checked at the end.
        #
        # DO NOT WRAP THE `CONSTRAINT ... FOREIGN KEY` LINES. SQLAlchemy's
        # SQLite dialect recovers constraint names by matching the stored DDL
        # text, and a newline between `CONSTRAINT <name>` and `FOREIGN KEY`
        # defeats the match: the key reflects as unnamed, and 0004's downgrade
        # -- four revisions below this one -- then fails with
        # `No such constraint: 'fk_raw_cache_tenant_id'`. Measured, after
        # wrapping it for readability did exactly that.
        bind.exec_driver_sql(
            """
            CREATE TABLE raw_cache_new (
                "key" VARCHAR NOT NULL,
                fetched_at DATETIME NOT NULL,
                payload_json JSON,
                tenant_id INTEGER NOT NULL,
                CONSTRAINT pk_raw_cache PRIMARY KEY (tenant_id, "key"),
                CONSTRAINT fk_raw_cache_tenant_id FOREIGN KEY(tenant_id) REFERENCES tenants (id)
            )
            """
        )
        bind.exec_driver_sql(
            'INSERT INTO raw_cache_new ("key", fetched_at, payload_json, tenant_id) '
            'SELECT "key", fetched_at, payload_json, tenant_id FROM raw_cache'
        )
        bind.exec_driver_sql("DROP TABLE raw_cache")
        bind.exec_driver_sql("ALTER TABLE raw_cache_new RENAME TO raw_cache")
        bind.exec_driver_sql(
            "CREATE INDEX ix_raw_cache_tenant_id ON raw_cache (tenant_id)"
        )
    else:
        op.alter_column(
            "raw_cache", "tenant_id", existing_type=sa.Integer(), nullable=False
        )
        op.drop_constraint("raw_cache_pkey", "raw_cache", type_="primary")
        op.create_primary_key("pk_raw_cache", "raw_cache", ["tenant_id", "key"])

    after = _rows(bind)
    if after != before:
        raise RuntimeError(
            f"raw_cache rebuild changed the row count: {before} -> {after}. "
            "The migration has not lost them silently; it is refusing to "
            "commit a table it cannot account for."
        )


def downgrade() -> None:
    """Possible only when no two tenants share a key, and it checks first.

    Restoring `PRIMARY KEY (key)` restores a rule the data may already violate
    -- which is the entire reason the composite exists. Recreating it on such a
    database raises from inside a rebuild and reads as a migration defect
    rather than as what it is. So the collisions are counted and refused by
    name; choosing which tenant loses its cached payload is an operator's call,
    not a migration's.
    """
    bind = op.get_bind()
    cache = sa.table("raw_cache", sa.column("key", sa.String))
    dupes = (
        sa.select(cache.c.key).group_by(cache.c.key).having(sa.func.count() > 1)
    )
    colliding = bind.execute(
        sa.select(sa.func.count()).select_from(dupes.subquery())
    ).scalar_one()
    if colliding:
        raise RuntimeError(
            f"cannot restore the single-column primary key: {colliding} cache "
            "key(s) are held by more than one tenant. That is legal at "
            "revision 0012 and illegal at 0011. Resolve the duplicates before "
            "downgrading -- a migration should not choose which tenant loses "
            "its cached payload."
        )

    before = _rows(bind)
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP INDEX IF EXISTS ix_raw_cache_tenant_id")
        bind.exec_driver_sql(
            """
            CREATE TABLE raw_cache_old (
                "key" VARCHAR NOT NULL,
                fetched_at DATETIME NOT NULL,
                payload_json JSON,
                tenant_id INTEGER,
                PRIMARY KEY ("key"),
                CONSTRAINT fk_raw_cache_tenant_id FOREIGN KEY(tenant_id) REFERENCES tenants (id)
            )
            """
        )
        bind.exec_driver_sql(
            'INSERT INTO raw_cache_old ("key", fetched_at, payload_json, tenant_id) '
            'SELECT "key", fetched_at, payload_json, tenant_id FROM raw_cache'
        )
        bind.exec_driver_sql("DROP TABLE raw_cache")
        bind.exec_driver_sql("ALTER TABLE raw_cache_old RENAME TO raw_cache")
        bind.exec_driver_sql(
            "CREATE INDEX ix_raw_cache_tenant_id ON raw_cache (tenant_id)"
        )
    else:
        op.drop_constraint("pk_raw_cache", "raw_cache", type_="primary")
        op.create_primary_key("raw_cache_pkey", "raw_cache", ["key"])
        op.alter_column(
            "raw_cache", "tenant_id", existing_type=sa.Integer(), nullable=True
        )

    after = _rows(bind)
    if after != before:
        raise RuntimeError(
            f"raw_cache rebuild changed the row count: {before} -> {after}."
        )
