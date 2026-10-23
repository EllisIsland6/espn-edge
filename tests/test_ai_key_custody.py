"""ADR-012 gate G7 -- a tenant's own AI key, held the way its ESPN session is.

The operator's decision (2026-10-23): *they pay for their own AI API, and we
store what's needed and practical, per best practice.* These tests hold that
sentence to its words.

The offline half runs on SQLite and proves the shape: stored encrypted, never
echoed, refused on the synthetic site through the same chokepoint as an ESPN
session, verified with the provider before anything is written, erased on
DELETE, used by the service without the host's ledger and never through the
host's key, and absent from every log line. The PostgreSQL half proves the
isolation that SQLite cannot -- one tenant's key invisible to another through
the application role, and gone, not hidden, after DELETE -- and runs only where
`PROVISION_PG_OWNER_URL` / `PROVISION_PG_APP_URL` point at a migrated cluster.
"""

from __future__ import annotations

import itertools
import logging
import os

import pytest
from fastapi.testclient import TestClient

from api import crypto
from api.auth import COOKIE_NAME, mint_session
from api.config import Settings
from api.db import SessionLocal
from api.main import app
from api.models import AiCredential, Membership, Tenant, User
from api.security import CSRF_COOKIE, CSRF_HEADER
from api.services import ai as ai_module
from api.services import ai_keys
from api.services.ai import AiDisabledError, AiError, AiService

KEY = "sk-ant-api03-this-is-a-test-value-and-nothing-real-0001"
HOST_KEY = "sk-ant-api03-the-hosts-key-which-the-invited-site-never-uses"
CSRF = "matching-token"

_user_seq = itertools.count()


def _mode(monkeypatch, mode: str, **overrides) -> None:
    """Both bindings that matter: `_bound_session` and the chokepoint read
    `api.config.get_settings` at call time; `AiService` bound its own copy at
    import. `raising=True` so a typo cannot leave a test running in the
    operator's mode and passing for the wrong reason."""
    settings = Settings(app_mode=mode, **overrides)
    monkeypatch.setattr("api.config.get_settings", lambda: settings, raising=True)
    monkeypatch.setattr("api.services.ai.get_settings", lambda: settings, raising=True)


def _invited(monkeypatch, **overrides) -> None:
    _mode(monkeypatch, "private_invited", **overrides)


def _person() -> tuple[int, str]:
    session = SessionLocal()
    try:
        tenant_id = session.query(Tenant).one().id
        user = User(email=f"invited{next(_user_seq)}@example.test")
        session.add(user)
        session.flush()
        session.add(Membership(tenant_id=tenant_id, user_id=user.id, role="member"))
        token = mint_session(session, user.id, origin="test")
        session.commit()
        return user.id, token
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _no_key_on_file():
    """Each test starts and ends with no key stored. The module does not use
    the schema-resetting `db_session` fixture, so rows would otherwise carry
    from one test into the next and a "stores nothing" assertion would be
    reading the previous test's row."""

    def clear():
        session = SessionLocal()
        try:
            session.query(AiCredential).delete()
            session.commit()
        finally:
            session.close()

    clear()
    yield
    clear()


@pytest.fixture
def client():
    _user_id, token = _person()
    with TestClient(app, raise_server_exceptions=False) as c:
        c.cookies.set(COOKIE_NAME, token)
        c.cookies.set(CSRF_COOKIE, CSRF)
        yield c


@pytest.fixture
def verified(monkeypatch):
    """The provider says yes, and records what it was asked."""
    asked: list[str] = []
    monkeypatch.setattr(ai_keys, "verify", lambda key: asked.append(key))
    return asked


def _put(client, key=KEY):
    return client.put("/api/ai/key", json={"api_key": key}, headers={CSRF_HEADER: CSRF})


def _rows() -> list[AiCredential]:
    session = SessionLocal()
    try:
        return list(session.query(AiCredential).all())
    finally:
        session.close()


# ------------------------------------------------------------ the shape


def test_the_key_is_stored_encrypted_and_never_echoed(monkeypatch, client, verified):
    _invited(monkeypatch)
    response = _put(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "anthropic" and body["hint"] == KEY[-4:]
    assert set(body) == {
        "provider",
        "hint",
        "created_at",
        "updated_at",
        "last_used_at",
        "last_error",
    }
    assert KEY not in response.text, "the key came back in the response"
    assert verified == [KEY], "the provider was not asked, or was asked something else"
    (row,) = _rows()
    assert row.api_key_encrypted != KEY and KEY not in row.api_key_encrypted
    assert crypto.decrypt(row.api_key_encrypted) == KEY
    assert row.key_hint == KEY[-4:] and row.last_used_at is None and row.last_error is None
    read = client.get("/api/ai/key")
    assert read.status_code == 200 and read.json()["hint"] == KEY[-4:]
    assert KEY not in read.text


def test_a_second_put_replaces_the_one_row(monkeypatch, client, verified):
    _invited(monkeypatch)
    assert _put(client).status_code == 200
    other = KEY[:-4] + "9999"
    assert _put(client, other).status_code == 200
    (row,) = _rows()
    assert crypto.decrypt(row.api_key_encrypted) == other and row.key_hint == "9999"
    assert verified == [KEY, other]


def test_a_refused_key_stores_nothing(monkeypatch, client):
    _invited(monkeypatch)

    def refuse(_key):
        raise ai_keys.AiKeyRefused()

    monkeypatch.setattr(ai_keys, "verify", refuse)
    response = _put(client)
    assert response.status_code == 400
    assert response.json() == {"detail": "the key was refused by Anthropic"}
    assert KEY not in response.text
    assert _rows() == []
    assert client.get("/api/ai/key").json() is None


def test_an_unverifiable_key_stores_nothing_and_says_so(monkeypatch, client):
    _invited(monkeypatch)

    def unreachable(_key):
        raise ai_keys.AiKeyUnverifiable()

    monkeypatch.setattr(ai_keys, "verify", unreachable)
    response = _put(client)
    assert response.status_code == 503
    assert "nothing was stored" in response.json()["detail"]
    assert _rows() == []


def test_the_synthetic_instance_refuses_custody_through_the_same_chokepoint(
    monkeypatch, client, verified
):
    """`crypto.encrypt` runs the hosted-mode guard before anything else, so
    the synthetic site answers the fixed 403 an ESPN session gets, asks the
    provider nothing, and writes nothing."""
    _mode(monkeypatch, "public_synthetic")
    response = _put(client)
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "hosted synthetic mode forbids credential custody"}
    assert verified == [] and _rows() == []
    assert client.get("/api/ai/key").json() is None


def test_a_malformed_body_gets_the_credential_neutral_422(monkeypatch, client, verified):
    """FastAPI's default 422 echoes the submitted input. For the one body that
    carries a provider key, `api/main.py` answers the same value-free 422 the
    account endpoints get."""
    _invited(monkeypatch)
    response = client.put("/api/ai/key", json={"api_kee": KEY}, headers={CSRF_HEADER: CSRF})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid account request"}
    assert KEY not in response.text and "api_kee" not in response.text
    assert verified == [] and _rows() == []


def test_a_mutation_without_the_csrf_header_is_refused(monkeypatch, client, verified):
    _invited(monkeypatch)
    response = client.put("/api/ai/key", json={"api_key": KEY})
    assert response.status_code == 403
    assert verified == [] and _rows() == []


def test_delete_erases_the_row(monkeypatch, client, verified):
    _invited(monkeypatch)
    assert _put(client).status_code == 200
    assert client.delete("/api/ai/key", headers={CSRF_HEADER: CSRF}).status_code == 204
    assert _rows() == []
    assert client.get("/api/ai/key").json() is None
    assert client.delete("/api/ai/key", headers={CSRF_HEADER: CSRF}).status_code == 404


def test_ai_status_is_a_fact_about_the_tenants_key_on_the_invited_site(
    monkeypatch, client, verified
):
    """Even with a host key configured, the invited site reports AI absent
    until THIS tenant stores a key; the host's key is never what enables it."""
    _invited(monkeypatch, anthropic_api_key=HOST_KEY)
    assert client.get("/api/ai/status").json()["enabled"] is False
    assert _put(client).status_code == 200
    assert client.get("/api/ai/status").json()["enabled"] is True
    assert client.delete("/api/ai/key", headers={CSRF_HEADER: CSRF}).status_code == 204
    assert client.get("/api/ai/status").json()["enabled"] is False


# ------------------------------------------------------------ the service


class _Recorder:
    """Stands in for `AnthropicLlmClient`: records how it was built, answers
    a valid report, or raises when told to."""

    built: list[tuple[str, bool]] = []
    fail: Exception | None = None

    def __init__(self, api_key: str, *, spend_is_the_hosts: bool = True):
        _Recorder.built.append((api_key, spend_is_the_hosts))
        self.last_usage = None

    def complete_json(self, *, model, system, user, schema, max_tokens, reservation=None):
        if _Recorder.fail is not None:
            raise _Recorder.fail
        return {
            "headline": "a grounded headline",
            "body": "a grounded body",
            "luck_notes": [],
            "waiver_highlights": [],
        }


@pytest.fixture
def recorder(monkeypatch):
    _Recorder.built = []
    _Recorder.fail = None
    monkeypatch.setattr(ai_module, "AnthropicLlmClient", _Recorder)

    def never(*_a, **_k):
        raise AssertionError("the host's ledger was consulted for a tenant's own spend")

    monkeypatch.setattr(ai_module.SpendLedger, "reserve", never)
    return _Recorder


def _bound_session():
    from api.tenancy import bind_session, resolve_tenant_id

    session = SessionLocal()
    bind_session(session, resolve_tenant_id(session))
    return session


def _league(session) -> int:
    """A report needs a league row to hang on; the facts are fake anyway."""
    from api.models import League
    from api.tenancy import current_tenant_id

    league = League(
        espn_league_id=f"keys-{next(_user_seq)}",
        season=2026,
        lifecycle="active",
        is_public=True,
        tenant_id=current_tenant_id(session),
    )
    session.add(league)
    session.commit()
    return league.id


def _store(session, key=KEY):
    ai_keys.verify = lambda _k: None
    ai_keys.store_key(session, key)
    session.commit()


def test_the_service_uses_the_tenants_key_and_not_the_hosts_ledger(monkeypatch, recorder):
    _invited(monkeypatch, anthropic_api_key=HOST_KEY)
    session = _bound_session()
    try:
        svc = AiService(session)
        assert svc.enabled is False, "no key on file yet"
        with pytest.raises(AiDisabledError):
            svc._ensure_client()
        assert recorder.built == [], "a client was built with no tenant key -- the host's?"
        _store(session)
        league_id = _league(session)
        svc = AiService(session)
        assert svc.enabled is True
        content = svc.generate(
            league_id=league_id, kind="weekly_recap", scope="week", model="m", facts={"week": 1}
        )
        assert content["headline"] == "a grounded headline"
        assert recorder.built == [(KEY, False)], recorder.built
        session.commit()
        row = session.query(AiCredential).one()
        assert row.last_used_at is not None and row.last_error is None
    finally:
        session.close()


def test_a_failed_call_records_the_class_and_never_the_message(monkeypatch, recorder):
    _invited(monkeypatch)
    session = _bound_session()
    try:
        _store(session)
        league_id = _league(session)
        recorder.fail = AiError(f"the provider said something that mentions {KEY}")
        svc = AiService(session)
        with pytest.raises(AiError):
            svc.generate(
                league_id=league_id, kind="weekly_recap", scope="week", model="m", facts={"week": 2}
            )
        session.rollback()
    finally:
        session.close()
    (row,) = _rows()
    assert row.last_error == "AiError" and row.last_used_at is None
    assert KEY not in (row.last_error or "")


def test_the_other_two_modes_keep_the_host_key_and_the_ledger(monkeypatch):
    """The control: outside the invited site nothing changed. The operator's
    key from the environment, the host ledger's predicate, no tenant lookup."""
    for mode in ("private_operator", "public_synthetic"):
        _mode(monkeypatch, mode, anthropic_api_key=HOST_KEY)
        svc = AiService(session=None)
        assert svc.enabled is True
        assert svc._tenant_keyed is False and svc._api_key == HOST_KEY
        assert svc._spend_bound_required() is ai_module._spend_bound_required()


def test_nothing_of_the_key_reaches_the_log(monkeypatch, client, verified, recorder, caplog):
    caplog.set_level(logging.DEBUG)
    _invited(monkeypatch)
    assert _put(client).status_code == 200
    session = _bound_session()
    try:
        league_id = _league(session)
        recorder.fail = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            AiService(session).generate(
                league_id=league_id, kind="weekly_recap", scope="week", model="m", facts={"week": 3}
            )
    finally:
        session.rollback()
        session.close()
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert all(KEY not in str(a) for a in (record.args or ()))
    assert KEY not in caplog.text


def test_the_production_verifier_treats_a_refusal_and_an_outage_differently(monkeypatch):
    """The real verifier, against a fake transport: 401 is a refusal; a 5xx
    or a network failure is "could not verify", which stores nothing either
    but tells the person to try again rather than that the key is wrong. The
    socket guard in conftest makes the real network impossible here."""
    import httpx

    calls: list[dict] = []

    def fake_get(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        status = fake_get.status
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_keys.httpx, "get", fake_get)
    fake_get.status = 200
    ai_keys.verify_with_anthropic(KEY)
    assert calls[-1]["url"] == ai_keys.MODELS_URL
    assert calls[-1]["headers"]["x-api-key"] == KEY
    assert "anthropic-version" in calls[-1]["headers"]
    fake_get.status = 401
    with pytest.raises(ai_keys.AiKeyRefused):
        ai_keys.verify_with_anthropic(KEY)
    fake_get.status = 503
    with pytest.raises(ai_keys.AiKeyUnverifiable):
        ai_keys.verify_with_anthropic(KEY)
    fake_get.status = httpx.ConnectError("no route")
    with pytest.raises(ai_keys.AiKeyUnverifiable):
        ai_keys.verify_with_anthropic(KEY)


def test_the_credential_endpoint_predicate_names_the_put_and_nothing_else():
    from api.main import _is_account_credential_endpoint

    class _Req:
        def __init__(self, method, path):
            self.method = method

            class _Url:
                pass

            self.url = _Url()
            self.url.path = path

    assert _is_account_credential_endpoint(_Req("PUT", "/api/ai/key")) is True
    assert _is_account_credential_endpoint(_Req("GET", "/api/ai/key")) is False
    assert _is_account_credential_endpoint(_Req("DELETE", "/api/ai/key")) is False
    assert _is_account_credential_endpoint(_Req("PUT", "/api/accounts")) is False
    assert _is_account_credential_endpoint(_Req("POST", "/api/accounts")) is True


# ------------------------------------------------------------ PostgreSQL

OWNER_URL = os.environ.get("PROVISION_PG_OWNER_URL", "")
APP_URL = os.environ.get("PROVISION_PG_APP_URL", "")
SUPER_URL = os.environ.get("PROVISION_PG_SUPER_URL", "")

needs_postgres = pytest.mark.skipif(
    not (OWNER_URL and APP_URL),
    reason=(
        "row security is real only on PostgreSQL: set PROVISION_PG_OWNER_URL and "
        "PROVISION_PG_APP_URL to a cluster migrated to 0018"
    ),
)


@needs_postgres
def test_one_tenants_key_is_invisible_to_another_and_gone_when_deleted():
    """Two people provisioned as their own tenants (WU-3's script); each stores
    a key as the application role with its own tenant bound; each reads only
    its own; after one deletes, a superuser read shows the row gone, not
    hidden; revoking the other takes their key with them and counts it."""
    import importlib.util
    import sys
    from pathlib import Path

    from sqlalchemy import create_engine, text

    spec = importlib.util.spec_from_file_location(
        "provision_user_for_keys", Path(__file__).resolve().parents[1] / "ops" / "provision_user.py"
    )
    pu = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pu
    spec.loader.exec_module(pu)

    owner = create_engine(OWNER_URL, future=True)
    app_role = create_engine(APP_URL, future=True)
    superuser = create_engine(SUPER_URL, future=True) if SUPER_URL else None
    issuer = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_keys"
    tag = os.urandom(3).hex()
    try:
        with owner.begin() as conn:
            a = pu.provision(conn, f"keys-a-{tag}@example.test", f"sub-a-{tag}", issuer)
            b = pu.provision(conn, f"keys-b-{tag}@example.test", f"sub-b-{tag}", issuer)

        def bound(conn, tenant_id):
            conn.execute(
                text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)}
            )

        for done, hint in ((a, "aaaa"), (b, "bbbb")):
            with app_role.begin() as conn:
                bound(conn, done.tenant_id)
                conn.execute(
                    text(
                        "INSERT INTO ai_credentials (tenant_id, provider, api_key_encrypted, key_hint,"
                        " created_at, updated_at) VALUES (:t, 'anthropic', :c, :h, now(), now())"
                    ),
                    {"t": done.tenant_id, "c": f"ENC-{hint}", "h": hint},
                )
        # Each tenant sees exactly its own row, whatever it asks for.
        with app_role.connect() as conn:
            bound(conn, a.tenant_id)
            rows = conn.execute(text("SELECT tenant_id, key_hint FROM ai_credentials")).all()
            assert [(r[0], r[1]) for r in rows] == [(a.tenant_id, "aaaa")]
            other = conn.execute(
                text("SELECT count(*) FROM ai_credentials WHERE tenant_id = :t"), {"t": b.tenant_id}
            ).scalar_one()
            assert other == 0, "tenant A can see tenant B's key row"
        # Nothing bound: nothing visible (0005's NO CONTEXT = NO ROWS).
        with app_role.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM ai_credentials")).scalar_one() == 0
        # A cannot write into B's tenant.
        with app_role.connect() as conn:
            bound(conn, a.tenant_id)
            with pytest.raises(Exception) as caught:
                conn.execute(
                    text(
                        "INSERT INTO ai_credentials (tenant_id, provider, api_key_encrypted, key_hint,"
                        " created_at, updated_at) VALUES (:t, 'anthropic', 'ENC-x', 'xxxx', now(), now())"
                    ),
                    {"t": b.tenant_id},
                )
            assert "row-level security" in str(caught.value) or "duplicate key" in str(caught.value)
        # A deletes its key: gone, not hidden.
        with app_role.begin() as conn:
            bound(conn, a.tenant_id)
            deleted = conn.execute(
                text("DELETE FROM ai_credentials WHERE tenant_id = :t"), {"t": a.tenant_id}
            ).rowcount
            assert deleted == 1
        if superuser is not None:
            with superuser.connect() as conn:
                remaining = conn.execute(
                    text("SELECT tenant_id FROM ai_credentials WHERE tenant_id IN (:a, :b)"),
                    {"a": a.tenant_id, "b": b.tenant_id},
                ).all()
            assert [r[0] for r in remaining] == [b.tenant_id]
        # Revoking B takes B's key with them, and the count says so.
        with owner.begin() as conn:
            counts = pu.revoke(conn, f"sub-b-{tag}", issuer, operator_tenant_id=1)
        assert counts["ai_credentials"] == 1 and counts["tenants"] == 1
        with owner.begin() as conn:
            counts = pu.revoke(conn, f"sub-a-{tag}", issuer, operator_tenant_id=1)
        assert counts["ai_credentials"] == 0 and counts["tenants"] == 1
    finally:
        for engine in (owner, app_role, superuser):
            if engine is not None:
                engine.dispose()


def test_the_postgres_half_says_why_it_skips():
    """The skip reason names the variables, so a green run without them is not
    mistaken for a proof of isolation."""
    assert "PROVISION_PG_OWNER_URL" in needs_postgres.kwargs["reason"]
    if not (OWNER_URL and APP_URL):
        print("\nai key PostgreSQL test skipped: " + needs_postgres.kwargs["reason"])
