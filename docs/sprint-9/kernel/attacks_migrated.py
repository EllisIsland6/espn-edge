"""Cross-tenant attacks against the schema the MIGRATIONS produced.

Not against hand-written SQL. Phase 36 proved a prototype; this proves what
`alembic upgrade head` actually leaves behind, as `edge_app` -- a LOGIN role
that is NOSUPERUSER and NOBYPASSRLS, because Phase 33 measured that FORCE ROW
LEVEL SECURITY constrains the table owner and NOT a superuser.

Every denial is paired with a control proving the same operation inside the
tenant's own scope is ACCEPTED. A test that only shows a refusal cannot tell
a working policy from a missing grant or a typo'd column.
"""
import os

import psycopg

# Overridable, because the originals hard-wired one machine's socket path and
# port -- so nobody else could run the only proof of tenant isolation this
# project has. Same defaults as before when the variables are unset.
DSN_OWNER = os.environ.get(
    "ATTACK_DSN_OWNER", "host=/tmp/pg/run port=5433 user=pgowner dbname=edge"
)
DSN_APP = os.environ.get(
    "ATTACK_DSN_APP", "host=/tmp/pg/run port=5433 user=edge_app dbname=edge"
)

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def denied(fn, want="42501"):
    """Returns (was_denied, sqlstate_or_message)."""
    try:
        fn()
        return False, "ACCEPTED"
    except psycopg.Error as exc:
        return exc.sqlstate == want, f"{exc.sqlstate}: {str(exc).splitlines()[0][:60]}"


def seed():
    with psycopg.connect(DSN_OWNER, autocommit=True) as c:
        for t in ("current_roster_entries", "current_roster_snapshots", "teams",
                  "raw_cache", "accounts", "memberships", "users", "leagues", "tenants"):
            c.execute(f"DELETE FROM {t}")
        c.execute("ALTER SEQUENCE tenants_id_seq RESTART WITH 1")
        c.execute("INSERT INTO tenants (id, slug, created_at) VALUES "
                  "(1,'alpha',now()), (2,'bravo',now())")
        c.execute("INSERT INTO users (id, email, created_at) VALUES "
                  "(1,'a@example.test',now()), (2,'b@example.test',now())")
        c.execute("INSERT INTO memberships (id, tenant_id, user_id, role) VALUES "
                  "(1,1,1,'member'), (2,2,2,'member')")
        # The colliding-league case P36-1 made impossible before 0003: both
        # tenants hold ESPN league 999 for the same season.
        c.execute("INSERT INTO leagues (id, espn_league_id, season, lifecycle, is_public, tenant_id)"
                  " VALUES (1,'999',2026,'active',false,1), (2,'999',2026,'active',false,2)")
        c.execute("INSERT INTO accounts (id,label,swid,espn_s2_encrypted,status,created_at,tenant_id)"
                  " VALUES (1,'alpha-acct','SWID-A','ENC-A','active',now(),1),"
                  "        (2,'bravo-acct','SWID-B','ENC-B','active',now(),2)")
        c.execute("INSERT INTO raw_cache (key, fetched_at, payload_json, tenant_id) VALUES "
                  "('league:999:alpha', now(), '{\"who\":\"alpha\"}', 1),"
                  "('league:999:bravo', now(), '{\"who\":\"bravo\"}', 2)")
        c.execute("INSERT INTO teams (id, league_id, espn_team_id, is_me, autodrafted, wins,"
                  " losses, ties, points_for, points_against) VALUES"
                  " (1,1,1,true,false,0,0,0,0,0), (2,2,1,true,false,0,0,0,0,0)")
        c.execute("INSERT INTO current_roster_snapshots (id, league_id, scoring_period, synced_at)"
                  " VALUES (1,1,1,now()), (2,2,1,now())")
        c.execute("INSERT INTO current_roster_entries (id, snapshot_id, team_id, lineup_slot_id,"
                  " slot_index, espn_player_id) VALUES (1,1,1,1,0,111), (2,2,2,2,0,222)")


def _resync_sequences():
    """Seeding with explicit ids leaves every sequence at 1, so the first
    CONTROL insert collides on the primary key and reads as a policy failure.
    A control that fails for the wrong reason proves nothing."""
    with psycopg.connect(DSN_OWNER, autocommit=True) as c:
        for table in ("tenants", "users", "memberships", "leagues", "accounts",
                      "teams", "current_roster_snapshots", "current_roster_entries"):
            c.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}','id'),"
                f" COALESCE((SELECT max(id) FROM {table}), 1))"
            )


def as_tenant(conn, tid):
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tid),))


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def main():
    seed()
    _resync_sequences()

    # --- A1: no context = no rows, on every policied table -------------------
    with psycopg.connect(DSN_APP) as c:
        for table in ("leagues", "accounts", "raw_cache", "users", "memberships",
                      "tenants", "current_roster_entries"):
            rows = one(c, f"SELECT count(*) FROM {table}")[0][0]
            check(f"A1 absent context reads nothing from {table}", rows == 0, f"{rows} rows")
        c.rollback()

    # --- A2/A3: scoped read, and the colliding id is invisible ---------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        mine = one(c, "SELECT id, tenant_id FROM leagues")
        check("A2 tenant sees exactly its own league", mine == [(1, 1)], str(mine))
        other = one(c, "SELECT id FROM leagues WHERE id = 2")
        check("A3 guessed id for another tenant returns nothing", other == [], str(other))
        same_espn = one(c, "SELECT count(*) FROM leagues WHERE espn_league_id = '999'")[0][0]
        check("A3b colliding ESPN id across tenants is invisible", same_espn == 1, f"{same_espn}")
        c.rollback()

    # --- A4/A5: cross-tenant UPDATE and DELETE affect nothing ----------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        n = c.execute("UPDATE leagues SET name='pwned' WHERE id=2").rowcount
        check("A4 cross-tenant UPDATE affects no rows", n == 0, f"{n} rows")
        n = c.execute("DELETE FROM leagues WHERE id=2").rowcount
        check("A5 cross-tenant DELETE affects no rows", n == 0, f"{n} rows")
        n = c.execute("UPDATE leagues SET name='mine' WHERE id=1").rowcount
        check("A5c CONTROL: same UPDATE in own scope is accepted", n == 1, f"{n} rows")
        c.rollback()

    # --- A6: INSERT into another tenant is refused ---------------------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        ok, detail = denied(lambda: c.execute(
            "INSERT INTO leagues (espn_league_id, season, lifecycle, is_public, tenant_id)"
            " VALUES ('777',2026,'active',false,2)"))
        check("A6 INSERT into another tenant refused (42501)", ok, detail)
        c.rollback()
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        c.execute("INSERT INTO leagues (espn_league_id, season, lifecycle, is_public, tenant_id)"
                  " VALUES ('777',2026,'active',false,1)")
        check("A6c CONTROL: same INSERT into own tenant accepted", True)
        c.rollback()

    # --- A7: accounts. The audit's headline finding. -------------------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        rows = one(c, "SELECT swid, espn_s2_encrypted FROM accounts")
        check("A7 credentials of other tenants are invisible",
              rows == [("SWID-A", "ENC-A")], str(rows))
        leaked = one(c, "SELECT count(*) FROM accounts WHERE swid = 'SWID-B'")[0][0]
        check("A7b targeted read of another tenant's swid returns nothing", leaked == 0)
        c.rollback()

    # --- A8: raw_cache payloads ---------------------------------------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        rows = one(c, "SELECT key FROM raw_cache")
        check("A8 raw payloads of other tenants are invisible",
              rows == [("league:999:alpha",)], str(rows))
        c.rollback()

    # --- A8b: the hole that WAS here, and the fix that closed it -------------
    #
    # This asserted the defect: `raw_cache`'s primary key was `key` alone, so a
    # second tenant inserting a key another tenant already held failed `23505`,
    # and a uniqueness error is not something row-level security hides. One
    # tenant could therefore test whether another held a given cache key.
    #
    # Revision 0012 made the primary key `(tenant_id, key)`. The INSERT below
    # now SUCCEEDS, which is the fix: the two rows are distinct keys. Asserting
    # the old refusal would keep failing forever while reporting a hole that no
    # longer exists -- it failed exactly that way the first time this suite was
    # run against the migrated schema.
    #
    # Both halves are checked, because "the insert succeeded" on its own would
    # also be true of a schema with no isolation at all.
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        accepted = True
        try:
            c.execute(
                "INSERT INTO raw_cache (key, fetched_at, payload_json, tenant_id)"
                " VALUES ('league:999:bravo', now(), '{}', 1)")
        except psycopg.Error as exc:
            accepted, detail = False, f"{exc.sqlstate}: {exc}"[:80]
        else:
            detail = "accepted, as it should be since 0012"
        check("A8b the colliding cache key is accepted (0012 closed the oracle)",
              accepted, detail)
        # ...and it is still the inserting tenant's own row only.
        rows = one(c, "SELECT key, tenant_id FROM raw_cache ORDER BY key")
        check("A8b2 and the other tenant's row with that key stays invisible",
              rows == [("league:999:alpha", 1), ("league:999:bravo", 1)], str(rows))
        c.rollback()

    # --- A9: users are reachable only through a membership -------------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        rows = one(c, "SELECT email FROM users")
        check("A9 only users with a membership in this tenant are visible",
              rows == [("a@example.test",)], str(rows))
        c.rollback()

    # --- A10: privilege escalation via memberships (correction 2) ------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        ok, detail = denied(lambda: c.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (2, 1, 'member')"))
        check("A10 cannot grant itself membership of another tenant (42501)", ok, detail)
        c.rollback()
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        c.execute("INSERT INTO memberships (tenant_id, user_id, role) VALUES (1, 2, 'member')")
        check("A10c CONTROL: membership within own tenant accepted", True)
        c.rollback()

    # --- A11: current_roster_entries, which reaches its league via snapshot --
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        rows = one(c, "SELECT espn_player_id FROM current_roster_entries")
        check("A11 roster entries scope through snapshot_id, not league_id",
              rows == [(111,)], str(rows))
        c.rollback()

    # --- A12: composite FK. A child pointing at another tenant's league. -----
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)
        ok, detail = denied(lambda: c.execute(
            "INSERT INTO teams (league_id, espn_team_id, is_me, autodrafted, wins, losses,"
            " ties, points_for, points_against) VALUES (2, 9, false, false, 0,0,0,0,0)"))
        check("A12 child row pointing at another tenant's league refused (42501)", ok, detail)
        c.rollback()

    # --- A13: pool reuse. The is_local=true / false difference. --------------
    with psycopg.connect(DSN_APP) as c:
        as_tenant(c, 1)                      # transaction-local
        one(c, "SELECT count(*) FROM leagues")
        c.commit()                           # transaction ends, so does the binding
        rows = one(c, "SELECT count(*) FROM leagues")[0][0]
        check("A13 after commit the binding is gone: same connection reads nothing",
              rows == 0, f"{rows} rows")
        c.rollback()

    with psycopg.connect(DSN_APP) as c:
        c.execute("SELECT set_config('app.tenant_id', '1', false)")  # session-scoped
        c.commit()
        rows = one(c, "SELECT count(*) FROM leagues")[0][0]
        check("A13b CONTROL: is_local=false SURVIVES the commit -- this is the pool leak",
              rows > 0, f"{rows} rows visible with no binding in this transaction")
        c.rollback()

    # --- A14: the owner/superuser bypass (P33-1) -----------------------------
    with psycopg.connect(DSN_OWNER) as c:
        rows = one(c, "SELECT count(*) FROM leagues")[0][0]
        check("A14 a SUPERUSER ignores FORCE RLS entirely -- why the startup guard exists",
              rows > 0, f"{rows} rows with no tenant set")
        c.rollback()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
