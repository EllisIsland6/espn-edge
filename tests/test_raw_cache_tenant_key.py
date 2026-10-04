"""`raw_cache`'s composite primary key, and the oracle it closes.

THE DEFECT, AS IT ACTUALLY BEHAVED
----------------------------------
With the primary key on `key` alone, two tenants could not hold the same cache
key: the second INSERT failed with a uniqueness error, and **a uniqueness error
is not something row-level security hides.** So tenant B learned that tenant A
holds that key — and a key carries a league id and a hashed SWID. Migration
0004 recorded the hole in its own docstring and could not close it there.

Everything below is provable on SQLite, with no row-level security, because the
leak was never in the policy. It was in the *constraint*: the thing that
refuses is the thing that tells you. That is why this file exists rather than
another PostgreSQL attack script.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.models import RawCache, Tenant
from api.services.cache import DBRawCache
from api.tenancy import bind_session

KEY = "espn:league:1234567:2026:mSettings"


@pytest.fixture
def tenants(db_session) -> tuple[int, int]:
    """Two tenants. The second is what makes any of this measurable."""
    first = db_session.query(Tenant).one()
    second = Tenant(slug="beta", created_at=datetime.now(UTC))
    db_session.add(second)
    db_session.flush()
    return first.id, second.id


def _write(session, tenant_id: int, key: str = KEY, payload=None) -> None:
    session.add(
        RawCache(
            key=key,
            tenant_id=tenant_id,
            fetched_at=datetime.now(UTC),
            payload_json=payload if payload is not None else {"t": tenant_id},
        )
    )
    session.flush()


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------


def test_two_tenants_can_hold_the_same_cache_key(db_session, tenants):
    """The defect, gone.

    Before this change the second write raised
    `UNIQUE constraint failed: raw_cache.key`, and that refusal was the leak.
    """
    first, second = tenants
    _write(db_session, first)
    _write(db_session, second)

    rows = db_session.execute(
        select(RawCache).where(RawCache.key == KEY)
    ).scalars().all()
    assert sorted(r.tenant_id for r in rows) == sorted([first, second])


def test_one_tenant_still_cannot_hold_the_same_key_twice(db_session, tenants):
    """The other half, and the half a looser fix would have lost.

    Dropping the constraint entirely would also have stopped the leak, and
    would have let one tenant accumulate unbounded duplicate rows for a single
    cache key. It is still a key; it is just a wider one.
    """
    first, _ = tenants
    _write(db_session, first)
    with pytest.raises(IntegrityError):
        _write(db_session, first)


def test_the_primary_key_is_tenant_first(db_session):
    """Order is not cosmetic here.

    `(tenant_id, key)` has a useful prefix — "this tenant's cached keys".
    `(key, tenant_id)` indexes "which tenants hold this key", which is the
    enumeration direction this change exists to close. Asserted on the
    constraint rather than on column declaration order, because those differ
    and the constraint is what the database enforces.
    """
    from api.db import Base

    table = Base.metadata.tables["raw_cache"]
    assert [c.name for c in table.primary_key.columns] == ["tenant_id", "key"]


def test_the_tenant_column_is_not_nullable(db_session):
    from api.db import Base

    assert Base.metadata.tables["raw_cache"].columns["tenant_id"].nullable is False


def test_not_null_is_load_bearing_not_hygiene(tmp_path):
    """Why the NOT NULL cannot be dropped "for flexibility".

    SQLite permits NULL in a primary-key column (outside INTEGER PRIMARY KEY /
    WITHOUT ROWID) and treats NULLs as distinct for uniqueness. So a composite
    key over a nullable column is not a weaker key — it is **no key at all**
    for the rows that matter, and a tenantless row could be written twice.
    PostgreSQL refuses a nullable column in a primary key outright, so the two
    engines disagree about the same schema.

    Built by hand rather than through the model, because the point is what
    SQLite does with a shape the model deliberately does not have. An
    instrument check: it shows the trap is real, which is what makes the NOT
    NULL above worth asserting.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        'CREATE TABLE c ("key" TEXT NOT NULL, tenant_id INTEGER, '
        'PRIMARY KEY (tenant_id, "key"))'
    )
    conn.execute('INSERT INTO c("key", tenant_id) VALUES (?, NULL)', (KEY,))
    conn.execute('INSERT INTO c("key", tenant_id) VALUES (?, NULL)', (KEY,))
    assert conn.execute("SELECT count(*) FROM c").fetchone()[0] == 2, (
        "SQLite refused duplicate NULLs in a primary key; the premise of the "
        "NOT NULL has changed and the migration's reasoning needs re-reading"
    )

    # ...and with NOT NULL, the same two writes cannot both land.
    conn.execute(
        'CREATE TABLE d ("key" TEXT NOT NULL, tenant_id INTEGER NOT NULL, '
        'PRIMARY KEY (tenant_id, "key"))'
    )
    conn.execute('INSERT INTO d("key", tenant_id) VALUES (?, 1)', (KEY,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO d("key", tenant_id) VALUES (?, 1)', (KEY,))
    conn.close()


# --------------------------------------------------------------------------
# The read path
# --------------------------------------------------------------------------


def test_the_cache_read_does_not_cross_tenants(db_session, tenants):
    """The change with the most behaviour in it.

    Both lookups used to be `session.get(RawCache, key)` — a primary-key
    lookup on `key` alone, which returned whichever tenant's row held it. On
    PostgreSQL the policy filtered that; offline nothing did. Now the query
    names both columns, so this is enforced by the statement rather than by
    something elsewhere being switched on.
    """
    first, second = tenants
    _write(db_session, second, payload={"owner": "beta"})

    bind_session(db_session, first)
    assert DBRawCache(db_session).get(KEY) is None, (
        "the first tenant read the second tenant's cached ESPN payload"
    )

    bind_session(db_session, second)
    assert DBRawCache(db_session).get(KEY) == {"owner": "beta"}


def test_a_write_does_not_overwrite_another_tenants_row(db_session, tenants):
    """The same mistake in the other direction.

    `set` read the row by key alone and then mutated it, so a second tenant
    writing the same key would have *replaced* the first tenant's payload
    rather than storing its own.
    """
    first, second = tenants
    _write(db_session, second, payload={"owner": "beta"})

    bind_session(db_session, first)
    DBRawCache(db_session).set(KEY, {"owner": "alpha"})
    db_session.flush()

    rows = {
        r.tenant_id: r.payload_json
        for r in db_session.execute(
            select(RawCache).where(RawCache.key == KEY)
        ).scalars()
    }
    assert rows == {first: {"owner": "alpha"}, second: {"owner": "beta"}}


def test_a_stale_row_is_still_a_miss(db_session, tenants):
    """The freshness rule survived the rewrite."""
    first, _ = tenants
    bind_session(db_session, first)
    _write(db_session, first)
    row = db_session.execute(
        select(RawCache).where(RawCache.tenant_id == first)
    ).scalar_one()
    row.fetched_at = datetime.now(UTC) - timedelta(days=30)
    db_session.flush()
    assert DBRawCache(db_session).get(KEY) is None


def test_an_unbound_session_is_refused_rather_than_served(db_session, tenants):
    """`current_tenant_id` raises on an unbound session, so a caller that
    forgot to bind gets an error instead of another tenant's payload."""
    from api.tenancy import TenantNotResolved

    db_session.info.pop("tenant_id", None)
    with pytest.raises((TenantNotResolved, RuntimeError)):
        DBRawCache(db_session).get(KEY)


# --------------------------------------------------------------------------
# The migration itself
# --------------------------------------------------------------------------


def _config(db_path):
    from tests.test_spend import _alembic_config

    return _alembic_config(db_path)


def test_the_migration_backfills_and_loses_nothing(tmp_path):
    """Phase 36's lesson, applied to a table rebuild that changes a key.

    `alembic upgrade head` once emptied all nine of `leagues`' cascading
    children and exited zero, with `PRAGMA foreign_key_check` clean either
    way. So this seeds rows across the three interesting cases — one written
    before tenancy existed, one owned by the default tenant, one owned by a
    second — runs the revision, and checks the count, the payloads and the
    backfill.

    The migration also checks its own row count and refuses to finish if it
    changed. Both exist on purpose: the in-migration check protects an
    operator's database, this one protects the next person's change.
    """
    from alembic import command

    db_path = tmp_path / "rawcache.db"
    config = _config(db_path)
    command.upgrade(config, "0011")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO tenants(id, slug, created_at) VALUES (9, 'beta', '2026-01-01')"
    )
    seeded = [
        ("k:tenantless", "2026-01-01", '{"a":1}', None),
        ("k:default", "2026-01-02", '{"b":2}', 1),
        ("k:beta", "2026-01-03", '{"c":3}', 9),
    ]
    conn.executemany(
        'INSERT INTO raw_cache("key", fetched_at, payload_json, tenant_id) '
        "VALUES (?,?,?,?)",
        seeded,
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "0012")

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM raw_cache").fetchone()[0] == len(
            seeded
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM raw_cache WHERE tenant_id IS NULL"
            ).fetchone()[0]
            == 0
        ), "the tenantless row was not backfilled, so the key is not a key"
        assert sorted(
            r[0] for r in conn.execute("SELECT payload_json FROM raw_cache")
        ) == sorted(row[2] for row in seeded)
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
        pk = [r[1] for r in conn.execute("PRAGMA table_info(raw_cache)") if r[5]]
        assert set(pk) == {"tenant_id", "key"}
    finally:
        conn.close()


def test_the_named_foreign_key_survives_the_downgrade(tmp_path):
    """A find that cost four revisions of distance to notice.

    SQLAlchemy's SQLite dialect recovers constraint NAMES by matching the
    stored DDL text. The first version of this migration wrapped
    `CONSTRAINT fk_raw_cache_tenant_id` and `FOREIGN KEY(...)` onto two lines
    for readability, which defeated the match — so after 0012's downgrade the
    foreign key reflected as *unnamed*, and **0004's downgrade, four revisions
    further down, failed with `No such constraint:
    'fk_raw_cache_tenant_id'`.** The constraint was there the whole time; only
    its name was invisible.

    Asserted by reflection rather than by reading the DDL, because reflection
    is what alembic's batch mode uses and therefore what actually matters.
    """
    from sqlalchemy import create_engine, inspect

    from alembic import command

    db_path = tmp_path / "fkname.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0011")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        names = {
            fk.get("name")
            for fk in inspect(engine).get_foreign_keys("raw_cache")
        }
    finally:
        engine.dispose()
    assert "fk_raw_cache_tenant_id" in names, (
        f"the foreign key reflected as {names}; a wrapped CONSTRAINT line hides "
        "the name from SQLAlchemy and breaks 0004's downgrade"
    )
