"""Recovery format v1 has drifted from the models. This says how, exactly.

WHY THIS FILE EXISTS
--------------------
Eighty-nine tests fail across three recovery files. None of them is caused by
Phase 41 -- measured, not assumed: `WorkerHeartbeat` was removed from
`api/models.py`, the files were re-run, and the counts were identical both
ways.

They are also not one failure wearing eighty-nine hats, which is what a first
pass at this file claimed before the causes were counted:

    70  recovery format v1 no longer matches any database this app can build
    10  <repo>/.venv/bin/python is missing or broken (ENVIRONMENTAL)
     1  tenant seeding collision in the oracle's own fixture
     8  assorted, downstream of the two above
    --
    89

Outside those three files the suite is 1155 tests and 0 failures.

Only the first group is a code problem, and it is one problem with three
independent parts. Each was measured by calling `validate_catalog` on a
database built from the current models and watching which of its four checks
refused:

1. **Eight tables the allowlist has never heard of** -- `tenants`, `users`,
   `memberships` (Phase 36), `app_sessions` (Phase 40), `jobs`, `schedules`,
   `outbox` (Phase 39), `worker_heartbeats` (Phase 41).
2. **Three tables gained a column the allowlist does not list** --
   `accounts`, `leagues` and `raw_cache` each have `tenant_id` from migrations
   0003/0004. Removing the eight unknown tables is NOT enough to pass; this is
   what the first pass at this file missed.
3. **The pinned catalog digest matches nothing buildable.** Both documented
   recipes (`create_all` over current models, and an older `opportunity_weeks`
   brought forward by the additive ALTERs) were run and neither reproduces
   `_FORMAT_V1_CATALOG_SHA256` even with every Phase 36-41 table excluded.

The ten environmental failures are the ones `docs/CLOSE-OUT.md` already
describes: `api/recovery.py` launches the backup job through
`ROOT / ".venv/bin/python"` -- deliberately the lexical venv path, so a
launchd plist survives a Python upgrade -- and in a sandbox where that venv was
never created the symlink dangles. Those tests pass on a machine with a real
repo venv and are not evidence of anything about this code.

The count 89 appears in `docs/CLOSE-OUT.md` attached to the environmental
cause alone. **That is a coincidence of arithmetic, not a shared cause**, and
it is recorded here because conflating the two would send the next person to
rebuild a venv and find 79 tests still red.

WHY IT IS NOT FIXED HERE
------------------------
Re-versioning the format means extending the allowlist with eight tables and
three columns, deciding bundled-or-excluded for each, re-pinning two digests
from real databases, and re-reading what a restore would then do to tenant
isolation. The comment above `_FORMAT_V1_CATALOG_SHA256` requires a "reviewed
recovery-format version" for exactly this, and no real restore is currently
authorised. So the drift is recorded with its size and its remedy rather than
patched to make tests green.

A smaller version was attempted and backed out: `worker_heartbeats` was added
to `EXPECTED_TABLE_COLUMNS` to clear the error. It did not clear it -- part 2
and part 3 above were still there -- and it would have asserted membership in
a frozen format nobody reviewed. Making one table's claim true while ten other
things stay false is not progress, it is a quieter failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile

import sqlalchemy as sa

import api.models  # noqa: F401 - registers every table on Base.metadata
from api.db import Base
from api.services.recovery import (
    _CREATE_ALL_OPPORTUNITY_ORDER,
    _FORMAT_V1_CATALOG_SHA256,
    EXPECTED_TABLE_COLUMNS,
    catalog_spec,
)

#: Part 1: tables the models create that recovery format v1 does not know about.
DRIFTED_TABLES = {
    "tenants": "Phase 36 - the isolation boundary",
    "users": "Phase 36 - identity",
    "memberships": "Phase 36 - the authorization join",
    "app_sessions": "Phase 40 - session tokens, hashed at rest",
    "jobs": "Phase 39 - the durable queue",
    "schedules": "Phase 39 - recurring intents",
    "outbox": "Phase 39 - pending side effects",
    "worker_heartbeats": "Phase 41 - worker liveness",
}

#: Part 2: allowlisted tables that gained a column the allowlist does not list.
DRIFTED_COLUMNS = {
    "accounts": ("tenant_id",),
    "leagues": ("tenant_id",),
    "raw_cache": ("tenant_id",),
}


def _live_catalog() -> dict:
    """The catalog of a database built the way the application builds one."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    engine = sa.create_engine(f"sqlite+pysqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    try:
        return catalog_spec(conn)
    finally:
        conn.close()
        os.unlink(path)


def test_the_metadata_is_actually_populated():
    """Guards the guard. An empty metadata makes the assertions below compare
    two empty sets, which is how this kind of test goes green while
    establishing nothing."""
    assert len(Base.metadata.tables) >= 25, (
        f"only {len(Base.metadata.tables)} tables registered; the model import "
        "is missing and every assertion below would be vacuous"
    )


def test_part_one_exactly_these_tables_are_outside_the_format():
    """Fails in both directions on purpose.

    A new table added to the models without a thought about recovery fails
    here, and so does a table removed from this list because somebody
    re-versioned the format -- at which point this file has done its job and
    should be deleted in that same change.
    """
    actual = set(Base.metadata.tables) - set(EXPECTED_TABLE_COLUMNS)
    assert actual == set(DRIFTED_TABLES), (
        "the set of tables outside recovery format v1 changed.\n"
        f"  now outside : {sorted(actual)}\n"
        f"  recorded    : {sorted(DRIFTED_TABLES)}\n"
        "If a table was added to the models, decide whether a backup should "
        "carry it and record it here. If the format was re-versioned, delete "
        "this file in the same change -- do not edit the list to match."
    )


def test_part_two_three_allowlisted_tables_gained_an_unlisted_column():
    """The part the first pass at this file missed.

    Removing the eight unknown tables from a live catalog is not enough to
    pass `validate_catalog`: `accounts`, `leagues` and `raw_cache` each carry a
    `tenant_id` the allowlist's column tuple does not mention. Asserted
    against the live catalog rather than against the models, because the
    allowlist is compared to a real database's PRAGMA output and column ORDER
    is part of what it checks.
    """
    catalog = _live_catalog()
    found = {}
    for table, expected in EXPECTED_TABLE_COLUMNS.items():
        if table not in catalog:
            continue
        actual = tuple(col["name"] for col in catalog[table]["columns"])
        allowed = {expected}
        if table == "opportunity_weeks":
            allowed.add(_CREATE_ALL_OPPORTUNITY_ORDER)
        if actual not in allowed:
            extra = tuple(c for c in actual if c not in expected)
            if extra:
                found[table] = extra
    assert found == DRIFTED_COLUMNS, (
        f"the column drift changed.\n  now : {found}\n  recorded : "
        f"{DRIFTED_COLUMNS}"
    )


def test_part_three_the_pinned_digest_matches_nothing_buildable():
    """The pin is dead weight until it is re-pinned from a real database.

    Both digests in `_FORMAT_V1_CATALOG_SHA256` describe the Phase-31 schema.
    Nothing this application can create hashes to either of them, and the
    assertion is that nothing does -- the moment one does, the format has been
    re-versioned and this file should go.

    Deliberately NOT asserting the current digest's value. Pinning it here
    would recreate the same stale-fingerprint problem one directory over, and
    this file's job is to describe drift rather than to pin a second format.
    """
    catalog = _live_catalog()
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() not in _FORMAT_V1_CATALOG_SHA256

    # ...and still not, with every drifted table removed, which is what makes
    # this a third independent part rather than a consequence of part one.
    for table in DRIFTED_TABLES:
        catalog.pop(table, None)
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() not in _FORMAT_V1_CATALOG_SHA256


def test_every_drifted_table_says_which_phase_brought_it():
    """A bare list of names ages into a mystery. The phase is how the next
    person finds the decision that created the table."""
    for table, note in DRIFTED_TABLES.items():
        assert "Phase" in note and len(note) > 15, (table, note)


def test_no_drifted_table_is_also_allowlisted():
    """The two sets are complements by construction, and this says so.

    It is also the assertion that would have caught the backed-out change
    described in the module docstring: adding `worker_heartbeats` to the
    allowlist while leaving it here is a contradiction, and a contradiction is
    how both lists end up believed.
    """
    assert not (set(DRIFTED_TABLES) & set(EXPECTED_TABLE_COLUMNS))


def test_the_environmental_failures_are_not_mixed_in_here():
    """Ten of the eighty-nine are a missing `<repo>/.venv/bin/python`.

    `api/recovery.py` launches the backup job through the LEXICAL venv path on
    purpose, so a launchd plist survives a Python upgrade. In a sandbox where
    that venv was never created the symlink dangles and those tests fail for a
    reason that has nothing to do with this code.

    Asserted as a structural fact -- that the path is lexical and is what the
    module uses -- rather than by checking whether the file exists, which would
    make this test's result depend on the machine it runs on.
    """
    from api import recovery

    lexical = recovery.ROOT / ".venv/bin/python"
    assert str(lexical).endswith(".venv/bin/python")
    assert lexical.is_absolute()
