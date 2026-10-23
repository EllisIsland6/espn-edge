"""ops/provision_user.py -- a tenant per invited person, and its undoing.

Two halves. The first runs everywhere: argument parsing, the slug, the
refusals, with no database. The second runs only against PostgreSQL, because
row security is real nowhere else, and it says so when it skips: provision two
people, prove through the application role that each sees only their own
rows, revoke one, prove the other is untouched and the revoked one's rows are
gone -- gone, not hidden, read as a superuser who bypasses the policies.

    PROVISION_PG_OWNER_URL=postgresql+psycopg2://edge_owner@/edge?host=/tmp/pg/run&port=5433
    PROVISION_PG_APP_URL=postgresql+psycopg2://edge_app@/edge?host=/tmp/pg/run&port=5433
    PROVISION_PG_SUPER_URL=postgresql+psycopg2://pgowner@/edge?host=/tmp/pg/run&port=5433

The owner must be the role that ran `alembic upgrade head`, NOSUPERUSER and
NOBYPASSRLS like `edge_owner` on RDS, or FORCE ROW LEVEL SECURITY is not
binding it and the test proves nothing about the shape 0017 documents.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "provision_user.py"


def _load():
    spec = importlib.util.spec_from_file_location("provision_user", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before execution: a dataclass under `from __future__ import
    # annotations` resolves its field types through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pu = _load()

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL"

# ------------------------------------------------------------------ everywhere


@pytest.mark.parametrize(
    ("email", "slug"),
    [
        ("ellis@example.test", "ellis"),
        ("First.Last+tag@example.test", "first-last-tag"),
        ("  Mixed_Case.99 @x", "mixed-case-99"),
        ("---@x", "member"),
        ("@x", "member"),
        ("a" * 80 + "@x", "a" * 40),
        ("ends-with-dash-" + "x" * 30 + "@x", ("ends-with-dash-" + "x" * 30)[:40].rstrip("-")),
    ],
)
def test_the_slug_is_the_local_part_made_safe(email, slug):
    assert pu.derive_slug(email) == slug
    assert len(pu.derive_slug(email)) <= pu.SLUG_MAX


def test_candidate_slugs_suffix_without_overflowing():
    base = "x" * pu.SLUG_MAX
    seen = list(pu.candidate_slugs(base))
    assert seen[0] == base
    assert seen[1] == "x" * (pu.SLUG_MAX - 2) + "-2"
    assert all(len(s) <= pu.SLUG_MAX for s in seen)
    assert len(seen) == 1 + pu.SLUG_ATTEMPTS
    assert len(set(seen)) == len(seen)


def test_parse_args_provision_and_revoke(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://edge_owner@127.0.0.1:15432/edge")
    monkeypatch.delenv("TENANT_ID", raising=False)
    args = pu.parse_args(["provision", "someone@example.test", "sub-1"])
    assert (args.command, args.email, args.subject, args.issuer) == (
        "provision",
        "someone@example.test",
        "sub-1",
        ISSUER,
    )
    args = pu.parse_args(["revoke", "sub-1"])
    assert (args.command, args.subject, args.operator_tenant_id) == ("revoke", "sub-1", 1)
    monkeypatch.setenv("TENANT_ID", "7")
    assert pu.parse_args(["revoke", "sub-1"]).operator_tenant_id == 7
    assert pu.parse_args(["--issuer", "https://other", "revoke", "s"]).issuer == "https://other"


@pytest.mark.parametrize(
    ("env", "argv"),
    [
        ({"DATABASE_URL": "postgresql://x@h/db"}, ["provision", "a@b", "s"]),  # no issuer
        ({"OIDC_ISSUER": ISSUER}, ["provision", "a@b", "s"]),  # no url
        ({"OIDC_ISSUER": ISSUER, "DATABASE_URL": "sqlite:///x.db"}, ["provision", "a@b", "s"]),
        ({"OIDC_ISSUER": ISSUER, "DATABASE_URL": "postgresql://x@h/db"}, ["provision", "a@b"]),
        ({"OIDC_ISSUER": ISSUER, "DATABASE_URL": "postgresql://x@h/db"}, []),
    ],
)
def test_parse_args_refuses_an_incomplete_call(monkeypatch, env, argv):
    for name in ("OIDC_ISSUER", "DATABASE_URL", "TENANT_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exc:
        pu.parse_args(argv)
    assert exc.value.code == 2


def test_the_revoke_order_deletes_leagues_before_accounts_and_the_tenant_last():
    tables = [t for t, _ in pu.REVOKE_ORDER]
    assert tables.index("leagues") < tables.index("accounts"), "leagues.account_id has no cascade"
    assert tables[-1] == "tenants" and tables[-2] == "users"
    assert tables.index("memberships") < tables.index("users")
    assert {"jobs", "schedules", "outbox", "raw_cache", "app_sessions", "identities"} <= set(tables)


def test_main_names_no_person_when_it_refuses(monkeypatch, capsys):
    """A refusal is printed to stderr, and the email and subject appear nowhere."""

    class _Conn:
        def execute(self, *_a, **_k):
            class _R:
                def first(self):
                    return None

            return _R()

    class _Begin:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *_a):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

        def dispose(self):
            pass

    monkeypatch.setattr(pu, "create_engine", lambda *_a, **_k: _Engine())
    code = pu.main(
        ["--issuer", ISSUER, "--url", "postgresql://x@h/db", "revoke", "the-subject-value"]
    )
    out, err = capsys.readouterr()
    assert code == 2
    assert "refused:" in err and "the-subject-value" not in err and out == ""


# ------------------------------------------------------------------ PostgreSQL

OWNER_URL = os.environ.get("PROVISION_PG_OWNER_URL", "")
APP_URL = os.environ.get("PROVISION_PG_APP_URL", "")
SUPER_URL = os.environ.get("PROVISION_PG_SUPER_URL", "")

needs_postgres = pytest.mark.skipif(
    not (OWNER_URL and APP_URL),
    reason=(
        "row security is real only on PostgreSQL: set PROVISION_PG_OWNER_URL and "
        "PROVISION_PG_APP_URL to a migrated cluster (see the module docstring)"
    ),
)


@pytest.fixture
def pg():
    from sqlalchemy import create_engine

    owner = create_engine(OWNER_URL, future=True)
    app = create_engine(APP_URL, future=True)
    superuser = create_engine(SUPER_URL, future=True) if SUPER_URL else None
    # Premise: the owner is bound by FORCE ROW LEVEL SECURITY exactly as
    # edge_owner is on RDS. A superuser or a BYPASSRLS role would pass every
    # test below while proving nothing about the shape 0017 documents.
    from sqlalchemy import text

    with owner.connect() as conn:
        rolsuper, rolbypass = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert not rolsuper and not rolbypass, (
        "PROVISION_PG_OWNER_URL must be a NOSUPERUSER NOBYPASSRLS owner"
    )
    yield owner, app, superuser
    for engine in (owner, app, superuser):
        if engine is not None:
            engine.dispose()


def _bound(conn, tenant_id=None, user_id=None):
    from sqlalchemy import text

    if tenant_id is not None:
        conn.execute(text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)})
    if user_id is not None:
        conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)})


def _seed_league(owner, done, tag):
    """An account, a league and a team for the person, as the owner with the
    tenant bound -- the rows a revoke has to take with it."""
    from sqlalchemy import text

    with owner.begin() as conn:
        _bound(conn, done.tenant_id, done.user_id)
        account_id = conn.execute(
            text(
                "INSERT INTO accounts (label, swid, espn_s2_encrypted, status, created_at, tenant_id)"
                " VALUES (:l, :s, 'ENC', 'active', now(), :t) RETURNING id"
            ),
            {"l": f"acct-{tag}", "s": f"SWID-{tag}", "t": done.tenant_id},
        ).scalar_one()
        league_id = conn.execute(
            text(
                "INSERT INTO leagues (espn_league_id, season, account_id, lifecycle, is_public, tenant_id)"
                " VALUES (:e, 2026, :a, 'active', false, :t) RETURNING id"
            ),
            {"e": f"9{tag}", "a": account_id, "t": done.tenant_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO teams (league_id, espn_team_id, is_me, autodrafted, wins, losses, ties,"
                " points_for, points_against) VALUES (:l, 1, true, false, 0, 0, 0, 0, 0)"
            ),
            {"l": league_id},
        )
    return league_id


def _count(engine, sql, **params):
    from sqlalchemy import text

    with engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


@needs_postgres
def test_two_people_two_tenants_then_one_revoked(pg):
    from sqlalchemy import text

    owner, app, superuser = pg
    tag = uuid.uuid4().hex[:8]
    a_sub, b_sub = f"sub-a-{tag}", f"sub-b-{tag}"
    with owner.begin() as conn:
        a = pu.provision(conn, f"alpha-{tag}@example.test", a_sub, ISSUER)
    with owner.begin() as conn:
        b = pu.provision(conn, f"bravo-{tag}@example.test", b_sub, ISSUER)
    assert a.created and b.created
    assert a.tenant_id != b.tenant_id and a.user_id != b.user_id
    # Idempotent on the identity: the same subject again is the same ids, nothing created.
    with owner.begin() as conn:
        again = pu.provision(conn, f"alpha-{tag}@example.test", a_sub, ISSUER)
    assert again == pu.Provisioned(a.tenant_id, a.user_id, a.membership_id, created=False)

    a_league = _seed_league(owner, a, f"a{tag}")
    b_league = _seed_league(owner, b, f"b{tag}")

    # The login read path, as the application role with NOTHING bound: the
    # identity is found, the user is bound, the membership names the tenant.
    with app.begin() as conn:
        found = conn.execute(
            text("SELECT user_id FROM identities WHERE issuer = :i AND subject = :s"),
            {"i": ISSUER, "s": b_sub},
        ).scalar_one()
        assert found == b.user_id
        _bound(conn, user_id=found)
        tenant = conn.execute(
            text("SELECT tenant_id FROM memberships WHERE user_id = :u"), {"u": found}
        ).scalar_one()
        assert tenant == b.tenant_id
        # And the application role may not write the lookup table (0017).
        with pytest.raises(Exception, match="permission denied"):
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO identities (issuer, subject, user_id, created_at)"
                        " VALUES ('x', 'y', :u, now())"
                    ),
                    {"u": found},
                )

    # Each sees only their own rows: bound to A, B's tenant, league and team are invisible.
    with app.begin() as conn:
        _bound(conn, a.tenant_id, a.user_id)
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 1
        assert [r[0] for r in conn.execute(text("SELECT id FROM leagues")).all()] == [a_league]
        assert conn.execute(text("SELECT count(*) FROM teams")).scalar_one() == 1
        assert (
            conn.execute(
                text("SELECT count(*) FROM memberships WHERE tenant_id = :t"), {"t": b.tenant_id}
            ).scalar_one()
            == 0
        )

    # Revoke B: counts per table, in the order the keys permit.
    with owner.begin() as conn:
        counts = pu.revoke(conn, b_sub, ISSUER, operator_tenant_id=1)
    assert counts["tenants"] == 1 and counts["users"] == 1 and counts["memberships"] == 1
    assert counts["identities"] == 1 and counts["leagues"] == 1 and counts["accounts"] == 1
    assert counts["raw_cache"] == 0 and counts["jobs"] == 0 and counts["app_sessions"] == 0

    # A is untouched, through the application role.
    with app.begin() as conn:
        _bound(conn, a.tenant_id, a.user_id)
        assert [r[0] for r in conn.execute(text("SELECT id FROM leagues")).all()] == [a_league]
        assert conn.execute(text("SELECT count(*) FROM teams")).scalar_one() == 1
    with owner.begin() as conn:
        assert pu.provision(conn, "ignored@example.test", a_sub, ISSUER).created is False

    # B is gone -- not hidden. A superuser bypasses the policies and sees every row.
    if superuser is not None:
        assert _count(superuser, "SELECT count(*) FROM tenants WHERE id = :t", t=b.tenant_id) == 0
        assert _count(superuser, "SELECT count(*) FROM users WHERE id = :u", u=b.user_id) == 0
        assert _count(superuser, "SELECT count(*) FROM teams WHERE league_id = :l", l=b_league) == 0
        assert _count(superuser, "SELECT count(*) FROM identities WHERE subject = :s", s=b_sub) == 0
    with owner.begin() as conn:
        with pytest.raises(pu.Refused, match="no identity"):
            pu.revoke(conn, b_sub, ISSUER, operator_tenant_id=1)


@needs_postgres
def test_the_slug_collides_and_the_next_suffix_lands(pg):
    owner, _app, _super = pg
    tag = uuid.uuid4().hex[:8]
    with owner.begin() as conn:
        first = pu.provision(conn, f"same-{tag}@one.test", f"sub-1-{tag}", ISSUER)
    with owner.begin() as conn:
        second = pu.provision(conn, f"same-{tag}@two.test", f"sub-2-{tag}", ISSUER)
    assert first.tenant_id != second.tenant_id
    from sqlalchemy import text

    with owner.begin() as conn:
        _bound(conn, second.tenant_id, second.user_id)
        slug = conn.execute(text("SELECT slug FROM tenants")).scalar_one()
    assert slug == f"same-{tag}-2"
    for sub in (f"sub-1-{tag}", f"sub-2-{tag}"):
        with owner.begin() as conn:
            assert pu.revoke(conn, sub, ISSUER, operator_tenant_id=1)["tenants"] == 1


@needs_postgres
def test_revoke_refuses_the_operator_and_the_shared_tenant(pg):
    from sqlalchemy import text

    owner, _app, _super = pg
    tag = uuid.uuid4().hex[:8]
    with owner.begin() as conn:
        solo = pu.provision(conn, f"solo-{tag}@example.test", f"sub-solo-{tag}", ISSUER)
    # The operator: whichever tenant id is named is refused, before any delete.
    with owner.begin() as conn:
        with pytest.raises(pu.Refused, match="operator"):
            pu.revoke(conn, f"sub-solo-{tag}", ISSUER, operator_tenant_id=solo.tenant_id)
    # A second member in the tenant makes it shared; the script will not guess.
    with owner.begin() as conn:
        _bound(conn, solo.tenant_id)
        extra_user = int(conn.execute(text("SELECT nextval('users_id_seq')")).scalar_one())
        _bound(conn, user_id=extra_user)
        conn.execute(
            text("INSERT INTO users (id, email, created_at) VALUES (:id, :e, now())"),
            {"id": extra_user, "e": f"extra-{tag}@example.test"},
        )
        conn.execute(
            text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'member')"),
            {"t": solo.tenant_id, "u": extra_user},
        )
    with owner.begin() as conn:
        with pytest.raises(pu.Refused, match="2 members"):
            pu.revoke(conn, f"sub-solo-{tag}", ISSUER, operator_tenant_id=1)
    # Nothing was deleted by a refusal: the person still provisions idempotently.
    with owner.begin() as conn:
        assert pu.provision(conn, "x@example.test", f"sub-solo-{tag}", ISSUER).created is False


@needs_postgres
def test_the_command_line_prints_ids_then_counts_and_nothing_else(capsys):
    tag = uuid.uuid4().hex[:8]
    sub = f"sub-cli-{tag}"
    common = ["--issuer", ISSUER, "--url", OWNER_URL]
    assert pu.main([*common, "provision", f"cli-{tag}@example.test", sub]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    fields = dict(part.split("=") for part in out.strip().split(" "))
    assert set(fields) == {"tenant_id", "user_id", "membership_id"}
    assert all(value.isdigit() for value in fields.values())
    assert tag not in out, "the email and the subject appear in no output"
    assert pu.main([*common, "revoke", "--operator-tenant-id", "1", sub]) == 0
    out, err = capsys.readouterr()
    counts = dict(part.split("=") for part in out.strip().split(" "))
    assert counts["tenants"] == "1" and counts["users"] == "1" and counts["identities"] == "1"
    assert tag not in out and err == ""
    assert pu.main([*common, "revoke", sub]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "refused: no identity" in err and tag not in err


def test_the_postgres_half_says_why_it_skips():
    """The skip reason names the variables, so a green run without them is not
    mistaken for a proof."""
    assert "PROVISION_PG_OWNER_URL" in needs_postgres.kwargs["reason"]
    if not (OWNER_URL and APP_URL):
        print("\nprovision_user PostgreSQL tests skipped: " + needs_postgres.kwargs["reason"])
