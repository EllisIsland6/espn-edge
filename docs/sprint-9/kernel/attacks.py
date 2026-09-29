"""Phase 36 — the named R4 attacks, as the non-owner runtime role.

"Any isolation failure blocks all later work", so these are written to FAIL
loudly rather than to pass quietly. Each names what it would mean if it passed.
"""
import sys; sys.path.insert(0, "/tmp/p35/repo")
import sqlalchemy as sa

A, B, *L = open("/tmp/p36/ids.txt").read().split(",")
A, B = int(A), int(B)
ALPHA, BRAVO = [int(x) for x in L[:3]], [int(x) for x in L[3:]]

APP = "postgresql+psycopg://edge_app@/edge?host=/tmp/p35&port=5434"
OWNER = "postgresql+psycopg://edge_owner@/edge?host=/tmp/p35&port=5434"
app = sa.create_engine(APP, future=True, pool_size=1, max_overflow=0)
owner = sa.create_engine(OWNER, future=True)

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def as_tenant(conn, tid):
    """Transaction-local, NOT session-local. This is the whole pool-reuse story.

    `SET LOCAL` takes no bind parameters, so the parameterisable form is
    `set_config(name, value, is_local)` with is_local TRUE. Phase 33 used FALSE,
    which is session-scoped and survives the transaction -- the exact shape of
    the pool-reuse leak tested below.
    """
    conn.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})


print("R4 ATTACKS, as edge_app (NOSUPERUSER, NOBYPASSRLS)")
print()

# 1 -- absent context must fail closed
with app.connect() as c:
    with c.begin():
        n = c.execute(sa.text("SELECT count(*) FROM leagues")).scalar()
    check("absent context returns nothing", n == 0,
          f"saw {n} leagues with no app.tenant_id set")

# 2 -- correct context sees only its own
with app.connect() as c:
    with c.begin():
        as_tenant(c, A)
        rows = c.execute(sa.text("SELECT id, tenant_id FROM leagues")).all()
    own = {r[0] for r in rows}
    check("tenant sees only its own leagues",
          own == set(ALPHA), f"alpha saw {sorted(own)}, expected {ALPHA}")

# 3 -- guessed / colliding IDs: naming another tenant's row by primary key
with app.connect() as c:
    with c.begin():
        as_tenant(c, A)
        got = c.execute(sa.text("SELECT count(*) FROM leagues WHERE id = ANY(:ids)"),
                        {"ids": BRAVO}).scalar()
    check("guessed primary keys of another tenant return nothing", got == 0,
          f"alpha saw {got} of bravo's leagues by id")

# 4 -- colliding espn ids: same external id, different tenants, both exist
with owner.connect() as c:
    dupes = c.execute(sa.text("""
        SELECT espn_league_id, count(DISTINCT tenant_id) FROM leagues
        GROUP BY espn_league_id HAVING count(DISTINCT tenant_id) > 1""")).all()
    check("the same ESPN league id can exist in two tenants", len(dupes) == 3,
          f"{len(dupes)} external ids shared across tenants")

# 5 -- direct SQL against a child table, bypassing the league join
with app.connect() as c:
    with c.begin():
        as_tenant(c, A)
        n = c.execute(sa.text("SELECT count(*) FROM teams")).scalar()
        cross = c.execute(sa.text(
            "SELECT count(*) FROM teams WHERE league_id = ANY(:ids)"), {"ids": BRAVO}).scalar()
    check("direct SQL on a child table is tenant-scoped", n == 3 and cross == 0,
          f"own teams {n}, other-tenant teams {cross}")

# 6 -- cross-tenant write: inserting into another tenant must be refused
with app.connect() as c:
    try:
        with c.begin():
            as_tenant(c, A)
            c.execute(sa.text("""
                INSERT INTO leagues (espn_league_id, season, name, size, lifecycle,
                    scoring_json, lineup_slots_json, is_public, tenant_id)
                VALUES ('999999', 2026, 'smuggled', 10, 'in_season','{}','{}',true,:t)"""),
                {"t": B})
        check("cross-tenant INSERT is refused", False, "the row was accepted")
    except Exception as exc:
        check("cross-tenant INSERT is refused", True, type(exc).__name__)

# 7 -- cross-tenant child: a team pointing at another tenant's league
with app.connect() as c:
    try:
        with c.begin():
            as_tenant(c, A)
            c.execute(sa.text("""
                INSERT INTO teams (league_id, espn_team_id, name, abbrev, owner_swids_json,
                    is_me, autodrafted, wins, losses, ties, points_for, points_against)
                VALUES (:l, 99, 'smuggled', 'X', '[]', false, false, 0,0,0,0,0)"""),
                {"l": BRAVO[0]})
        check("cross-tenant child INSERT is refused", False, "the row was accepted")
    except Exception as exc:
        check("cross-tenant child INSERT is refused", True, type(exc).__name__)

# 8 -- POOL REUSE: the same physical connection, next transaction, no context
with app.connect() as c:
    with c.begin():
        as_tenant(c, A)
        c.execute(sa.text("SELECT count(*) FROM leagues")).scalar()
    with c.begin():                     # same connection, fresh transaction
        leaked = c.execute(sa.text("SELECT count(*) FROM leagues")).scalar()
        setting = c.execute(sa.text("SELECT current_setting('app.tenant_id', true)")).scalar()
    check("tenant context does not survive the transaction", leaked == 0,
          f"next transaction on the same connection saw {leaked} rows, setting={setting!r}")

# 9 -- pool reuse across checkin/checkout with pool_size=1 (same backend)
with app.connect() as c:
    with c.begin():
        as_tenant(c, A)
        c.execute(sa.text("SELECT 1"))
with app.connect() as c2:               # pool_size=1 -> same physical connection
    with c2.begin():
        leaked = c2.execute(sa.text("SELECT count(*) FROM leagues")).scalar()
    check("context does not survive a pool checkin/checkout", leaked == 0,
          f"recycled connection saw {leaked} rows")

# 10 -- immutable ownership: repointing a league to another tenant
with owner.connect() as c:
    try:
        with c.begin():
            c.execute(sa.text("UPDATE leagues SET tenant_id = :b WHERE id = :l"),
                      {"b": B, "l": ALPHA[0]})
        check("league ownership cannot be repointed", False, "the UPDATE succeeded")
    except Exception as exc:
        check("league ownership cannot be repointed", True, type(exc).__name__)

# 11 -- owner vs runtime role: the owner IS exempt, and that is the point
with owner.connect() as c:
    with c.begin():
        n = c.execute(sa.text("SELECT count(*) FROM leagues")).scalar()
    check("the owner role bypasses RLS (so the app must never use it)", n == 6,
          f"owner saw all {n} leagues with no tenant context")

with app.connect() as c:
    r = c.execute(sa.text("""SELECT rolsuper, rolbypassrls FROM pg_roles
                             WHERE rolname = current_user""")).one()
    check("the runtime role is neither superuser nor BYPASSRLS",
          r[0] is False and r[1] is False, f"rolsuper={r[0]} rolbypassrls={r[1]}")

# 12 -- the runtime role cannot turn RLS off
with app.connect() as c:
    try:
        with c.begin():
            c.execute(sa.text("ALTER TABLE leagues DISABLE ROW LEVEL SECURITY"))
        check("the runtime role cannot disable RLS", False, "it disabled RLS")
    except Exception as exc:
        check("the runtime role cannot disable RLS", True, type(exc).__name__)

print()
failed = [n for n, p, _ in results if not p]
print(f"{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILURES (these block all later work):")
    for f in failed:
        print(f"  - {f}")
