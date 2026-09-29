"""row-level security policies (PostgreSQL only)

Everything before this revision arranged for a tenant to be *knowable*. This
is the revision that makes it *enforced*. Until it runs, `leagues.tenant_id`
is a column the application fills in and nothing checks.

PostgreSQL only, and a no-op elsewhere. SQLite has no row-level security at
all, so on SQLite this revision does nothing and says so rather than appearing
to have protected something.

## Three corrections to the design Phase 36 proved

The SQL in `docs/sprint-9/kernel/rls.sql` was measured against 13 cross-tenant
attacks and denied all 13. It is still wrong in three places, and each was
found by reading it against the table list rather than by running it -- which
is the point of the audit that followed the attack suite.

1. **`current_roster_entries` has no `league_id`.** The prototype policy filters
   on one. Phase 36's run reported that `CREATE POLICY` failing, and the error
   was swallowed by a `grep -v` that was filtering routine `psql` notices. The
   table ended up with FORCE and no policy, which denies everything -- broken
   closed rather than leaky, and invisible because attack #5 probed `teams`.
   Here it reaches its league through `snapshot_id`.

2. **`tenants` and `memberships` had `USING` and no `WITH CHECK`.** `USING`
   scopes what a policy lets you *read*; without `WITH CHECK`, writes are
   unconstrained. On `memberships` that is not a data leak, it is privilege
   escalation: one tenant may INSERT a membership granting itself access to
   another. Every policy below has both.

3. **`users` had no policy at all.** It is a global table by construction --
   one person can belong to several tenants -- but "no tenant column" is not
   "safe to read": under a tenant-scoped role every email in the system was
   selectable. A user is visible here only through a membership in the current
   tenant.

## What this revision deliberately leaves alone

**The AI spend ledger.** `ai_spend_months` and `ai_spend_entries` get no
policies, because the UTC-month ceiling they enforce is *shared across
tenants* -- a per-tenant policy would give each tenant its own ceiling, which
is a different product decision and a larger one. The consequence is stated
rather than hidden: one tenant's spend consumes every tenant's budget, and a
tenant-scoped role can read and modify another tenant's ledger rows. The
operator grant should withhold DELETE on both tables; that is a grant, not a
policy, and grants are not made here (see below).

**Global reference data.** `players`, `nflverse_player_maps`, `adp_snapshots`,
`opportunity_weeks` and `opportunity_imports` are the same rows for every
tenant. RLS on them would cost query plans and protect nothing.

**Roles and grants.** Roles are cluster-level and a migration running as the
schema owner may hold no CREATEROLE. More importantly, a role that this
migration invented would be a role no deployment knows about. `edge_app` /
`edge_worker` and their grants stay an operator step --
`docs/sprint-9/kernel/rls.sql` has the statements. Two consequences worth
carrying: **a new table added by a later migration has no grants for the app
role until the operator issues them**, and **none of this binds at all if the
application connects as the owner or as a BYPASSRLS role**, which Phase 33
measured -- policies all correct, isolation silently off. That is what
`api/db.assert_runtime_role_is_constrained()` refuses at startup.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | None = None
depends_on: str | None = None

#: Tables scoped by their own `tenant_id`.
DIRECT = ("leagues", "accounts", "raw_cache")

#: Tables that reach a tenant through `leagues.id`.
VIA_LEAGUE = (
    "teams",
    "draft_picks",
    "metrics",
    "matchups",
    "transactions",
    "lineup_slots",
    "current_roster_snapshots",
    "metric_snapshots",
    "ai_reports",
)

_LEAGUE_SCOPE = "league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())"

#: `current_roster_entries` reaches its league through its snapshot, because it
#: has no `league_id` of its own. This is correction 1 above.
_ENTRY_SCOPE = (
    "snapshot_id IN (SELECT id FROM current_roster_snapshots WHERE " + _LEAGUE_SCOPE + ")"
)


def _policy(table: str, predicate: str) -> None:
    """USING and WITH CHECK, always both. See correction 2."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS t_{table} ON {table}")
    op.execute(
        f"CREATE POLICY t_{table} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        # Not "nothing to do" -- nothing CAN be done. Said out loud so a
        # SQLite run is never mistaken for a protected database.
        op.get_bind().exec_driver_sql("SELECT 1")
        return

    # `current_setting(..., true)` returns NULL when the setting is absent, so
    # every predicate below compares against NULL and matches nothing. NO
    # CONTEXT = NO ROWS is the "absent context" attack, and it is a property of
    # this one function rather than of each policy remembering to handle it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant() RETURNS bigint AS $$
          SELECT NULLIF(current_setting('app.tenant_id', true), '')::bigint;
        $$ LANGUAGE sql STABLE
        """
    )

    _policy("tenants", "id = current_tenant()")
    _policy("memberships", "tenant_id = current_tenant()")

    # Correction 3: a user is reachable only through a membership in this
    # tenant. Without this, every email in the system was selectable.
    _policy(
        "users",
        "id IN (SELECT user_id FROM memberships WHERE tenant_id = current_tenant())",
    )

    for table in DIRECT:
        _policy(table, "tenant_id = current_tenant()")
    for table in VIA_LEAGUE:
        _policy(table, _LEAGUE_SCOPE)
    _policy("current_roster_entries", _ENTRY_SCOPE)


def downgrade() -> None:
    if not _is_postgres():
        return
    tables = (
        ("tenants", "memberships", "users")
        + DIRECT
        + VIA_LEAGUE
        + ("current_roster_entries",)
    )
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS t_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS current_tenant()")
