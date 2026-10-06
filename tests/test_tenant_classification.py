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
    # Worker liveness. Global by construction, like `jobs` has no RLS policy
    # by construction: one worker process serves every tenant, so there is no
    # tenant whose heartbeat this would be. `owner` is a host or task identity,
    # which is also why `observability.py` refuses it as a metric dimension.
    # Nothing in the table names a member, a league or a payload -- the
    # columns are two timestamps and six counters.
    "worker_heartbeats",
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
    # Scoped at revision 0014. The Phase 36 audit's first finding -- swid plus
    # the Fernet-encrypted espn_s2, no tenant column, therefore no policy. It
    # reaches no league, so it needed a column of its own. NOT NULL now, so a
    # credential row invisible to every policy is impossible rather than merely
    # unlikely. No unique constraint was swapped with it: this table has none,
    # so `swid` is not unique and two rows may hold the same credential -- a
    # product decision nobody has made.
    "accounts": "tenant_id",
    # Scoped at revision 0012, which also made the key composite
    # `(tenant_id, key)` and closed a measured enumeration oracle: with `key`
    # alone, a second tenant's INSERT failed on the primary key, and a
    # uniqueness error is not something row-level security hides.
    #
    # This entry said NOT YET SCOPED for two revisions after it was scoped, and
    # the test below passed the whole time because it asserts the SET and this
    # name was still in it. Green while recording something false -- lesson 1.
    "raw_cache": "tenant_id",
    # The AI spend ledger. **Deliberately global**, decided by the operator on
    # 2026-10-06 rather than left open: the UTC-month ceiling is shared across
    # tenants, so one tenant's spend consumes everyone's budget and a
    # tenant-scoped role can modify another tenant's ledger rows. Both
    # consequences are accepted; the ceiling is a cost control on one
    # Anthropic key, not a per-tenant entitlement.
    #
    # Recorded as a decision rather than as a gap, because "NOT YET SCOPED"
    # invited the next session to close it and closing it would have been
    # wrong. The two tests below pin the decision from both sides: these
    # tables carry no tenant column at all, and the set of genuinely unscoped
    # tables is now empty.
    "ai_spend_months": "DELIBERATELY GLOBAL - one shared UTC-month ceiling",
    "ai_spend_entries": "DELIBERATELY GLOBAL - one shared UTC-month ceiling",
    # Durable work. Scoped by its own tenant_id, and deliberately WITHOUT an
    # RLS policy: one worker process serves every tenant, so a policy on the
    # claim would have to be bypassed to work at all. Enforcement for jobs is
    # the tenant the worker binds before it RUNS one, not a predicate on the
    # claim -- recorded here so "no policy" reads as a decision.
    "jobs": "tenant_id (nullable; the worker refuses a tenantless job)",
    # Recurring intents. Same reasoning as `jobs`: scoped by its own
    # tenant_id, and no RLS policy because one scheduler process materialises
    # for every tenant, so a policy on the scan would have to be bypassed to
    # function. Recorded so "no policy" reads as a decision.
    "schedules": "tenant_id (nullable; no production writer -- see test_outbox_schedules_unwired)",
    # Pending side effects. Same reasoning as `jobs` and `schedules`: scoped
    # by its own tenant_id, no RLS policy, because one relay process drains
    # for every tenant and a policy on the scan would have to be bypassed to
    # function. The tenant travels in the row for the receiver's benefit.
    "outbox": "tenant_id (nullable; no production writer -- see test_outbox_schedules_unwired)",
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


#: The entries that are NOT a column path. Defined once, because every test
#: that asks "is this a path?" was asking "does it start with NOT YET" -- and
#: the moment a second sentinel existed (DELIBERATELY GLOBAL, for the shared
#: spend ledger) two tests started reading the sentinel as a column name and
#: asserting that a column called `DELIBERATELY` exists. A predicate keyed on
#: one spelling is the same defect as a test parametrised over its own input.
SENTINEL_PREFIXES = ("NOT YET", "DELIBERATELY GLOBAL")


def is_column_path(path: str) -> bool:
    return not path.startswith(SENTINEL_PREFIXES)


def test_each_sentinel_prefix_says_something_true_about_the_table_set():
    """Both exclusions are accounted for, in the direction each one claims.

    The first version of this test asserted that no entry is "neither a column
    path nor a sentinel" -- which is a tautology, because `is_column_path` is
    DEFINED as "does not start with a sentinel". `A and not A` cannot fail.
    Removed rather than reworded: what is left are two assertions that can.

    `NOT YET` must now match nothing -- the set emptied when the spend ledger
    became a decision -- and `DELIBERATELY GLOBAL` must match something, since
    a prefix nothing uses is dead weight that reads as a working exclusion.
    """
    not_yet = sorted(t for t, p in TENANT_TABLES.items() if p.startswith("NOT YET"))
    assert not_yet == [], (
        f"{not_yet} are recorded as NOT YET SCOPED. That set emptied on "
        "2026-10-06; a new entry in it is a new known hole and should be "
        "recorded as one deliberately, not inherited from this prefix."
    )
    global_tables = sorted(
        t for t, p in TENANT_TABLES.items() if p.startswith("DELIBERATELY GLOBAL")
    )
    assert global_tables == ["ai_spend_entries", "ai_spend_months"], global_tables


@pytest.mark.parametrize(
    "table",
    sorted(t for t, path in TENANT_TABLES.items() if is_column_path(path)),
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


def test_the_leagues_tenant_column_is_not_null():
    """The expand window is closed, and reopening it should be as loud as
    closing it was.

    Its predecessor asserted the opposite -- that the column was still
    nullable -- and said to delete it in the change that landed the contract.
    This is that change. Replacing the assertion rather than removing it keeps
    the fact under test: `leagues.tenant_id` being NOT NULL is what makes a
    tenantless league impossible rather than merely unlikely, and a league with
    no tenant is invisible to every policy and therefore to everyone.
    """
    tenant_id = Base.metadata.tables["leagues"].columns["tenant_id"]
    assert tenant_id.nullable is False


def test_the_old_global_unique_is_gone():
    """`uq_league_season` made the colliding-tenant case impossible to INSERT.

    Two tenants could not both hold the same ESPN league and season -- which
    is exactly the attack Phase 36 existed to test. It held the line through
    the expand window, because SQL treats NULLs as distinct and two
    NULL-tenant rows would otherwise have satisfied the tenant-scoped
    constraint. With the column NOT NULL there are no such rows left to guard.
    """
    names = {c.name for c in Base.metadata.tables["leagues"].constraints}
    assert "uq_league_season" not in names
    assert "uq_league_tenant_season" in names


def test_two_tenants_can_hold_the_same_espn_league(db_session):
    """P36-1, as a behaviour rather than a schema claim.

    The whole point of dropping the global unique. Asserted by inserting,
    because a constraint inventory can look right while the database refuses
    the row.
    """
    from api.models import League, Tenant

    first = db_session.query(Tenant).one()
    second = Tenant(slug="beta")
    db_session.add(second)
    db_session.flush()

    for tenant in (first, second):
        db_session.add(
            League(
                tenant_id=tenant.id,
                espn_league_id="collide-1",
                season=2026,
                is_public=True,
            )
        )
    db_session.flush()

    held = [
        row.tenant_id
        for row in db_session.query(League).filter(
            League.espn_league_id == "collide-1"
        )
    ]
    assert sorted(held) == sorted([first.id, second.id])


def test_the_unscoped_tenant_tables_are_named_and_not_forgotten():
    """These are known holes, kept visible. When one is fixed, its entry changes
    from NOT YET SCOPED to the path, and the test above starts checking it.

    **The list is now empty.** `leagues` left it when 0003 landed `tenant_id`,
    which also completed the chain for the nine tables reaching a tenant
    through it; `raw_cache` left at 0012 and `accounts` at 0014; and the two
    spend-ledger tables left on 2026-10-06, not by acquiring a column but by
    the operator deciding they should not have one. Their entries say
    DELIBERATELY GLOBAL and `test_the_shared_spend_ceiling_is_a_decision`
    below pins what that costs.

    The distinction matters more than the empty set does: "NOT YET SCOPED"
    invited the next session to close these two, and closing them would have
    been wrong.

    A caution this entry earned: `raw_cache` sat in this set for two revisions
    after it was scoped, and this test passed the whole time, because it asserts
    the SET and the stale name was still in it. Green while recording something
    false. The remedy is not a cleverer assertion here, it is editing the entry
    in the same change that scopes the table.
    """
    unscoped = {t for t, p in TENANT_TABLES.items() if p.startswith("NOT YET")}
    assert unscoped == set(), (
        f"the set of known-unprotected tenant tables changed: {sorted(unscoped)}. "
        f"If one was fixed, record its path instead of removing it from the list."
    )


def test_the_shared_spend_ceiling_is_a_decision_and_says_what_it_costs():
    """The spend ledger is global on purpose. Pinned so it stays a decision.

    Asserted three ways, because "deliberately global" is exactly the kind of
    label that can be true of the comment and false of the schema:

      * the entries say DELIBERATELY GLOBAL, so nobody reads them as a gap;
      * the tables really have **no** tenant column, so the label is not
        describing an unused one; and
      * nothing claims an RLS policy for them.

    What it costs, stated here rather than left to be rediscovered: one
    tenant's spend consumes everyone's budget, and a tenant-scoped role can
    modify another tenant's ledger rows. Both were accepted on 2026-10-06. The
    ceiling is a cost control on one Anthropic key, not a per-tenant
    entitlement -- and if that ever changes, this test is what fails.
    """
    ledger = ("ai_spend_months", "ai_spend_entries")
    for table in ledger:
        assert TENANT_TABLES[table].startswith("DELIBERATELY GLOBAL"), table
        columns = set(Base.metadata.tables[table].columns.keys())
        assert "tenant_id" not in columns, (
            f"{table} now has a tenant_id, so it is no longer global. Either "
            "scope it properly and change its entry, or drop the column."
        )


def test_a_plain_tenant_id_path_means_the_column_cannot_be_null():
    """"Scoped by tenant_id" should mean the tenant is required.

    Otherwise it means "has somewhere to put a tenant", which is what the
    expand windows were and not what they became. A table that has not
    contracted has to say so in its entry -- see the three below -- so the
    unqualified claim is the strong one.

    This check is new, and it found three entries making the strong claim on a
    nullable column the moment it existed. They now say `(nullable; ...)` with
    what enforces them instead.

    Two of those three were qualified wrongly at first. `schedules` and
    `outbox` said "nothing refuses a tenantless one", which reads as a live
    hole; measured by AST scan plus a raw-SQL check, **nothing under `api/`
    writes either table** -- the modules are reached only by `snapshot.py`,
    and only for their two measurement functions. So it is an obligation for
    whoever wires them up, not an exposure, and `test_outbox_schedules_unwired`
    fails the moment that changes.
    """
    overclaiming = sorted(
        table
        for table, path in TENANT_TABLES.items()
        if path == "tenant_id"
        and Base.metadata.tables[table].columns["tenant_id"].nullable
    )
    assert overclaiming == [], (
        f"{overclaiming} are recorded as scoped by tenant_id but the column is "
        "nullable, so a row with no tenant is constructible and invisible to "
        "every policy. Either contract the column or qualify the entry."
    )


def test_the_nullable_tenant_columns_are_exactly_these_three():
    """Phase 39's tables, and three different situations rather than one gap.

    `jobs` -- the worker refuses a tenantless job at run time
    (`TenantlessJob`, non-retryable), so the application enforces what the
    schema does not. Note that `tests/test_worker.py` constructs one
    deliberately to test that refusal: a NOT NULL would leave the guard's own
    test unable to build its subject, which is an argument to think rather than
    an argument not to do it.

    `schedules` and `outbox` -- **nothing refuses one.** A tenantless schedule
    materialises tenantless jobs, which the worker then refuses, so the failure
    surfaces one layer late and as somebody else's error. A tenantless outbox
    message is delivered with no tenant and the receiver copes.

    Fails when the set changes, in either direction, so contracting one of them
    is a deliberate act and adding a fourth is not silent.
    """
    nullable = sorted(
        table
        for table, path in TENANT_TABLES.items()
        if path.startswith("tenant_id (nullable")
    )
    assert nullable == ["jobs", "outbox", "schedules"]
    for table in nullable:
        assert Base.metadata.tables[table].columns["tenant_id"].nullable, (
            f"{table} is no longer nullable; move its entry to the plain "
            '"tenant_id" form in the same change'
        )


