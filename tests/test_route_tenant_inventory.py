"""Every route is classified, or the build fails.

Phase 37's plan sizes the problem as **50 unowned data-returning routes**, and
its acceptance asks that all 50 default-deny across colliding tenants. Fifty
edits is fifty chances to miss one, and the one missed is the leak -- the same
shape as Phase 36's table audit, where an attack suite passed 13/13 while
three tenant-scoped tables had no policy at all, because an attack suite only
probes what the policies already cover.

So this is the structural version. Every route must either obtain its session
from `db.get_session` -- the seam that binds a tenant to the transaction -- or
appear below with a reason. A new route does neither by default, so adding one
fails this file until somebody decides which it is.

What this does NOT establish: that a bound route's data is actually isolated.
That is row-level security's job, it is PostgreSQL-only, and it was measured
in Phase 36. SQLite has no RLS, so an offline test asserting isolation would
be a green tick establishing nothing.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from api.db import engine, get_session
from api.main import app

#: Routes that take no database session at all, each with the reason. These
#: are not exemptions from tenancy -- they are routes with no tenant data to
#: scope, and `test_the_sessionless_routes_really_touch_no_database` is what
#: keeps that from being a claim anyone can make by editing this dict.
SESSIONLESS_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): "app root; static response",
    ("GET", "/docs"): "FastAPI's own Swagger UI",
    ("GET", "/docs/oauth2-redirect"): "FastAPI's own OAuth redirect shim",
    ("GET", "/openapi.json"): "FastAPI's own schema; describes routes, returns no data",
    ("GET", "/redoc"): "FastAPI's own ReDoc UI",
    ("GET", "/api/health"): "liveness; no query",
    ("GET", "/config.json"): "runtime SPA config; read before anyone logs in, nothing private",
    ("GET", "/api/ai/status"): "reads the AI feature flag and model names; AiService(session=None)",
    ("GET", "/api/players/team-logo/{team}"): "static NFL team logo asset",
    ("GET", "/api/players/{espn_player_id}/portrait"): "public ESPN headshot passthrough",
    ("GET", "/api/recovery/status"): "operator control plane; restic/launchd state, not app data",
    ("POST", "/api/recovery/backup"): "operator control plane; triggers a backup, no app data",
    ("GET", "/api/auth/login"): "starts a sign-in; redirects to the identity provider, reads nothing",
}

#: The third kind, and there is exactly one: a route that touches the
#: database BEFORE a tenant can be bound, because binding one is what it is
#: trying to achieve. Revision 0007 describes the deadlock and 0017 the
#: lookup table. Each entry names the only tables the route may touch, and
#: `test_the_pre_tenant_routes_touch_only_the_tables_they_declare` reads the
#: SQL the route actually issues -- so an entry here is a measurement of what
#: the route reads before anyone is authenticated, not a note about it.
PRE_TENANT_ROUTES: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/api/auth/callback"): frozenset({"identities", "memberships", "app_sessions"}),
}

#: Safe to actually call in a test: GET, and no side effect. The one excluded
#: is the backup POST, which would run restic.
_CALLABLE = {
    (method, path)
    for method, path in SESSIONLESS_ROUTES
    if method == "GET" and "{" not in path
}


def _iter_routes(container):
    for route in getattr(container, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_routes(inner)
            continue
        yield route


def _routes() -> dict[tuple[str, str], object]:
    found: dict[tuple[str, str], object] = {}
    for route in _iter_routes(app):
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not methods or endpoint is None:
            continue
        for method in set(methods) - {"HEAD", "OPTIONS"}:
            found[(method, route.path)] = endpoint
    return found


def _uses_get_session(endpoint) -> bool:
    try:
        parameters = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        return False
    return any(
        getattr(parameter.default, "dependency", None) is get_session
        for parameter in parameters.values()
    )


def test_the_route_table_is_actually_populated():
    """Guards the guard. This FastAPI version keeps included routers as nested
    `_IncludedRouter` objects rather than flattening them into `app.routes`, so
    the obvious walk finds five routes -- all of them FastAPI's own -- and
    every assertion below passes against a set that contains no API route at
    all. That is this project's most frequent defect, and it happened here
    while writing this file."""
    routes = _routes()
    assert len(routes) >= 40, (
        f"only {len(routes)} routes discovered; the walker is not descending "
        f"into the included routers. Found: {sorted(routes)}"
    )


def test_every_route_is_either_tenant_bound_or_declared_sessionless():
    """The tripwire. A new route fails this until someone decides."""
    unclassified = {
        key
        for key, endpoint in _routes().items()
        if not _uses_get_session(endpoint)
        and key not in SESSIONLESS_ROUTES
        and key not in PRE_TENANT_ROUTES
    }
    assert not unclassified, (
        f"these routes neither take a tenant-bound session nor are declared "
        f"sessionless: {sorted(unclassified)}. Either add "
        f"`session: Session = Depends(get_session)`, or record it in "
        f"SESSIONLESS_ROUTES with the reason it needs no tenant."
    )


def test_the_sessionless_list_names_no_route_that_stopped_existing():
    stale = set(SESSIONLESS_ROUTES) - set(_routes())
    assert not stale, f"SESSIONLESS_ROUTES names routes that no longer exist: {sorted(stale)}"


def test_the_sessionless_list_names_no_route_that_now_takes_a_session():
    """A route that gained a session should leave the list, or the list stops
    describing anything."""
    routes = _routes()
    promoted = {
        key
        for key in SESSIONLESS_ROUTES
        if key in routes and _uses_get_session(routes[key])
    }
    assert not promoted, (
        f"these are declared sessionless but now take a tenant-bound session: "
        f"{sorted(promoted)}. Remove them from SESSIONLESS_ROUTES."
    )


@pytest.mark.parametrize("method,path", sorted(_CALLABLE))
def test_the_sessionless_routes_really_touch_no_database(method, path):
    """Behavioural, not a promise.

    Without this, `SESSIONLESS_ROUTES` is a list anyone can add a route to,
    and the reason beside it is prose. Counting connection checkouts while the
    route runs is what makes the entry a measurement: a handler that opens its
    own session -- directly, or three helpers down -- checks out a connection
    and is caught, whatever the dict says about it.
    """
    checkouts: list[object] = []

    def _count(dbapi_connection, connection_record, connection_proxy):
        checkouts.append(connection_record)

    event.listen(engine, "checkout", _count)
    try:
        with TestClient(app) as client:
            checkouts.clear()  # the lifespan's init_db legitimately uses one
            response = client.request(method, path)
    finally:
        event.remove(engine, "checkout", _count)

    assert response.status_code < 500, f"{method} {path} returned {response.status_code}"
    assert not checkouts, (
        f"{method} {path} is declared sessionless but checked out "
        f"{len(checkouts)} database connection(s). Either it needs "
        f"`Depends(get_session)`, or the work it is doing belongs elsewhere."
    )


# --------------------------------------------------------------------------
# The pre-tenant route: measured, not declared
# --------------------------------------------------------------------------

def test_the_pre_tenant_list_names_no_route_that_stopped_existing():
    stale = set(PRE_TENANT_ROUTES) - set(_routes())
    assert not stale, f"PRE_TENANT_ROUTES names routes that no longer exist: {sorted(stale)}"


def test_the_pre_tenant_routes_do_not_also_take_a_session():
    """A route in both classes would be bound and pre-tenant at once, which
    is a contradiction, and this is where it would be noticed."""
    routes = _routes()
    both = sorted(k for k in PRE_TENANT_ROUTES if k in routes and _uses_get_session(routes[k]))
    assert not both, both


def test_the_pre_tenant_routes_touch_only_the_tables_they_declare(monkeypatch):
    """What the callback reads before anyone is authenticated, from the SQL.

    Driven to the point where it has verified an identity and is looking the
    subject up -- the provider is faked at the `oidc` seam, which is the only
    way to reach the database work without a network -- and every statement
    the engine executes is captured. Each table name in those statements must
    be in the declared set. The set is small on purpose: an unpolicied read
    of anything else before binding would be a cross-tenant read with a
    login-shaped excuse.
    """
    import re

    from api import oidc
    from api.config import Settings

    monkeypatch.setattr(
        "api.config.get_settings",
        lambda: Settings(
            app_mode="public_synthetic",
            oidc_issuer="https://issuer.test",
            oidc_client_id="client",
            oidc_client_secret="secret",
            oidc_redirect_uri="https://app.test/api/auth/callback",
        ),
        raising=True,
    )
    monkeypatch.setattr(oidc, "exchange_code", lambda code: "raw-token", raising=True)
    monkeypatch.setattr(
        oidc,
        "verify_id_token",
        lambda raw, *, nonce: oidc.Identity(issuer="https://issuer.test", subject="nobody"),
        raising=True,
    )

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with TestClient(app) as client:
            statements.clear()
            client.cookies.set("edge_oidc", "state.nonce", path="/api/auth")
            response = client.get(
                "/api/auth/callback",
                params={"code": "c", "state": "state"},
                follow_redirects=False,
            )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # The subject is unprovisioned, so the route must stop at the lookup.
    assert response.status_code == 403, response.text
    assert statements, "the callback issued no SQL, so this measured nothing"

    allowed = PRE_TENANT_ROUTES[("GET", "/api/auth/callback")]
    touched = set()
    for statement in statements:
        touched |= set(re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+\"?([a-z_]+)\"?", statement, re.I))
    assert touched and touched <= allowed, (
        f"the callback touched {sorted(touched)} before a tenant was bound; "
        f"declared: {sorted(allowed)}"
    )
