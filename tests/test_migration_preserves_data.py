"""A migration that reports success and deletes your data is a passing build.

Phase 36 ran `alembic upgrade head` against a seeded SQLite database and it
emptied every one of the nine tables that cascade off `leagues`. It logged
"Running upgrade 0002 -> 0003" and exited zero.

Two independent defects, both of which pass every schema-shaped check:

1. **The cascade.** With foreign keys enforced, SQLite's `DROP TABLE` performs
   an implicit `DELETE` that fires `ON DELETE CASCADE`. Alembic's batch mode
   rebuilds a table by copy-drop-rename, so any batch operation on a parent
   table deletes its children. `PRAGMA foreign_key_check` is CLEAN afterwards
   -- the rows are gone, not dangling -- so the obvious guard does not see it,
   and neither does a schema-comparison test, because the schema is correct.
   Only rows put in beforehand reveal it.

2. **The silent no-commit.** The first fix issued `PRAGMA foreign_keys=OFF`
   through the connection, which implicitly BEGINs a SQLAlchemy transaction.
   Alembic's `begin_transaction()` then returns a no-op context manager, so
   the migration ran and was never committed. `alembic upgrade 0002` logged
   success against a database it left empty.

Both were found by seeding rows and looking afterwards, which is the only
thing that distinguishes them from a clean run. This test is that look.

It is a slow test by this suite's standards -- it runs the real migration
chain against a real file -- and it is worth it: the failure it catches is
total and silent, and it is in the code path that runs against the operator's
production database.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.test_spend import _alembic_config

#: Every table that cascades off `leagues`. Derived rather than listed, so a
#: new child is covered the day it is added and not the day someone remembers.
CASCADE_PROBE_TABLE = "teams"


def _seed(db_path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for league_id, espn_id in ((1, "111"), (2, "222")):
            # This seeds a database at revision 0002, where `tenant_id` does
            # not exist yet -- the whole point is to migrate it forward. So no
            # tenant here, deliberately, and revision 0013's catch-up backfill
            # is what gives these rows one.
            connection.execute(
                "INSERT INTO leagues (id, espn_league_id, season, lifecycle, "
                "is_public) VALUES (?, ?, ?, ?, ?)",
                (league_id, espn_id, 2026, "active", 0),
            )
            connection.execute(
                "INSERT INTO teams (league_id, espn_team_id, is_me, autodrafted, "
                "wins, losses, ties, points_for, points_against) "
                "VALUES (?, ?, 0, 0, 0, 0, 0, 0.0, 0.0)",
                (league_id, 7),
            )
        connection.commit()
    finally:
        connection.close()


def _cascading_children(db_path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name
            for name in names
            for row in connection.execute(f"PRAGMA foreign_key_list({name})")
            if row[2] == "leagues" and row[6] == "CASCADE"
        }
    finally:
        connection.close()


@pytest.fixture
def migrated_to_0002(tmp_path):
    from alembic import command

    db_path = tmp_path / "preserve.db"
    command.upgrade(_alembic_config(db_path), "0002")
    return db_path


def test_the_probe_table_really_does_cascade(migrated_to_0002):
    """Guards the guard. If `teams` stopped cascading off `leagues`, the test
    below would go green for the wrong reason -- rows that survive because
    nothing would have deleted them, not because the migration protected
    them. That is this project's most frequent defect and it is cheap to
    exclude here."""
    children = _cascading_children(migrated_to_0002)
    assert CASCADE_PROBE_TABLE in children, (
        f"{CASCADE_PROBE_TABLE} no longer cascades off leagues; the "
        f"preservation test below no longer proves anything. Cascading "
        f"children are: {sorted(children)}"
    )
    assert len(children) >= 9, (
        f"only {len(children)} cascading children found; expected the nine "
        f"Phase 36 measured. Got: {sorted(children)}"
    )


def test_upgrading_to_head_preserves_child_rows(migrated_to_0002):
    """The regression. Remove the `foreign_keys=OFF` guard in alembic/env.py
    and this fails with `teams` empty, while every other test stays green."""
    from alembic import command

    _seed(migrated_to_0002)
    command.upgrade(_alembic_config(migrated_to_0002), "head")

    connection = sqlite3.connect(migrated_to_0002)
    try:
        # Defect 2 first: a fresh connection reading the version proves the
        # migration was COMMITTED, not merely executed. Everything below is
        # meaningless if this is empty.
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        assert version, (
            "alembic_version is empty after `upgrade head`: the migration ran "
            "without committing. Check that no transaction is open before "
            "alembic's begin_transaction() in env.py."
        )

        teams = connection.execute("SELECT id, league_id FROM teams").fetchall()
        assert teams == [(1, 1), (2, 2)], (
            f"child rows did not survive the migration: {teams!r}. The batch "
            f"rebuild of `leagues` cascade-deleted them. Note that "
            f"`PRAGMA foreign_key_check` is clean either way."
        )

        leagues = connection.execute(
            "SELECT id, espn_league_id, tenant_id FROM leagues"
        ).fetchall()
        assert [row[:2] for row in leagues] == [(1, "111"), (2, "222")]
        assert all(row[2] is not None for row in leagues), (
            f"0003 left a league with no tenant: {leagues!r}. Backfill did not "
            f"run, or ran before the column existed."
        )

        assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        # --- revision 0013, the contract step -----------------------------
        # These rows were seeded at 0002, before `tenant_id` existed. 0003
        # added it nullable and backfilled; 0013 makes it NOT NULL and runs a
        # catch-up backfill for anything written during the rollback window.
        # Without that catch-up the NOT NULL fails on a real database for a
        # reason that reads as a migration bug.
        nullable = [
            row[3]
            for row in connection.execute("PRAGMA table_info(leagues)")
            if row[1] == "tenant_id"
        ]
        assert nullable == [1], (
            "leagues.tenant_id is still nullable after `upgrade head`; "
            "revision 0013 did not land"
        )

        # The old global unique is gone, and its absence is the point: it made
        # two tenants holding the same ESPN league impossible to INSERT, which
        # is exactly the case Phase 36 existed to test.
        ddl = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='leagues'"
        ).fetchone()[0]
        assert "uq_league_tenant_season" in ddl
        assert "uq_league_season" not in ddl.replace("uq_league_tenant_season", "")

        # And the behaviour, not just the inventory: a constraint list can
        # look right while the database refuses the row.
        tenant = connection.execute("SELECT id FROM tenants LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO tenants(id, slug, created_at) VALUES (9, 'beta', '2026-01-01')"
        )
        connection.executemany(
            "INSERT INTO leagues(espn_league_id, season, lifecycle, is_public, "
            "tenant_id) VALUES (?, ?, 'active', 0, ?)",
            [("collide", 2026, tenant), ("collide", 2026, 9)],
        )
        held = connection.execute(
            "SELECT count(*) FROM leagues WHERE espn_league_id='collide'"
        ).fetchone()[0]
        assert held == 2, "two tenants cannot hold the same ESPN league and season"
    finally:
        connection.close()


def test_the_contract_step_backfills_rows_from_the_rollback_window(tmp_path):
    """Revision 0013's catch-up backfill, exercised.

    `test_upgrading_to_head_preserves_child_rows` cannot reach it: it seeds at
    0002, 0003's own backfill gives those rows a tenant, and by the time 0013
    runs there is nothing tenantless left. That test passed with the catch-up
    deleted — a probe that cannot reach the defect is not a probe.

    The catch-up exists for the window between 0003 and 0013, when the old
    application code is still running and still inserting leagues with no
    tenant. Without it the NOT NULL fails on a production database for a reason
    that reads as a migration bug. So this stops at 0012, writes the row the
    old code would have written, and then runs the contract step.
    """
    from alembic import command

    db_path = tmp_path / "window.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0012")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO leagues(espn_league_id, season, lifecycle, is_public, "
            "tenant_id) VALUES ('rollback-window', 2026, 'active', 0, NULL)"
        )
        connection.commit()
        assert (
            connection.execute(
                "SELECT count(*) FROM leagues WHERE tenant_id IS NULL"
            ).fetchone()[0]
            == 1
        ), "the rollback-window row was not written; this test proves nothing"
    finally:
        connection.close()

    command.upgrade(config, "0013")

    connection = sqlite3.connect(db_path)
    try:
        assert (
            connection.execute("SELECT count(*) FROM leagues").fetchone()[0] == 1
        ), "the rollback-window row was lost rather than backfilled"
        assert (
            connection.execute(
                "SELECT count(*) FROM leagues WHERE tenant_id IS NULL"
            ).fetchone()[0]
            == 0
        ), "the catch-up backfill did not run"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
