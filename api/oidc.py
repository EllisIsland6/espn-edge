"""The OIDC front door: discovery, the authorization redirect, and ID-token
verification. Everything that talks to the identity provider lives here, and
nothing here knows about tenants.

Phase 40's division is kept exactly: a verified `sub` proves WHO; `auth.py`
mints the application's own session for that user; `tenancy.py` decides WHICH
TENANT from the membership. An identity provider's groups, roles or access
tokens never reach the authorization decision, because they are never read.

WHAT IS VERIFIED, AND WITH WHAT
-------------------------------
The ID token's signature against the provider's JWKS (RS256 only -- `alg` is
pinned by the caller, never read from the token, which is the whole `alg:
none` family closed in one line); its `iss` against the configured issuer;
its `aud` against the client id; `exp`/`iat` with a small leeway; the `nonce`
against the one this flow generated; and, when present, Cognito's `token_use`,
which must be `id` -- an access token is a different artefact and must not
open a session. Every one of those has a test that removes it.

NETWORK
-------
Three fetches, all through `httpx` and none through anything else, so that
tests can stand up a provider with `respx` and the production proxy settings
apply uniformly: the discovery document, the JWKS, and the code exchange.
`PyJWKClient` was NOT used for the JWKS because it fetches with `urllib`,
which the test double cannot intercept -- a verification path that silently
reached the real internet from the suite would be a worse defect than the one
being fixed. Discovery and JWKS are cached per issuer for the life of the
process; `_reset()` exists for tests.

The code exchange authenticates with `client_secret_basic`, the one method
every provider supports.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from . import config

#: Algorithms accepted for the ID token. A list of one, passed to the decoder,
#: is what makes `alg: none` and the HMAC-with-public-key confusion impossible:
#: the decoder never consults the token's own header to choose.
ALGORITHMS = ("RS256",)

#: Clock skew tolerated on `exp`/`iat`/`nbf`. Thirty seconds is generous for
#: two systems on NTP and far too small to matter for a stolen token.
LEEWAY_SECONDS = 30

#: Claims a token must carry to be considered at all. A token missing any one
#: of these is malformed for this purpose, whatever else it says.
REQUIRED_CLAIMS = ("exp", "iat", "sub", "aud", "iss")

_TIMEOUT = httpx.Timeout(10.0)

_lock = threading.Lock()
_discovery: dict[str, dict[str, Any]] = {}
_jwks: dict[str, dict[str, Any]] = {}


class OidcError(Exception):
    """Any failure between "redirect the user" and "this is subject S".

    One exception for every cause. `reason` is for logs and tests; the HTTP
    response that results carries fixed text, because telling a caller WHICH
    check their token failed is a free hint to whoever is forging one.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Identity:
    """What a verified ID token establishes, and nothing more."""

    issuer: str
    subject: str


@dataclass(frozen=True, slots=True)
class Flow:
    """The two single-use secrets of one login attempt.

    `state` binds the callback to the browser that started the flow (CSRF on
    the callback); `nonce` binds the ID token to this flow (replay of a token
    issued to someone else's flow). They are different threats and they are
    checked at different points, which is why there are two of them.
    """

    state: str
    nonce: str

    @classmethod
    def new(cls) -> Flow:
        return cls(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32))

    def encode(self) -> str:
        return f"{self.state}.{self.nonce}"

    @classmethod
    def decode(cls, raw: str | None) -> Flow | None:
        if not raw or raw.count(".") != 1:
            return None
        state, nonce = raw.split(".", 1)
        if not state or not nonce:
            return None
        return cls(state=state, nonce=nonce)


def is_configured() -> bool:
    return config.get_settings().is_oidc_configured


def _settings():
    s = config.get_settings()
    if not s.is_oidc_configured:
        raise OidcError("not_configured")
    return s


def _reset() -> None:
    """Drop the per-issuer caches. Tests only."""
    with _lock:
        _discovery.clear()
        _jwks.clear()


def _get_json(url: str) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"fetch_failed:{type(exc).__name__}") from exc
    if not isinstance(body, dict):
        raise OidcError("fetch_failed:not_an_object")
    return body


def discovery(issuer: str) -> dict[str, Any]:
    """The provider's configuration document, cached per issuer.

    The document's own `issuer` must equal the one configured -- the
    specification requires it, and it is the check that stops a misconfigured
    `OIDC_ISSUER` pointing at one provider's discovery while trusting another's
    tokens.
    """
    with _lock:
        cached = _discovery.get(issuer)
    if cached is not None:
        return cached
    doc = _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
    if doc.get("issuer") != issuer:
        raise OidcError("discovery_issuer_mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise OidcError(f"discovery_missing:{key}")
    with _lock:
        _discovery[issuer] = doc
    return doc


def _signing_keys(issuer: str, jwks_uri: str, *, refresh: bool = False) -> jwt.PyJWKSet:
    with _lock:
        cached = None if refresh else _jwks.get(issuer)
    if cached is None:
        cached = _get_json(jwks_uri)
        with _lock:
            _jwks[issuer] = cached
    try:
        return jwt.PyJWKSet.from_dict(cached)
    except jwt.PyJWTError as exc:
        raise OidcError("jwks_invalid") from exc


def authorization_url(flow: Flow) -> str:
    """Where to send the browser."""
    s = _settings()
    doc = discovery(s.oidc_issuer)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": s.oidc_redirect_uri,
            "scope": "openid",
            "state": flow.state,
            "nonce": flow.nonce,
        }
    )
    return f"{doc['authorization_endpoint']}?{query}"


def exchange_code(code: str) -> str:
    """Trade the authorization code for the ID token. Returns the raw token."""
    s = _settings()
    doc = discovery(s.oidc_issuer)
    try:
        response = httpx.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.oidc_redirect_uri,
                "client_id": s.oidc_client_id,
            },
            auth=(s.oidc_client_id, s.oidc_client_secret),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise OidcError(f"token_endpoint_unreachable:{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise OidcError(f"token_endpoint_status:{response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise OidcError("token_endpoint_not_json") from exc
    token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise OidcError("token_response_missing_id_token")
    return token


def verify_id_token(raw_token: str, *, nonce: str) -> Identity:
    """Every check, in the order a forged token would fail them.

    The key is chosen by the token's `kid` from the provider's JWKS; a `kid`
    the set does not hold triggers ONE refresh (key rotation) and then fails.
    The algorithm is pinned, not read. Audience and issuer are required to
    match exactly. Then the nonce, in constant time.
    """
    s = _settings()
    doc = discovery(s.oidc_issuer)
    try:
        header = jwt.get_unverified_header(raw_token)
    except jwt.PyJWTError as exc:
        raise OidcError("token_malformed") from exc
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise OidcError("token_no_kid")

    key = None
    for refresh in (False, True):
        keyset = _signing_keys(s.oidc_issuer, doc["jwks_uri"], refresh=refresh)
        try:
            key = keyset[kid]
            break
        except KeyError:
            continue
    if key is None:
        raise OidcError("token_unknown_kid")

    try:
        claims = jwt.decode(
            raw_token,
            key=key.key,
            algorithms=list(ALGORITHMS),
            audience=s.oidc_client_id,
            issuer=s.oidc_issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError as exc:
        raise OidcError(f"token_rejected:{type(exc).__name__}") from exc

    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise OidcError("nonce_mismatch")
    if claims.get("token_use", "id") != "id":
        raise OidcError("not_an_id_token")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise OidcError("token_no_sub")
    return Identity(issuer=s.oidc_issuer, subject=subject)
