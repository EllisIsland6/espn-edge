"""Recovery format v2: the guard that would have caught v1's drift.

WHAT WENT WRONG WITH v1, AND WHAT THAT ASKS OF THIS FILE
--------------------------------------------------------
Format v1 froze the whole SQLite catalog and pinned its digest -- a good
control. Then Phases 36-41 added eight tables and a `tenant_id` to three
existing ones, nobody re-versioned the format, and it came to match **no
database the application could build**. Every backup and every restore
refused, 70 tests went red, and the red was opaque: one error code, repeated.

Nothing in the suite noticed, because nothing compared the pin to a database.
So the central test here is
`test_every_documented_shape_hashes_to_a_pinned_digest`: it builds each shape
the application can produce and requires its digest to be pinned, and it
requires the reverse too -- a pinned digest no shape produces is a stale pin,
which is exactly what v1 became.

The second lesson was subtler. The suite builds databases with `create_all`
and the operator runs `alembic upgrade head`, and the two disagree about
`leagues`' column order, so a single-order allowlist passes one and refuses the
other -- silently, in whichever direction nobody was testing. Under v1 the
migrated shape could not validate at all, because `catalog_spec` included
alembic's own `alembic_version` table and the allowlist never listed it. Both
are pinned below, from real databases built both ways.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest
import sqlalchemy as sa

import api.models  # noqa: F401 - registers every table on Base.metadata
from api.db import Base
from api.services import recovery
from api.services.recovery import (
    _CREATE_ALL_LEAGUES_ORDER,
    _CREATE_ALL_OPPORTUNITY_ORDER,
    _FORMAT_V1_CATALOG_SHA256,
    _FORMAT_V2_CATALOG_SHA256,
    EXCLUDED_COLUMNS,
    EXPECTED_TABLE_COLUMNS,
    NOT_BUNDLED,
    catalog_spec,
    validate_catalog,
)

#: The two columns alembic adds to `opportunity_weeks` on an older local file.
ADDITIVE = ("receiving_tds", "team_passing_yards")

ROOT = os.getcwd()


def _digest(path: str) -> str:
    conn = sqlite3.connect(path)
    try:
        catalog = catalog_spec(conn)
    finally:
        conn.close()
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _build_create_all(*, older_opportunity: bool) -> str:
    """A fresh `create_all` database, in a subprocess.

    A subprocess because the `older_opportunity` variant removes two columns
    from `Base.metadata`, and doing that in-process would leak into every test
    that runs afterwards.
    """
    script = f"""
import os, sys
os.environ.setdefault("APP_MODE", "private_operator")
sys.path.insert(0, {ROOT!r})
import sqlalchemy as sa, api.models
from api.db import Base
if {older_opportunity!r}:
    ow = Base.metadata.tables["opportunity_weeks"]
    for name in {ADDITIVE!r}:
        ow._columns.remove(ow.columns[name])
path = sys.argv[1]
engine = sa.create_engine("sqlite+pysqlite:///" + path, future=True)
Base.metadata.create_all(engine)
if {older_opportunity!r}:
    with engine.begin() as conn:
        for name in {ADDITIVE!r}:
            conn.execute(sa.text("ALTER TABLE opportunity_weeks ADD COLUMN " + name + " FLOAT"))
engine.dispose()
"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    subprocess.run([sys.executable, "-c", script, path], check=True, timeout=180)
    return path


def _build_migrated() -> str:
    """What `alembic upgrade head` actually produces.

    The shape an operator's database has, and the one v1 could not validate at
    all. Built by running the real migration chain, because deriving it from
    the models is how the column-order disagreement went unnoticed.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{path}",
            "APP_MODE": "private_operator",
            "SEASON": "2026",
            "ANTHROPIC_API_KEY": "",
            "RECOVERY_REQUIRED": "false",
            "TELEMETRY_ENABLED": "false",
            "TELEMETRY_REPORT_PATH": f"{path}.report.md",
            "ESPN_API_HOST": "https://127.0.0.1:9",
        }
    )
    env.setdefault(
        "FERNET_KEY",
        subprocess.run(
            [sys.executable, "-c",
             "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"],
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True, capture_output=True, env=env, cwd=ROOT, timeout=180,
    )
    return path


SHAPES = {
    "create_all": lambda: _build_create_all(older_opportunity=False),
    "create_all with an older opportunity_weeks": lambda: _build_create_all(
        older_opportunity=True
    ),
    "alembic upgrade head": _build_migrated,
}


@pytest.fixture(scope="module")
def shape_digests() -> dict[str, str]:
    out = {}
    for name, build in SHAPES.items():
        path = build()
        try:
            out[name] = _digest(path)
        finally:
            os.unlink(path)
    return out


# --------------------------------------------------------------------------
# The pin, against real databases
# --------------------------------------------------------------------------


def test_every_documented_shape_hashes_to_a_pinned_digest(shape_digests):
    """The test that would have caught the whole v1 drift.

    Each shape is built, hashed, and required to be in the pin. Nothing here
    is derived from the models -- the migrated shape comes from running the
    real migration chain, because deriving it is how the `leagues` column-order
    disagreement stayed invisible.
    """
    unpinned = {
        name: digest
        for name, digest in shape_digests.items()
        if digest not in _FORMAT_V2_CATALOG_SHA256
    }
    assert unpinned == {}, (
        "these schema shapes are buildable and not pinned, so a backup taken "
        f"from one of them will refuse: {unpinned}. Re-run "
        "`.venv/phase41/digests.py` and re-pin as a reviewed format version."
    )


def test_no_pinned_digest_is_stale(shape_digests):
    """The reverse, and the half v1 failed.

    A pin no shape produces is dead weight that reads as a working control. v1
    had two such entries for an entire sprint.
    """
    orphans = set(_FORMAT_V2_CATALOG_SHA256) - set(shape_digests.values())
    assert orphans == set(), (
        f"pinned digests that nothing buildable produces: {sorted(orphans)}"
    )


def test_each_shape_is_distinct(shape_digests):
    """Three shapes, three digests.

    If two collapsed, one of the three recipes would not be exercising what it
    claims, and the pin would be smaller than it looks.
    """
    assert len(set(shape_digests.values())) == len(SHAPES), shape_digests


def test_every_shape_validates(shape_digests):
    """End to end: `validate_catalog` accepts each one.

    The digest assertions above pass if the digests merely agree with the pin;
    this one requires the other three checks to pass too.
    """
    for name, build in SHAPES.items():
        path = build()
        try:
            conn = sqlite3.connect(path)
            try:
                catalog = catalog_spec(conn)
            finally:
                conn.close()
            assert validate_catalog(catalog) == shape_digests[name], name
        finally:
            os.unlink(path)


def test_the_v1_pin_is_dead_and_unused(shape_digests):
    """`_FORMAT_V1_CATALOG_SHA256` is kept only as a record.

    Nothing buildable hashes to it and nothing in the recovery path reads it.
    Asserted so a future edit cannot quietly point `validate_catalog` back at
    a fingerprint that matches no database.
    """
    assert not (set(_FORMAT_V1_CATALOG_SHA256) & set(shape_digests.values()))
    assert not (_FORMAT_V1_CATALOG_SHA256 & _FORMAT_V2_CATALOG_SHA256)

    from api.services import recovery

    source = (recovery.__file__).replace(".pyc", ".py")
    body = open(source, encoding="utf-8").read()
    reads = body.count("_FORMAT_V1_CATALOG_SHA256")
    assert reads == 1, (
        f"_FORMAT_V1_CATALOG_SHA256 appears {reads} times in recovery.py; it "
        "should be defined once and read nowhere"
    )


def test_the_version_marker_says_two_everywhere_it_appears():
    """The number in every bundle, state file and canary must be the version
    the catalog actually is.

    Asserted across all three markers rather than one, because they are three
    separate literals and the failure mode is updating some of them: a bundle
    stamped v1 whose catalog is v2 refuses for the right reason and reports the
    wrong one, which is the sort of error message that costs an afternoon.
    """
    from api.services import recovery

    assert recovery.FORMAT_VERSION == 2
    assert recovery.BUNDLE_FILENAME.endswith("-v2.json")
    assert recovery.RECOVERY_CANARY.endswith("-format-v2")


def test_only_the_three_format_markers_moved_to_v2():
    """What did NOT change, and why, stated as an assertion.

    Three `-v1` strings survive on purpose and this test names them, because
    the first version of it said "no v1 marker survives anywhere" and failed on
    all three -- a claim true of the format markers and asserted of the class,
    which is the pattern this repository has recorded thirty-odd times.

    `RECOVERY_TAG` is the Restic snapshot tag: changing it orphans every
    snapshot already in the repository, which is the opposite of what a
    recovery format change should do. The two scratch sentinels name directory
    roots a previous run may still own, and a run that cannot recognise its own
    scratch root cannot clean it up.
    """
    from api.services import recovery

    deliberate = {
        "RECOVERY_TAG": "espn-edge-private-v1",
        "_SCRATCH_ROOT_MARKER": ".espn-edge-recovery-root-v1",
        "_SCRATCH_RUN_MARKER": ".espn-edge-recovery-run-v1",
    }
    for name, value in deliberate.items():
        assert getattr(recovery, name) == value, name

    body = open(recovery.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    allowed = set(deliberate.values()) | {"_FORMAT_V1_CATALOG_SHA256"}
    offenders = [
        line.strip()
        for line in body.splitlines()
        if "-v1" in line and not any(token in line for token in allowed)
    ]
    assert offenders == [], offenders


# --------------------------------------------------------------------------
# The allowlist covers the models
# --------------------------------------------------------------------------


def test_the_metadata_is_actually_populated():
    """Guards the guard: an empty metadata makes the next test compare two
    empty sets and pass while checking nothing."""
    assert len(Base.metadata.tables) >= 25, len(Base.metadata.tables)


def test_every_table_the_models_create_is_allowlisted():
    """The guard that failed to exist for six phases.

    A table added to the models without a decision about recovery fails here,
    which is the whole point: under v1 it failed silently, in sixty-four tests
    reporting one opaque error code.
    """
    missing = sorted(set(Base.metadata.tables) - set(EXPECTED_TABLE_COLUMNS))
    assert missing == [], (
        f"tables the models create and recovery format v2 does not know: "
        f"{missing}. Add each to EXPECTED_TABLE_COLUMNS, decide whether it "
        "belongs in NOT_BUNDLED, and re-pin the digests."
    )


def test_the_allowlist_names_no_table_that_does_not_exist():
    extra = sorted(set(EXPECTED_TABLE_COLUMNS) - set(Base.metadata.tables))
    assert extra == [], extra


def test_alembic_version_is_not_part_of_the_catalog():
    """It exists only in a MIGRATED database, so including it made the
    fingerprint depend on provenance rather than shape -- and since the
    allowlist never listed it, v1 refused every database produced by
    `alembic upgrade head`."""
    path = _build_migrated()
    try:
        conn = sqlite3.connect(path)
        try:
            present = [
                name
                for (name,) in conn.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            ]
            catalog = catalog_spec(conn)
        finally:
            conn.close()
        # It really is in the database...
        assert "alembic_version" in present
        # ...and really is not in the catalog.
        assert "alembic_version" not in catalog
    finally:
        os.unlink(path)


def test_leagues_is_allowed_in_both_column_orders():
    """The disagreement that a single-order allowlist would hide.

    `tenant_id` is appended in a migrated database and fifth in a `create_all`
    one. Both orders are real: the suite builds one, the operator runs the
    other. Asserted as a property of the two tuples, and then end to end by
    `test_every_shape_validates`.
    """
    migrated = EXPECTED_TABLE_COLUMNS["leagues"]
    fresh = _CREATE_ALL_LEAGUES_ORDER
    assert migrated != fresh, "the two orders are supposed to differ"
    assert set(migrated) == set(fresh), "they must hold the same columns"
    assert migrated[-1] == "tenant_id"
    assert fresh.index("tenant_id") == 4


def test_opportunity_weeks_keeps_its_own_dual_order():
    """Unchanged from v1, and asserted so the leagues work above did not
    disturb it."""
    assert EXPECTED_TABLE_COLUMNS["opportunity_weeks"] != _CREATE_ALL_OPPORTUNITY_ORDER
    assert set(EXPECTED_TABLE_COLUMNS["opportunity_weeks"]) == set(
        _CREATE_ALL_OPPORTUNITY_ORDER
    )


# --------------------------------------------------------------------------
# What travels, and what must not
# --------------------------------------------------------------------------


def test_not_bundled_is_exactly_the_intended_set():
    """Changing what a backup carries should be a deliberate edit here.

    `schedules` and `tenants` are deliberately ABSENT -- they ARE bundled.
    Schedules are configuration the operator created, and dropping them would
    mean nothing ever syncs again after a restore with nothing saying so;
    `tenants` must travel because three tables reference it.
    """
    assert NOT_BUNDLED == frozenset(
        {
            "raw_cache",
            "users",
            "memberships",
            "app_sessions",
            "jobs",
            "outbox",
            "worker_heartbeats",
        }
    )
    assert "schedules" not in NOT_BUNDLED
    assert "tenants" not in NOT_BUNDLED


def test_raw_cache_never_travels():
    """Private ESPN payloads. True in v1 and must stay true."""
    assert "raw_cache" in NOT_BUNDLED


def test_no_member_identifier_travels():
    """`users.email` is unique, so the single-sentinel substitution `accounts`
    uses for credentials would violate the constraint on the second row, and a
    per-row stand-in would be fabricating identity. So the table does not
    travel at all -- and `teams.owner_swids_json` stays excluded by column."""
    assert "users" in NOT_BUNDLED
    assert "owner_swids_json" in EXCLUDED_COLUMNS["teams"]


def test_every_bundled_table_has_its_foreign_keys_bundled_too():
    """The invariant that decided `memberships`.

    A bundled table whose foreign key points at an unbundled one writes
    dangling references into a database whose own verification runs
    `PRAGMA foreign_key_check`. `memberships` carries no identifier itself,
    which is why excluding it is not obvious until this is stated: every row
    points at a `users` row that is not in the bundle.
    """
    broken = {}
    for table in Base.metadata.sorted_tables:
        if table.name in NOT_BUNDLED:
            continue
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if target in NOT_BUNDLED:
                broken.setdefault(table.name, []).append(target)
    assert broken == {}, (
        f"bundled tables referencing unbundled ones: {broken}. Either bundle "
        "the target or stop bundling the source -- a restore cannot write a "
        "dangling reference past `PRAGMA foreign_key_check`."
    )


def test_tenants_is_bundled_because_things_point_at_it():
    """Stated as the measurement rather than as a claim: which bundled tables
    actually reference it."""
    referrers = sorted(
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in NOT_BUNDLED
        and any(fk.column.table.name == "tenants" for fk in table.foreign_keys)
    )
    assert referrers, "nothing references tenants; the reason for bundling it changed"
    assert "tenants" not in NOT_BUNDLED


def test_the_credential_columns_are_still_substituted():
    """v2 changed what tables travel and must not have changed what happens to
    a credential that does."""
    assert EXCLUDED_COLUMNS["accounts"] == {"swid", "espn_s2_encrypted", "status"}


def test_a_restore_refuses_a_target_that_is_not_empty(tmp_path):
    """`create_all` seeds the default tenant, so a restore that did not start
    from empty collided on `tenants.id` -- which is what 24 integration tests
    reported once the allowlist let them get that far. The refusal protects the
    clearing step below it: that step can only ever remove rows the schema
    seeded, because a target holding anything else is rejected first.

    The bundle has to be **valid**. The first version of this test passed
    `b"{}"` and asserted "either error code", and it passed with the refusal
    deleted -- because the bundle is parsed before the target is looked at, so
    execution never reached the line under test. A test that cannot reach its
    subject is not a weak test, it is a different test.
    """
    from api.services.recovery import (
        RecoveryError,
        build_logical_bundle,
        restore_bundle_to_scratch,
    )

    source = tmp_path / "source.db"
    engine = sa.create_engine(f"sqlite:///{source}")
    Base.metadata.create_all(engine)
    engine.dispose()
    bundle, _manifest = build_logical_bundle(source)

    # A target that already holds a row nobody put there on purpose.
    target = tmp_path / "not-empty.db"
    engine = sa.create_engine(f"sqlite:///{target}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO players (espn_player_id, name) VALUES (1, 'x')")
        )
    engine.dispose()

    with pytest.raises(RecoveryError) as exc:
        restore_bundle_to_scratch(bundle, target)
    assert exc.value.code == "recovery_target_unavailable", exc.value.code

    # ...and the same bundle into an empty target does not raise, so the
    # refusal above is about the target rather than about the bundle.
    clean = tmp_path / "clean.db"
    restore_bundle_to_scratch(bundle, clean)


# --------------------------------------------------------------------------
# The restore verifier's environment
# --------------------------------------------------------------------------


def test_the_restore_verifier_supplies_every_required_setting(tmp_path):
    """The guard for a defect that only a clean clone could show.

    `_run_operational_restore_verifier` spawns `python -m api.recovery
    internal-verify` with a hand-built environment, and three of `Settings`'
    required fields were missing from it. It worked anyway, because `Settings`
    is configured with `env_file=str(ROOT / ".env")` -- an absolute path -- so
    the hand-built environment never isolated the subprocess from the
    operator's gitignored file. On a machine without that file the subprocess
    printed a traceback instead of JSON and the caller reported "Restored
    application verification failed": a missing setting presented as data loss,
    during a restore.

    Asserted over `Settings.model_fields` rather than against a list of three
    names, so a required setting added later fails here instead of failing on
    somebody else's machine.
    """
    import inspect

    from api.config import Settings
    from api.services import recovery

    required = {
        (field.alias or name).upper()
        for name, field in Settings.model_fields.items()
        if field.is_required()
    }
    assert required, "no required settings found; this test would be vacuous"

    source = inspect.getsource(recovery._run_operational_restore_verifier)
    missing = sorted(name for name in required if f'"{name}":' not in source)
    assert missing == [], (
        f"the restore verifier's environment does not set {missing}, so it "
        "depends on a .env file that a CI runner or a fresh clone does not have"
    )


def test_the_verifier_runs_in_private_operator_mode():
    """Not merely present -- correct.

    Inheriting `APP_MODE` from an untracked file meant the verifier could have
    run in hosted mode, which is synthetic-only, against real restored data.
    """
    import inspect

    from api.services import recovery

    source = inspect.getsource(recovery._run_operational_restore_verifier)
    assert '"APP_MODE": "private_operator"' in source
    assert '"TELEMETRY_ENABLED": "false"' in source


def test_a_verifier_that_cannot_start_is_a_different_error_from_one_that_says_no():
    """Conflating them is how a missing setting reads as a corrupted restore.

    Both raise `recovery_bundle_invalid` -- the code is part of the safe-error
    vocabulary and not worth expanding -- but the messages differ, and the
    message is what an operator reads at two in the morning.
    """
    import inspect

    from api.services import recovery

    source = inspect.getsource(recovery._run_operational_restore_verifier)
    assert "Restore verifier did not run." in source
    assert "Restored application verification failed." in source


# --------------------------------------------------------------------------
# The fingerprint must not depend on constraint-clause order
# --------------------------------------------------------------------------
#
# Measured: two checkouts of the identical commit produced `leagues` DDL that
# differed only in whether the FK clause came before or after the UNIQUE one.
# Same constraints, same semantics, different SHA-256 -- so the pin computed in
# one tree refused a database built in the other, and the clean clone is what
# caught it. `_canonical_table_sql` sorts the constraint clauses. These tests
# hold that fix in place from BOTH sides: reordering must not change the
# reading, and a real change must still change it. A canonicaliser that threw
# the clauses away would satisfy the first half alone.

_CLAUSE_ORDER_FK_FIRST = """
CREATE TABLE clause_order (
    id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_clause_order_tenant_id FOREIGN KEY(tenant_id) REFERENCES tenants (id),
    CONSTRAINT uq_clause_order_season UNIQUE (tenant_id, season)
)
"""

_CLAUSE_ORDER_UNIQUE_FIRST = """
CREATE TABLE clause_order (
    id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_clause_order_season UNIQUE (tenant_id, season),
    CONSTRAINT fk_clause_order_tenant_id FOREIGN KEY(tenant_id) REFERENCES tenants (id)
)
"""

#: One column swapped inside the UNIQUE clause. A real schema difference.
_CLAUSE_ORDER_DIFFERENT_UNIQUE = _CLAUSE_ORDER_FK_FIRST.replace(
    "UNIQUE (tenant_id, season)", "UNIQUE (tenant_id, id)"
)

#: The same constraints, but two columns swapped. Column order is part of the
#: schema and must still read differently.
_CLAUSE_ORDER_DIFFERENT_COLUMNS = _CLAUSE_ORDER_FK_FIRST.replace(
    "    tenant_id INTEGER NOT NULL,\n    season INTEGER NOT NULL,",
    "    season INTEGER NOT NULL,\n    tenant_id INTEGER NOT NULL,",
)


def _reading_of(ddl: str) -> str:
    """What the catalog records for a table created by this exact DDL.

    Through a real SQLite database, not by calling the canonicaliser on a
    string: the catalog hashes what SQLite stores, and SQLite is free to
    rewrite what it was handed.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(ddl)
        return json.dumps(
            recovery._table_catalog(conn, "clause_order"),
            sort_keys=True,
            separators=(",", ":"),
        )
    finally:
        conn.close()


def test_the_two_clause_orders_really_are_different_text():
    """The instrument check. If SQLite normalised the DDL on the way in, the
    next test would be comparing a string to itself and would pass with the
    canonicaliser deleted."""
    stored = []
    for ddl in (_CLAUSE_ORDER_FK_FIRST, _CLAUSE_ORDER_UNIQUE_FIRST):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(ddl)
            (sql,) = conn.execute(
                "SELECT sql FROM sqlite_schema WHERE name='clause_order'"
            ).fetchone()
        finally:
            conn.close()
        stored.append(sql)
    assert stored[0] != stored[1]
    # And they differ ONLY in that ordering: same clauses, so the un-sorted
    # normalisation differs while the sorted character multiset does not.
    assert recovery._normalize_sql(stored[0]) != recovery._normalize_sql(stored[1])
    assert sorted(recovery._normalize_sql(stored[0])) == sorted(recovery._normalize_sql(stored[1]))


def test_constraint_clause_order_does_not_change_the_reading():
    """The defect, as a test. This is what differed between two checkouts."""
    assert _reading_of(_CLAUSE_ORDER_FK_FIRST) == _reading_of(_CLAUSE_ORDER_UNIQUE_FIRST)


def test_a_changed_constraint_still_changes_the_reading():
    """The other direction: sorting the clauses must not amount to ignoring
    them. One column swapped inside the UNIQUE clause is a real difference and
    must read differently."""
    assert _reading_of(_CLAUSE_ORDER_FK_FIRST) != _reading_of(_CLAUSE_ORDER_DIFFERENT_UNIQUE)


def test_a_changed_column_order_still_changes_the_reading():
    """Column order is deliberately not sorted -- `leagues` has two legitimate
    orders and the allowlist is what decides which are acceptable, so the
    fingerprint has to be able to see the difference."""
    assert _reading_of(_CLAUSE_ORDER_FK_FIRST) != _reading_of(_CLAUSE_ORDER_DIFFERENT_COLUMNS)


def test_the_canonical_sql_lists_every_clause_it_was_given():
    """Nothing is dropped on the way through. Counted, because a split on
    top-level commas is exactly the kind of parser that silently eats a clause
    containing a parenthesised column list."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(_CLAUSE_ORDER_FK_FIRST)
        (sql,) = conn.execute("SELECT sql FROM sqlite_schema WHERE name='clause_order'").fetchone()
    finally:
        conn.close()
    canonical = recovery._canonical_table_sql(sql)
    for fragment in (
        "id integer not null",
        "tenant_id integer not null",
        "season integer not null",
        "primary key (id)",
        "constraint fk_clause_order_tenant_id foreign key(tenant_id)",
        "constraint uq_clause_order_season unique (tenant_id, season)",
    ):
        assert fragment in canonical, fragment
    # Three column definitions and three constraint clauses, so five top-level
    # commas and not one more.
    assert canonical.count(",") == 5 + 1, canonical  # +1 inside the UNIQUE list


def test_an_unparseable_table_sql_is_passed_through_unsorted():
    """A DDL with no parenthesised body is not something to guess at: it keeps
    the reading the un-canonicalised path gave it."""
    assert recovery._canonical_table_sql(None) == ""
    assert recovery._canonical_table_sql("") == ""
    assert recovery._canonical_table_sql("CREATE TABLE t AS SELECT 1") == (
        "create table t as select 1"
    )
