"""The recovery format has drifted from the models, and this says by how much.

WHY THIS FILE EXISTS
--------------------
Eighty-nine tests fail, in three files, and every one of them predates Phase
41 -- measured, not assumed: the `worker_heartbeats` table was removed from
`api/models.py`, the files were re-run, and the counts were identical both
ways.

    tests/test_recovery.py              64   recovery_schema_drift
    tests/test_recovery_integration.py  24   recovery_schema_drift
    tests/test_recovery_oracle.py        1   UNIQUE constraint failed: tenants.id
                                        --
                                        89

Outside those three files the suite is 1370 tests and 0 failures.

The cause is structural. Recovery format v1 freezes the whole SQLite catalog:
`EXPECTED_TABLE_COLUMNS` lists the tables, `_FORMAT_V1_CATALOG_SHA256` pins the
exact catalog digest, and `validate_catalog` refuses anything else. That is a
good control -- it is why "restore from backup" cannot silently restore into a
schema nobody reviewed. But Phases 36 through 40 added seven tables to the
models and the frozen format was never re-versioned, so the format now matches
no database this application can create.

Sixty-four tests reporting the same opaque error say "something is wrong"
without saying what. This file says what, in one assertion, and will fail the
day somebody fixes it -- at which point the fix is to delete this file in the
same change that re-versions the format.

A SECOND PRE-EXISTING FAILURE, SAME FAMILY
------------------------------------------
`tests/test_recovery_oracle.py::test_independent_oracle_covers_every_retained_value_and_detects_substitution`
fails with `sqlite3.IntegrityError: UNIQUE constraint failed: tenants.id`.
Measured the same way -- removed `WorkerHeartbeat` from `api/models.py`, re-ran
that one file, identical failure -- so it is Phase 36-40 fallout rather than
Phase 41's. The oracle builds its own database and the tenant seeding collides
with it. Recorded here beside the schema drift because both are the same
underlying thing: the recovery code was written against a schema that no
longer exists, and it will be re-read as one piece when the format is
re-versioned.

WHY IT IS NOT FIXED HERE
------------------------
Re-versioning the recovery format means extending the allowlist with seven
tables, deciding for each whether it is bundled or excluded, re-pinning two
catalog digests by reproducing them from real databases, and re-reading what a
restore would then do to tenant isolation. The comment above
`_FORMAT_V1_CATALOG_SHA256` requires a "reviewed recovery-format version" for
exactly this, and no real restore is currently authorised. So the drift is
recorded with its size and its remedy rather than patched to make tests green.

A smaller version of the mistake was attempted and backed out during Phase 41:
`worker_heartbeats` was added to `EXPECTED_TABLE_COLUMNS` to clear the error.
It did not clear it -- the seven older tables were still missing -- and it
would have asserted membership in a frozen format that nobody reviewed. Making
one table's claim true while six remain false is not progress, it is a quieter
failure.
"""

from __future__ import annotations

import api.models  # noqa: F401 - registers every table on Base.metadata
from api.db import Base
from api.services.recovery import EXPECTED_TABLE_COLUMNS

#: Tables the models create that recovery format v1 does not know about.
#:
#: Each arrived with the phase named beside it. None is in the frozen catalog,
#: so a backup taken from a current database cannot validate and a restore
#: cannot be attempted -- which is the correct fail-closed behaviour of a
#: control that has not been updated, not a bug in the control.
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


def test_the_metadata_is_actually_populated():
    """Guards the guard. An empty metadata makes the assertion below pass by
    comparing two empty sets, which is how this kind of test goes green while
    establishing nothing."""
    assert len(Base.metadata.tables) >= 25, (
        f"only {len(Base.metadata.tables)} tables registered; the model import "
        "is missing and the drift assertion below would be vacuous"
    )


def test_the_recovery_format_drift_is_named_and_not_forgotten():
    """Exactly these tables are outside recovery format v1.

    Fails in both directions on purpose. A new table added to the models
    without a thought about recovery fails here, and so does a table removed
    from the drift list because somebody re-versioned the format -- at which
    point this file has done its job and should be deleted in that same change.
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


def test_every_drifted_table_says_which_phase_brought_it():
    """A bare list of names ages into a mystery. The phase is how the next
    person finds the decision that created the table."""
    for table, note in DRIFTED_TABLES.items():
        assert "Phase" in note and len(note) > 15, (table, note)


def test_no_drifted_table_is_also_allowlisted():
    """The two sets are complements by construction, and this says so.

    It is also the assertion that caught the backed-out change described in
    the module docstring: adding `worker_heartbeats` to the allowlist while
    leaving it in the drift list would be a contradiction, and a contradiction
    is how both lists end up believed.
    """
    assert not (set(DRIFTED_TABLES) & set(EXPECTED_TABLE_COLUMNS))
