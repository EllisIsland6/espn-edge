"""The tenant seam: what it binds, and what it refuses.

The offline suite runs SQLite, which has no row-level security and no GUCs.
It therefore **cannot** prove isolation, and nothing in this file claims to.
What it can prove is the half the application owns: that every session handed
out by the two seams carries a tenant, that the binding is transaction-local,
that it survives a commit, and that the seam refuses rather than guesses.

The other half -- that an unbound session sees nothing -- was measured in
Phase 36 against a live PostgreSQL with `FORCE ROW LEVEL SECURITY`: 13 attacks
denied with SQLSTATE 42501, each paired with a control proving the same write
into the tenant's own scope is accepted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from api.db import SessionLocal, engine, get_session, session_scope
from api.models import Tenant
from api.tenancy import (
    DEFAULT_TENANT_SLUG,
    TENANT_GUC,
    TenantNotResolved,
    _apply_guc,
    bind_session,
    current_tenant_id,
    ensure_default_tenant,
    resolve_tenant_id,
)


def _tenant_ids() -> list[int]:
    session = SessionLocal()
    try:
        return list(session.execute(select(Tenant.id).order_by(Tenant.id)).scalars())
    finally:
        session.close()


def test_the_schema_arrives_with_exactly_one_tenant():
    """`create_all` seeds it, the same way alembic 0003's backfill does. If
    this is empty the whole file below is testing a condition production never
    has, and if it is more than one the fixtures are leaking."""
    assert len(_tenant_ids()) == 1


class _FakeRequest:
    """Enough of a request for the dependency. In private-operator mode the
    cookies are never read; in hosted mode this is where the session token
    would come from."""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = cookies or {}


def test_the_request_dependency_yields_a_bound_session():
    generator = get_session(_FakeRequest())
    session = next(generator)
    try:
        assert current_tenant_id(session) == _tenant_ids()[0]
    finally:
        generator.close()


def test_the_service_scope_yields_a_bound_session():
    """Background work is a named attack in Phase 36's contract: a job outside
    a request has no ambient tenant, and running it as the owner with a
    hand-written filter is how a scheduled export reads every tenant."""
    with session_scope() as session:
        assert current_tenant_id(session) == _tenant_ids()[0]


def test_a_raw_session_is_not_bound_and_says_so():
    """Recorded rather than closed. 28 places in this suite construct
    `SessionLocal()` directly, and application code does it in exactly two --
    which are the seams themselves. So the hole is real, it is confined to
    tests, and on PostgreSQL such a session reads nothing because no GUC is
    set. `current_tenant_id` raising is what stops it being silent."""
    session = SessionLocal()
    try:
        with pytest.raises(TenantNotResolved):
            current_tenant_id(session)
    finally:
        session.close()


def test_resolution_refuses_when_no_tenant_exists(monkeypatch):
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM tenants"))
        session.flush()
        with pytest.raises(TenantNotResolved, match="no tenant exists"):
            resolve_tenant_id(session)
    finally:
        session.rollback()
        session.close()


def test_resolution_refuses_when_a_second_tenant_exists():
    """The important refusal. `LIMIT 1` here would silently serve the lowest
    id the day a second tenant appears -- correct-looking, and wrong for
    everyone but the first tenant. There is no authentication in this
    application, so there is nothing to choose with, and choosing anyway is
    the defect this phase exists to prevent."""
    session = SessionLocal()
    try:
        session.add(Tenant(slug="second"))
        session.flush()
        with pytest.raises(TenantNotResolved, match="more than one tenant"):
            resolve_tenant_id(session)
    finally:
        session.rollback()
        session.close()


def test_ensure_default_tenant_is_idempotent():
    session = SessionLocal()
    try:
        first = ensure_default_tenant(session)
        second = ensure_default_tenant(session)
        session.commit()
        assert first == second
        assert _tenant_ids() == [first]
    finally:
        session.close()


def test_recreating_the_schema_does_not_accumulate_tenants():
    """The `after_create` hook fires once per creation of an empty table. If
    it ever fired twice the suite would drift into the two-tenant refusal
    above, which would present as unrelated tests failing later."""
    from api.db import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ids = _tenant_ids()
    assert len(ids) == 1

    session = SessionLocal()
    try:
        slug = session.execute(select(Tenant.slug)).scalar_one()
        assert slug == DEFAULT_TENANT_SLUG
    finally:
        session.close()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))


def test_the_guc_is_set_transaction_local_not_session_scoped():
    """One boolean, and it is the whole control.

    `set_config(name, value, is_local)`. `true` scopes the value to this
    transaction. `false` scopes it to the connection's session -- so under a
    pool, the next request to borrow this connection inherits the previous
    request's tenant. That is the pool-reuse attack Phase 36 names, and it
    would pass every test that checks the tenant is set, because it IS set.

    `SET LOCAL app.tenant_id = :t` is not an option: it takes no bind
    parameter, so the tenant would have to be interpolated into SQL text.
    """
    recorder = _Recorder()
    _apply_guc(recorder, 7)

    assert len(recorder.calls) == 1
    statement, params = recorder.calls[0]
    assert "set_config" in statement
    assert ", true)" in statement.replace(" ,", ",")
    assert "false" not in statement
    assert params == {"name": TENANT_GUC, "value": "7"}
    assert "7" not in statement, "the tenant must be a bind parameter, not interpolated"


def test_the_binding_is_reapplied_after_a_commit(monkeypatch):
    """Transaction-local means the binding dies with the transaction.

    A handler that commits half way through runs the rest of its statements in
    a NEW transaction with no tenant set. Under RLS that second half reads
    nothing -- which presents as a mysteriously empty result, not an error. So
    the seam re-binds on every transaction the session begins.

    Forced down the PostgreSQL branch with a stub settings object, because
    SQLite has no `set_config` to call.
    """
    calls: list[int] = []

    class _PgSettings:
        is_postgres = True
        # No configured tenant: this test is about the REBINDING, so the
        # configured-tenant verifier must not run and reach for a `tenants`
        # row. Spelled out rather than left off, because leaving it off is
        # what broke this test when the field was added -- and `getattr` with
        # a default in production would have hidden a real typo instead.
        tenant_id = None

    monkeypatch.setattr("api.tenancy.get_settings", lambda: _PgSettings())
    monkeypatch.setattr(
        "api.tenancy._apply_guc",
        lambda connection, tenant_id, user_id=None: calls.append(tenant_id),
    )

    session = SessionLocal()
    try:
        bind_session(session, 42)
        session.execute(select(Tenant.id)).all()
        first = len(calls)
        assert first >= 1, "the binding was never applied"

        session.commit()
        session.execute(select(Tenant.id)).all()
        assert len(calls) > first, (
            "the tenant was not re-bound after commit: every statement after "
            "the first commit would run with no tenant set"
        )
        assert set(calls) == {42}
    finally:
        session.close()


def test_a_league_created_through_the_api_is_owned():
    """The write half. A row created with a NULL tenant is invisible to every
    policy once RLS is on -- it fails closed, silently, and the league the
    operator just added simply does not appear.

    Ownership comes from the session's bound tenant, never from the request
    body: a client-supplied tenant id is a client-chosen owner.
    """
    from fastapi.testclient import TestClient

    from api.main import app
    from api.models import League

    with TestClient(app) as client:
        response = client.post("/api/leagues", json={"league_ref": "424242", "season": 2026})
    assert response.status_code in (200, 201), response.text

    session = SessionLocal()
    try:
        league = session.execute(
            select(League).where(League.espn_league_id == "424242")
        ).scalar_one()
        assert league.tenant_id == _tenant_ids()[0], (
            "the league was created without a tenant; under row-level security "
            "it would be invisible to everyone, including its creator"
        )
    finally:
        session.close()
