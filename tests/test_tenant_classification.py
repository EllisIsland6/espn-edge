"""Every table must be classified tenant-scoped or global, deliberately.

Phase 36's attack suite passed 13/13 and still missed three tenant-scoped tables
with no policy — `accounts`, `raw_cache` and the spend ledger — because an attack
suite can only probe tables the policies already cover. The failure mode was not
a bad policy, it was **a table nobody classified**.

So this is the check that fails the build. It cannot verify PostgreSQL row-level
security (the offline suite runs SQLite, which has none), and it does not pretend
to: what it enforces is that no table can be ADDED without someone deciding which
side of the tenant boundary it is on. That is the half that is checkable offline
and it is the half that actually failed.
"""

from __future__ import annotations

import pytest

# Importing the models is what registers them on `Base.metadata`. Without this the
# metadata is EMPTY, and `test_every_table_is_classified` passes vacuously against
# a set of zero tables — a green tick establishing nothing, which is the failure
# this whole file exists to stop.
import api.models  # noqa: F401  - registers every table on Base.metadata
from api.db import Base

#: Public NFL and market reference data. No tenant dimension, and none needed —
#: every tenant reads the same rows, and that is the intended behaviour.
GLOBAL_TABLES = frozenset({
    "players",
    "nflverse_player_maps",
    "adp_snapshots",
    "opportunity_weeks",
    "opportunity_imports",
    # The kernel's own tables, and they are global by construction rather than
    # by oversight. `tenants` IS the tenant list -- scoping it to a tenant is
    # circular. `users` is global because one person may belong to several
    # tenants; it is `memberships`, the join, that carries the authorization
    # fact, and a membership is reachable only through the tenant it names.
    # None of the three is readable by an unauthenticated caller; "global"
    # here means "has no tenant_id column", not "public".
    "tenants",
    "users",
    "memberships",
    # Read BEFORE a tenant is known, by the code working out which tenant to
    # use, so a tenant predicate on it would be circular. It is protected by
    # the token being 256 bits of randomness and stored only as a SHA-256
    # hash, not by a policy. "Global" here means "has no tenant_id column",
    # and emphatically not "readable by anyone".
    "app_sessions",
})

#: Tenant-scoped: the rows belong to one tenant and must never cross. Reaching
#: the tenant is a separate question from being scoped by it — `accounts` has no
#: league at all, `current_roster_entries` reaches one only through its snapshot —
#: so the path is recorded beside the name rather than assumed to be `league_id`.
TENANT_TABLES = {
    # Landed by alembic 0003. The column exists, every existing row was
    # backfilled to one tenant, and the unique is tenant-scoped.
    #
    # Read the column, not the promise: `tenant_id` is still NULLABLE, because
    # the contract step that makes it NOT NULL is parked in alembic/pending/
    # until all 37 League writers supply a tenant (Phase 37). So a league with
    # no tenant is still constructible today. That is the expand window, and
    # `test_the_leagues_tenant_column_is_still_nullable` below pins it so the
    # window closing is a deliberate act rather than a surprise.
    "leagues": "tenant_id",
    "accounts": "NOT YET SCOPED - holds swid + encrypted espn_s2",
    "raw_cache": "NOT YET SCOPED - raw ESPN payloads for private leagues",
    "ai_spend_months": "NOT YET SCOPED - global ceiling is shared across tenants",
    "ai_spend_entries": "NOT YET SCOPED - global ceiling is shared across tenants",
    # Durable work. Scoped by its own tenant_id, and deliberately WITHOUT an
    # RLS policy: one worker process serves every tenant, so a policy on the
    # claim would have to be bypassed to work at all. Enforcement for jobs is
    # the tenant the worker binds before it RUNS one, not a predicate on the
    # claim -- recorded here so "no policy" reads as a decision.
    "jobs": "tenant_id",
    # Recurring intents. Same reasoning as `jobs`: scoped by its own
    # tenant_id, and no RLS policy because one scheduler process materialises
    # for every tenant, so a policy on the scan would have to be bypassed to
    # function. Recorded so "no policy" reads as a decision.
    "schedules": "tenant_id",
    "teams": "league_id",
    "draft_picks": "league_id",
    "metrics": "league_id",
    "matchups": "league_id",
    "transactions": "league_id",
    "lineup_slots": "league_id",
    "current_roster_snapshots": "league_id",
    "current_roster_entries": "snapshot_id -> current_roster_snapshots.league_id",
    "metric_snapshots": "league_id",
    "ai_reports": "league_id",
}


def test_the_metadata_is_actually_populated():
    """Guards the guard: every assertion below is over `Base.metadata.tables`, and
    an empty metadata makes all of them pass while checking nothing."""
    assert len(Base.metadata.tables) >= 20, (
        f"only {len(Base.metadata.tables)} tables registered; the model import is missing"
    )


def test_every_table_is_classified():
    """A new table breaks this until someone decides which side it is on.

    This is the whole point. `accounts` and `raw_cache` sat unclassified and
    unprotected through an entire phase whose acceptance was thirteen passing
    isolation attacks.
    """
    known = set(GLOBAL_TABLES) | set(TENANT_TABLES)
    actual = set(Base.metadata.tables)
    unclassified = actual - known
    assert not unclassified, (
        f"unclassified tables: {sorted(unclassified)}. Add each to GLOBAL_TABLES "
        f"(public reference data) or TENANT_TABLES (rows belonging to one tenant, "
        f"with the path that reaches the tenant)."
    )


def test_the_classification_names_no_table_that_does_not_exist():
    """The other direction: a stale entry is a classification of nothing."""
    known = set(GLOBAL_TABLES) | set(TENANT_TABLES)
    missing = known - set(Base.metadata.tables)
    assert not missing, f"classified tables that no longer exist: {sorted(missing)}"


def test_the_two_classifications_are_disjoint():
    overlap = set(GLOBAL_TABLES) & set(TENANT_TABLES)
    assert not overlap, f"a table cannot be both: {sorted(overlap)}"


@pytest.mark.parametrize(
    "table",
    sorted(t for t, path in TENANT_TABLES.items() if not path.startswith("NOT YET")),
)
def test_each_scoped_tenant_table_has_the_column_its_path_names(table):
    """The recorded path must exist. Phase 36 generated a policy over `league_id`
    for `current_roster_entries`, which has no such column — the CREATE POLICY
    failed and the error was hidden by a `grep -v` filtering routine output."""
    columns = {c.name for c in Base.metadata.tables[table].columns}
    path = TENANT_TABLES[table]
    first_hop = path.split()[0].split("->")[0].strip()
    assert first_hop in columns, (
        f"{table} is recorded as reaching its tenant via {first_hop!r}, "
        f"but that column does not exist. Columns: {sorted(columns)}"
    )


def test_the_unscoped_tenant_tables_are_named_and_not_forgotten():
    """These are known holes, kept visible. When one is fixed, its entry changes
    from NOT YET SCOPED to the path, and the test above starts checking it.

    `leagues` left this list when alembic 0003 landed `tenant_id`, which also
    completed the chain for the nine tables that reach a tenant through it.
    The four below reach no tenant by any path: `accounts` has no league at
    all, and the spend ledger is deliberately global because the UTC-month
    ceiling is shared -- which is a decision to make, not a column to add."""
    unscoped = {t for t, p in TENANT_TABLES.items() if p.startswith("NOT YET")}
    assert unscoped == {
        "accounts", "raw_cache", "ai_spend_months", "ai_spend_entries",
    }, (
        f"the set of known-unprotected tenant tables changed: {sorted(unscoped)}. "
        f"If one was fixed, record its path instead of removing it from the list."
    )


def test_the_leagues_tenant_column_is_still_nullable():
    """Pins the expand window open, and fails when it closes.

    `leagues.tenant_id` being nullable is not an accident to be tidied up: it
    is what lets the 37 League construction sites be converted a few at a time
    with the suite green. When alembic/pending/0004 lands this test fails, and
    the fix is to delete it -- in the same change that moves the migration and
    edits the model, so the three cannot drift apart silently.

    Until then, do not read `"leagues": "tenant_id"` above as "leagues are
    isolated". The column exists; nothing yet requires it to be filled.
    """
    tenant_id = Base.metadata.tables["leagues"].columns["tenant_id"]
    assert tenant_id.nullable, (
        "leagues.tenant_id is NOT NULL, so the contract step has landed. "
        "Delete this test, and check that alembic/pending/0004 moved into "
        "alembic/versions/ in the same change."
    )
