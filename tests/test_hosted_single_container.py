"""The hosted shape: one process serving the API and the built frontend.

Everything here was written because booting the application against a real
PostgreSQL found it. The offline suite could not have: SQLite has no
row-level security and the development server serves the frontend itself, so
both of the defects below are invisible until something deploys.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import _mount_frontend
from api.routers.health import describe_backend

# ----------------------------------------------------- the health endpoint ---


def test_the_backend_description_names_the_real_datastore():
    assert describe_backend("sqlite:////var/lib/edge.db") == "sqlite"
    assert (
        describe_backend("postgresql+psycopg2://u:p@db.internal:5432/edge")
        == "postgresql://db.internal:5432/edge"
    )


def test_the_backend_description_never_carries_a_credential():
    """This endpoint is what a load balancer polls without authenticating, so
    everything it returns is public.

    Measured before `backend` existed: `/api/health` reported
    `db_path: /tmp/pg/data/edge.db` for an application connected to
    PostgreSQL — a health check describing a datastore it was not using.
    """
    described = describe_backend(
        "postgresql+psycopg2://edge_app:sup3r-s3cret@db.internal:5432/edge"
    )
    assert "sup3r-s3cret" not in described
    assert "edge_app" not in described
    # ...and it is still useful: staging and production are distinguishable.
    assert "db.internal:5432/edge" in described


def test_health_reports_the_backend():
    from api.main import app

    body = TestClient(app).get("/api/health").json()
    assert body["status"] == "ok"
    # The suite runs on SQLite, so this is the honest reading here. The point
    # of the field is that it is read off the live URL rather than from the
    # SQLite-only `db_path` setting, which is what made it lie on PostgreSQL.
    assert body["backend"] == "sqlite", body


# ------------------------------------------------------- the static mount ---


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "web-dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html>SHELL", encoding="utf-8")
    (root / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("NOT-SERVABLE", encoding="utf-8")
    return root


@pytest.fixture
def spa(dist: Path) -> TestClient:
    application = FastAPI()

    @application.get("/api/real")
    def real() -> dict:
        return {"api": True}

    _mount_frontend(application, dist)
    return TestClient(application)


def test_a_client_side_route_gets_the_shell(spa):
    response = spa.get("/league/1/roster")
    assert response.status_code == 200
    assert "SHELL" in response.text


def test_a_real_file_is_served_as_itself(spa):
    assert spa.get("/assets/app.css").text == "body{}"


def test_an_unknown_api_path_is_a_404_and_not_the_shell(spa):
    """The catch-all must not answer for `/api/*`.

    An unknown API path that returns 200 with a page of HTML is a worse error
    than a 404: the caller's JSON parse fails somewhere else entirely, and the
    traceback points at the wrong component.
    """
    response = spa.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "SHELL" not in response.text


def test_a_registered_api_route_still_wins(spa):
    assert spa.get("/api/real").json() == {"api": True}


@pytest.mark.parametrize(
    "attempt",
    [
        "../outside.txt",
        "../../outside.txt",
        "assets/../../outside.txt",
        "%2e%2e%2foutside.txt",
        "..%2Foutside.txt",
        "./../outside.txt",
    ],
)
def test_no_traversal_escapes_the_served_directory(spa, attempt):
    """`full_path` comes from the URL, so this is a request the handler would
    otherwise honour. Every candidate is resolved and checked to be inside the
    served directory before it is opened."""
    response = spa.get(f"/{attempt}")
    assert "NOT-SERVABLE" not in response.text, attempt
    # The shell is the correct answer: it is a path the SPA router may own.
    assert response.status_code == 200


def test_the_traversal_target_really_exists(dist):
    """The instrument check. If `outside.txt` were missing, every assertion
    above would pass against a file that was never there to leak."""
    assert (dist.parent / "outside.txt").read_text(encoding="utf-8") == "NOT-SERVABLE"


# ----------------------------------------------- the configured tenant id ---
#
# `resolve_tenant_id`'s `SELECT id FROM tenants` cannot work on PostgreSQL,
# and that was measured on a correctly migrated database rather than reasoned
# about: the app connects as a NOSUPERUSER NOBYPASSRLS role, `tenants` has
# FORCE ROW LEVEL SECURITY, and the policy hides every row until
# `app.tenant_id` is bound. So the query that exists to FIND the tenant needs
# a tenant already bound to return anything. As `edge_app` with nothing bound,
# `SELECT count(*) FROM tenants` returns 0; with `SET LOCAL app.tenant_id='1'`
# it returns 1. Every request 500s with `TenantNotResolved`.


def test_a_configured_tenant_is_used_without_querying_for_it(db_session, monkeypatch):
    """The point of the setting: no read is needed to learn the tenant."""
    from api import tenancy
    from api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tenant_id", 4242, raising=False)
    monkeypatch.setattr(tenancy, "get_settings", lambda: settings)
    assert tenancy.resolve_tenant_id(db_session) == 4242


def test_a_configured_tenant_that_is_not_a_usable_id_refuses(db_session, monkeypatch):
    from api import tenancy
    from api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tenant_id", 0, raising=False)
    monkeypatch.setattr(tenancy, "get_settings", lambda: settings)
    with pytest.raises(tenancy.TenantNotResolved) as caught:
        tenancy.resolve_tenant_id(db_session)
    assert "not a usable tenant id" in str(caught.value)


def test_a_configured_tenant_that_does_not_exist_refuses_after_binding(
    db_session, monkeypatch
):
    """The order is the whole control and it cannot be reversed.

    Under FORCE ROW LEVEL SECURITY, `tenants` is invisible until a tenant is
    bound, so a check BEFORE binding reads nothing and would reject every id
    including the right one. Checking after binding reads exactly the row the
    binding claims.

    Without this, a typo in `TENANT_ID` binds a tenant that does not exist,
    every policy then matches nothing, and the application starts cleanly,
    answers 200, and shows an empty database — which is the worst of the three
    outcomes, because it looks like no data rather than a misconfiguration.
    """
    from api import tenancy
    from api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tenant_id", 999_999, raising=False)
    monkeypatch.setattr(tenancy, "get_settings", lambda: settings)
    with pytest.raises(tenancy.TenantNotResolved) as caught:
        tenancy.bind_session(db_session, 999_999)
    assert "does not name an existing tenant" in str(caught.value)


def test_binding_the_tenant_that_does_exist_is_accepted(db_session, monkeypatch):
    """The control for the test above. A verifier that refused everything
    would satisfy it and break every request."""
    from api import tenancy
    from api.config import get_settings
    from api.models import Tenant

    real = db_session.query(Tenant).one().id
    settings = get_settings()
    monkeypatch.setattr(settings, "tenant_id", real, raising=False)
    monkeypatch.setattr(tenancy, "get_settings", lambda: settings)
    tenancy.bind_session(db_session, real)  # must not raise
    assert db_session.info["tenant_id"] == real
