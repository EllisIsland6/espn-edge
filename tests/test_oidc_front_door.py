"""The front door, against a provider that exists only in this file.

A real RSA key pair is generated once per module; the provider's discovery
document, JWKS and token endpoint are served by `respx`, which intercepts
`httpx` and nothing else -- so a verification path that reached the real
network would fail here rather than silently succeed.

The tests are grouped by what each check protects against, and each negative
case is a control for the positive one: the SAME token, with one thing
changed, must be refused, and no session row may exist afterwards. "No
session row" is asserted directly, because a 401 whose handler had already
minted would be the defect that matters most.
"""
from __future__ import annotations

import itertools
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api import oidc
from api.auth import COOKIE_NAME
from api.config import Settings
from api.db import SessionLocal
from api.main import app
from api.models import AppSession, Identity, Membership, Tenant, User
from api.routers.auth import FLOW_COOKIE, NOT_PROVISIONED
from api.security import CSRF_COOKIE

ISSUER = "https://issuer.test"
CLIENT_ID = "edge-client"
CLIENT_SECRET = "edge-secret"
REDIRECT = "https://app.test/api/auth/callback"
KID = "key-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_other_pem = _other_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key(), as_dict=True) | {
    "kid": KID,
    "use": "sig",
    "alg": "RS256",
}

_seq = itertools.count()


def _hosted(monkeypatch, **overrides) -> None:
    values = dict(
        app_mode="public_synthetic",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri=REDIRECT,
    )
    values.update(overrides)
    monkeypatch.setattr("api.config.get_settings", lambda: Settings(**values), raising=True)


def _token(sub: str = "subject-never-provisioned", **overrides) -> str:
    key = overrides.pop("_key", _pem)
    kid = overrides.pop("_kid", KID)
    alg = overrides.pop("_alg", "RS256")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": sub,
        "iat": now,
        "exp": now + 300,
        "nonce": "nonce-1",
        "token_use": "id",
    }
    claims.update(overrides)
    for name in [k for k, v in claims.items() if v is None]:
        del claims[name]
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture(autouse=True)
def _fresh_caches():
    oidc._reset()
    yield
    oidc._reset()


@pytest.fixture
def provider():
    """The provider. Yields the respx router so tests can tamper with it."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        )
        router.get(f"{ISSUER}/jwks").mock(return_value=httpx.Response(200, json={"keys": [_jwk]}))
        router.token = router.post(f"{ISSUER}/token").mock(
            return_value=httpx.Response(200, json={"id_token": _token()})  # unprovisioned
        )
        yield router


def _provision(*, membership: bool = True) -> tuple[int, str]:
    """A user with an identity, and (by default) a membership. Subjects and
    emails are unique per call: the module shares one database, and two tests
    provisioning the same subject collide on the second, which reads as a
    refusal and is not."""
    n = next(_seq)
    subject = f"subject-{n}"
    session = SessionLocal()
    try:
        tenant_id = session.query(Tenant).one().id
        user = User(email=f"oidc{n}@example.test")
        session.add(user)
        session.flush()
        if membership:
            session.add(Membership(tenant_id=tenant_id, user_id=user.id, role="member"))
        session.add(Identity(issuer=ISSUER, subject=subject, user_id=user.id))
        session.commit()
        return user.id, subject
    finally:
        session.close()


def _arm(provider, token: str) -> None:
    """What the token endpoint hands back next."""
    provider.token.mock(return_value=httpx.Response(200, json={"id_token": token}))


def _sessions_for(user_id: int | None = None) -> int:
    session = SessionLocal()
    try:
        query = select(func.count()).select_from(AppSession).where(AppSession.origin == "oidc")
        if user_id is not None:
            query = query.where(AppSession.user_id == user_id)
        return session.execute(query).scalar_one()
    finally:
        session.close()


def _client() -> TestClient:
    """Over https, or the browser-faithful `Secure` session cookie the callback
    sets would never be sent back and every post-login assertion would read
    as an authentication failure. (That is also why this is a helper and not
    an inline `TestClient(app)`: the default base URL is plain http.)"""
    return TestClient(app, base_url="https://testserver")


def _callback(client: TestClient, *, state: str = "state-1", nonce: str = "nonce-1", code="code"):
    client.cookies.set(FLOW_COOKIE, f"{state}.{nonce}", path="/api/auth")
    return client.get(
        "/api/auth/callback", params={"code": code, "state": "state-1"}, follow_redirects=False
    )


# ------------------------------------------------------------------ /login

def test_login_redirects_to_the_provider_with_fresh_state_and_nonce(monkeypatch, provider):
    _hosted(monkeypatch)
    with _client() as client:
        response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    location = httpx.URL(response.headers["location"])
    assert str(location).startswith(f"{ISSUER}/authorize?")
    params = dict(location.params)
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT
    assert params["scope"] == "openid"
    flow = oidc.Flow.decode(response.cookies.get(FLOW_COOKIE))
    assert flow is not None
    assert params["state"] == flow.state and params["nonce"] == flow.nonce
    assert len(flow.state) >= 32 and len(flow.nonce) >= 32
    assert "HttpOnly" in response.headers["set-cookie"]


def test_two_logins_do_not_share_secrets(monkeypatch, provider):
    _hosted(monkeypatch)
    with _client() as client:
        a = client.get("/api/auth/login", follow_redirects=False).cookies.get(FLOW_COOKIE)
        b = client.get("/api/auth/login", follow_redirects=False).cookies.get(FLOW_COOKIE)
    assert a != b


def test_login_is_404_in_private_operator_mode(monkeypatch):
    monkeypatch.setattr(
        "api.config.get_settings", lambda: Settings(app_mode="private_operator"), raising=True
    )
    with _client() as client:
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 404
        assert client.get("/api/auth/callback", follow_redirects=False).status_code == 404


def test_login_is_503_when_the_provider_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        "api.config.get_settings", lambda: Settings(app_mode="public_synthetic"), raising=True
    )
    with _client() as client:
        response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 503


def test_a_partially_configured_provider_is_refused_at_construction():
    with pytest.raises(ValueError, match="partially configured"):
        Settings(app_mode="public_synthetic", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID)


# --------------------------------------------------------------- /callback

def test_a_provisioned_subject_gets_a_session_and_a_csrf_cookie(monkeypatch, provider):
    user_id, subject = _provision()
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        response = _callback(client)
        assert response.status_code == 302, response.text
        assert response.headers["location"] == "/"
        assert response.cookies.get(COOKIE_NAME)
        assert response.cookies.get(CSRF_COOKIE)
        # The flow cookie is spent.
        assert any(
            FLOW_COOKIE in h and "Max-Age=0" in h for h in response.headers.get_list("set-cookie")
        )
        # And the session is real: /me answers with it.
        me = client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["user_id"] == user_id
    assert _sessions_for(user_id) == 1
    assert provider.token.called, "the code was never exchanged, so nothing was verified"


def test_the_code_is_exchanged_with_the_client_secret(monkeypatch, provider):
    _, subject = _provision()
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        _callback(client)
    request = provider.token.calls.last.request
    assert request.headers.get("authorization", "").startswith("Basic ")
    body = dict(httpx.QueryParams(request.content.decode()))
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "code"
    assert body["redirect_uri"] == REDIRECT


def test_an_unprovisioned_subject_is_refused_and_nothing_is_minted(monkeypatch, provider):
    _hosted(monkeypatch)
    before = _sessions_for()
    with _client() as client:
        response = _callback(client)
    assert response.status_code == 403
    assert response.json()["detail"] == NOT_PROVISIONED
    assert "subject-never-provisioned" not in response.text
    assert _sessions_for() == before


def test_a_provisioned_subject_without_a_membership_is_refused(monkeypatch, provider):
    user_id, subject = _provision(membership=False)
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        response = _callback(client)
    assert response.status_code == 403
    assert _sessions_for(user_id) == 0


@pytest.mark.parametrize(
    "state,nonce,code",
    [
        ("wrong-state", "nonce-1", "code"),   # state mismatch: CSRF on the callback
        ("state-1", "nonce-1", ""),           # no code
    ],
)
def test_a_bad_flow_is_400_and_mints_nothing(monkeypatch, provider, state, nonce, code):
    user_id, subject = _provision()
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        response = _callback(client, state=state, nonce=nonce, code=code)
    assert response.status_code == 400
    assert _sessions_for(user_id) == 0
    assert not provider.token.called


def test_a_missing_flow_cookie_is_400(monkeypatch, provider):
    user_id, subject = _provision()
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        response = client.get(
            "/api/auth/callback", params={"code": "c", "state": "s"}, follow_redirects=False
        )
    assert response.status_code == 400
    assert _sessions_for(user_id) == 0


# ------------------------------------------------- the token, one thing wrong

def _refuse(monkeypatch, provider, make_token, *, status: int = 401):
    """`make_token(subject)` builds the tampered token for a freshly
    provisioned subject, so the only thing wrong with it is the one thing the
    test changed."""
    user_id, subject = _provision()
    _hosted(monkeypatch)
    _arm(provider, make_token(subject))
    with _client() as client:
        response = _callback(client)
    assert response.status_code == status, response.text
    assert _sessions_for(user_id) == 0
    # The reason stays on the server: no check name leaks into the body.
    assert "nonce" not in response.text and "signature" not in response.text


def test_the_unmodified_token_is_the_control(monkeypatch, provider):
    """Every refusal below edits one field of THIS token. If this did not
    mint, the refusals would be measuring nothing."""
    user_id, subject = _provision()
    _hosted(monkeypatch)
    _arm(provider, _token(sub=subject))
    with _client() as client:
        assert _callback(client).status_code == 302
    assert _sessions_for(user_id) == 1


def test_a_token_signed_by_another_key_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, _key=_other_pem))


def test_a_token_with_an_unknown_kid_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, _kid="key-9"))


def test_alg_none_is_refused(monkeypatch, provider):
    """The family of bugs the module docstring names. `jwt.encode` refuses to
    produce `alg: none` with a key, so the token is assembled by hand."""
    import base64
    import json

    def b64(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def make(subject: str) -> str:
        now = int(time.time())
        header = b64({"alg": "none", "typ": "JWT", "kid": KID})
        payload = b64(
            {"iss": ISSUER, "aud": CLIENT_ID, "sub": subject, "iat": now, "exp": now + 300,
             "nonce": "nonce-1"}
        )
        return f"{header}.{payload}."

    _refuse(monkeypatch, provider, make)


def test_an_hs256_token_signed_with_the_public_key_is_refused(monkeypatch, provider):
    """Key confusion: sign with HMAC using the public key as the secret."""
    public_pem = _key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # PyJWT refuses to HMAC with a PEM public key by default; build it raw.
    import base64
    import hashlib
    import hmac as _hmac
    import json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def make(subject: str) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": subject, "iat": now,
                  "exp": now + 300, "nonce": "nonce-1"}
        signing_input = (
            b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
            + "."
            + b64(json.dumps(claims).encode())
        )
        sig = _hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{b64(sig)}"

    _refuse(monkeypatch, provider, make)


def test_the_wrong_audience_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, aud="someone-else"))


def test_the_wrong_issuer_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, iss="https://other.test"))


def test_an_expired_token_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, exp=int(time.time()) - 600))


def test_a_token_missing_a_required_claim_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, iat=None))


def test_the_wrong_nonce_is_refused(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, nonce="someone-elses-flow"))


def test_an_access_token_is_not_an_id_token(monkeypatch, provider):
    _refuse(monkeypatch, provider, lambda s: _token(sub=s, token_use="access"))


def test_a_token_endpoint_error_is_401_and_mints_nothing(monkeypatch, provider):
    user_id, _ = _provision()
    _hosted(monkeypatch)
    provider.token.mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with _client() as client:
        assert _callback(client).status_code == 401
    assert _sessions_for(user_id) == 0


def test_a_discovery_document_for_another_issuer_is_refused(monkeypatch, provider):
    user_id, _ = _provision()
    _hosted(monkeypatch)
    provider.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(
            200,
            json={
                "issuer": "https://other.test",
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            },
        )
    )
    with _client() as client:
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 503
        assert _callback(client).status_code == 401
    assert _sessions_for(user_id) == 0


# ------------------------------------------------------------ instruments

def test_the_provider_is_consulted_through_httpx_only(monkeypatch, provider):
    """If any fetch bypassed httpx, respx would not see it and this count
    would be short -- and in production that fetch would ignore the proxy."""
    _, subject = _provision()
    _arm(provider, _token(sub=subject))
    _hosted(monkeypatch)
    with _client() as client:
        _callback(client)
    routes_called = {str(c.request.url.path) for c in provider.calls}
    assert routes_called >= {"/.well-known/openid-configuration", "/jwks", "/token"}, routes_called


def test_the_session_expiry_is_bounded():
    """mint_session's default TTL applies to OIDC sessions too."""
    from api.auth import DEFAULT_TTL

    assert DEFAULT_TTL <= timedelta(hours=24)
    assert datetime.now(UTC) + DEFAULT_TTL > datetime.now(UTC)
