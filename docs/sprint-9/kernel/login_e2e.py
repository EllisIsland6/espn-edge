"""The whole login path, against the schema the migration chain produces.

Not against hand-made tables -- `alembic upgrade head` built this database,
including 0006's app_sessions and 0007's read policies.
"""
import hashlib, secrets
import psycopg

OWNER = "host=/tmp/pg/run port=5433 user=pgowner dbname=edge2"
APP   = "host=/tmp/pg/run port=5433 user=edge_app dbname=edge2"
results = []
def check(name, ok, detail=""):
    results.append(ok); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))

token_a, token_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
h = lambda t: hashlib.sha256(t.encode()).hexdigest()

with psycopg.connect(OWNER, autocommit=True) as c:
    c.execute("GRANT USAGE ON SCHEMA public TO edge_app")
    c.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO edge_app")
    c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO edge_app")
    # tenant 1 already exists: migration 0003's backfill created it.
    c.execute("INSERT INTO tenants (id,slug,created_at) VALUES (2,'bravo',now()) ON CONFLICT DO NOTHING")
    c.execute("SELECT setval(pg_get_serial_sequence('tenants','id'), 2)")
    c.execute("INSERT INTO users (id,email,created_at) VALUES (1,'a@x.test',now()),(2,'b@x.test',now())")
    c.execute("INSERT INTO memberships (id,tenant_id,user_id,role) VALUES (1,1,1,'member'),(2,2,2,'member')")
    # Distinct ESPN ids: the old global unique is still in force at head,
    # because the contract step that drops it is parked at 0008. The
    # colliding-tenant case is only stageable after that lands -- measured in
    # Phase 37b, unchanged here.
    c.execute("INSERT INTO leagues (id,espn_league_id,season,lifecycle,is_public,tenant_id) VALUES "
              "(1,'111',2026,'active',false,1),(2,'222',2026,'active',false,2)")
    c.execute("SELECT setval(pg_get_serial_sequence('leagues','id'), 2)")
    c.execute("INSERT INTO app_sessions (token_hash,user_id,created_at,expires_at,origin) VALUES "
              "(%s,1,now(),now()+interval '1 hour','test'),(%s,2,now(),now()+interval '1 hour','test')",
              (h(token_a), h(token_b)))

def login(conn, token):
    """Exactly what api/db._bound_session does in hosted mode."""
    uid = conn.execute("SELECT user_id FROM app_sessions WHERE token_hash=%s "
                       "AND revoked_at IS NULL AND expires_at > now()", (h(token),)).fetchone()
    if uid is None: return None, None
    uid = uid[0]
    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(uid),))
    tid = conn.execute("SELECT tenant_id FROM memberships WHERE user_id=%s", (uid,)).fetchall()
    if len(tid) != 1: return uid, None
    tid = tid[0][0]
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tid),))
    return uid, tid

print("End-to-end login on the migrated schema:")
with psycopg.connect(APP) as c:
    uid, tid = login(c, token_a)
    check("user A logs in and lands in its tenant", (uid, tid) == (1, 1), f"user={uid} tenant={tid}")
    lg = c.execute("SELECT id,tenant_id FROM leagues").fetchall()
    check("sees only its own tenant's leagues", lg == [(1,1)], str(lg))
    check("tenant 2's league is invisible to A",
          c.execute("SELECT count(*) FROM leagues WHERE id=2").fetchone()[0] == 0)
    c.rollback()

with psycopg.connect(APP) as c:
    uid, tid = login(c, token_b)
    check("user B lands in a DIFFERENT tenant", (uid, tid) == (2, 2), f"user={uid} tenant={tid}")
    lg = c.execute("SELECT id,tenant_id FROM leagues").fetchall()
    check("and sees only tenant 2's leagues", lg == [(2,2)], str(lg))
    c.rollback()

print("\nRefusals:")
with psycopg.connect(APP) as c:
    check("an unknown token yields no user", login(c, "nonsense")[0] is None)
    c.rollback()
with psycopg.connect(OWNER, autocommit=True) as c:
    c.execute("UPDATE app_sessions SET revoked_at=now() WHERE token_hash=%s", (h(token_a),))
with psycopg.connect(APP) as c:
    check("a revoked token yields no user", login(c, token_a)[0] is None)
    c.rollback()

print("\nThe escalation the read policy must not open:")
with psycopg.connect(APP) as c:
    login(c, token_b)
    try:
        c.execute("INSERT INTO memberships (tenant_id,user_id,role) VALUES (1,2,'member')")
        check("B cannot grant itself tenant 1", False, "ACCEPTED")
    except psycopg.Error as e:
        check("B cannot grant itself tenant 1", e.sqlstate == "42501", e.sqlstate)
    c.rollback()
with psycopg.connect(APP) as c:
    login(c, token_b)
    c.execute("INSERT INTO leagues (espn_league_id,season,lifecycle,is_public,tenant_id) "
              "VALUES ('555',2026,'active',false,2)")
    check("CONTROL: B can still write in its own tenant", True)
    c.rollback()

print(f"\n{sum(results)}/{len(results)} passed")
