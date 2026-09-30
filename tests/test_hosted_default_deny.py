"""Hosted mode: every data route refuses an unauthenticated caller.

Phase 40's acceptance asks that routes default-deny. This is the half that can
be proven offline: **without a session cookie, nothing answers.** The other
half -- that an authenticated caller sees only their own tenant's rows -- is
row-level security's job, it is PostgreSQL-only, and it was measured in
`docs/sprint-9/kernel/login_e2e.py` (9/9, two tenants served at once).

Asserting isolation here, on SQLite, would be a green tick establishing
nothing. Asserting refusal here is real, because the refusal happens in the
dependency, before any handler runs.
"""

from __future__ import annotations

import inspect
import itertools

import pytest
from fastapi.testclient import TestClient

from api.auth import COOKIE_NAME, mint_session
from api.config import Settings
from api.db import SessionLocal, get_session
from api.main import app
from api.models import Membership, Tenant, User
from api.security import CSRF_COOKIE, CSRF_HEADER


def _hosted(monkeypatch) -> None:
    """Patch the binding `_bound_session` actually reads.

    It imports `get_settings` from `.config` inside the function body, so the
    lookup happens at call time and patching the module attribute reaches it.
    Patched with `raising=True`: a typo here would otherwise silently leave the
    app in private-operator mode and every assertion below would pass for the
    wrong reason.
    """
    monkeypatch.setattr(
        "api.config.get_settings",
        lambda: Settings(app_mode="public_synthetic"),
        raising=True,
    )


def _iter_routes(container):
    for route in getattr(container, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_routes(inner)
            continue
        yield route


def _authenticated_get_routes() -> list[tuple[str, str]]:
    """Every GET route that takes a tenant-bound session, with path params
    filled in. The values do not need to exist: a 401 is decided in the
    dependency, before the handler ever looks one up."""
    found = []
    for route in _iter_routes(app):
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not methods or endpoint is None or "GET" not in methods:
            continue
        try:
            params = inspect.signature(endpoint).parameters
        except (TypeError, ValueError):
            continue
        if not any(
            getattr(p.default, "dependency", None) is get_session for p in params.values()
        ):
            continue
        path = route.path
        for name in ("league_id", "team_id", "account_id", "espn_player_id"):
            path = path.replace("{" + name + "}", "1")
        found.append(("GET", path))
    return sorted(set(found))


ROUTES = _authenticated_get_routes()


def test_there_are_routes_to_check():
    """Guards the guard. An empty list makes every parametrised case below
    vanish, and a file with no tests reports as success."""
    assert len(ROUTES) >= 25, f"only {len(ROUTES)} routes discovered: {ROUTES}"


@pytest.mark.parametrize("method,path", ROUTES)
def test_hosted_mode_refuses_every_route_without_a_session(method, path, monkeypatch):
    _hosted(monkeypatch)
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} to an unauthenticated "
        f"caller in hosted mode"
    )


@pytest.mark.parametrize("method,path", ROUTES)
def test_hosted_mode_refuses_a_garbage_cookie(method, path, monkeypatch):
    """A cookie that is not a real token must be no better than no cookie."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "not-a-real-token")
        response = client.request(method, path)
    assert response.status_code == 401, f"{method} {path} accepted a forged cookie"


def test_private_operator_mode_still_serves_without_a_cookie():
    """The control. If these refused too, the test above would be measuring
    "the app is broken" rather than "hosted mode denies by default"."""
    with TestClient(app) as client:
        response = client.get("/api/portfolio")
    assert response.status_code == 200


_user_seq = itertools.count()


def _make_user_with_membership() -> tuple[int, str]:
    """`users.email` is unique, so each caller needs its own -- two tests
    sharing one address collide on the second, which reads as an auth failure
    and is not."""
    session = SessionLocal()
    try:
        tenant_id = session.query(Tenant).one().id
        user = User(email=f"hosted{next(_user_seq)}@example.test")
        session.add(user)
        session.flush()
        session.add(Membership(tenant_id=tenant_id, user_id=user.id, role="member"))
        token = mint_session(session, user.id, origin="test")
        session.commit()
        return user.id, token
    finally:
        session.close()


def test_a_real_session_gets_past_the_door(monkeypatch):
    """The other control: the 401s above are about authentication, not about
    hosted mode having broken every route."""
    user_id, token = _make_user_with_membership()
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == user_id


def test_logout_needs_a_csrf_token(monkeypatch):
    """Logout is state-changing, so it is CSRF-protected like anything else.

    The first version of this file called it without a token and passed --
    because `api/security.py` bound `get_settings` at import and the hosted
    patch never reached the middleware, leaving it inert. Measured and fixed;
    this case is what would catch it again."""
    _user_id, token = _make_user_with_membership()
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.post("/api/auth/logout").status_code == 403


def test_logout_revokes_the_session_it_was_called_with(monkeypatch):
    _user_id, token = _make_user_with_membership()
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        client.cookies.set(CSRF_COOKIE, "matching-token")
        assert client.post(
            "/api/auth/logout", headers={CSRF_HEADER: "matching-token"}
        ).status_code == 204
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/api/auth/me").status_code == 401, (
            "the session still worked after logout"
        )
