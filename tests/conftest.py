"""Test setup — isolate config + DB to a temp dir BEFORE importing api modules.

All tests run offline against fixtures in tests/fixtures/ (SPEC 12).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Must be set before any `api` import so get_settings()/engine pick them up.
_TMP = tempfile.mkdtemp(prefix="espn-edge-test-")
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["SEASON"] = "2026"
os.environ["ANTHROPIC_API_KEY"] = ""
# Phase 30 recovery is exercised with explicit injected settings/state in its
# own tests. Existing offline fixture tests are not private operator runs.
os.environ["RECOVERY_REQUIRED"] = "false"
# Phase 31: pin the mode too, or a hosted `.env` makes the offline suite
# environment-dependent. Hosted-mode tests inject their own settings.
os.environ["APP_MODE"] = "private_operator"
# Phase 32: both are required, so the suite must pin them or nothing imports.
os.environ["TELEMETRY_ENABLED"] = "false"
os.environ["TELEMETRY_REPORT_PATH"] = str(Path(_TMP) / "phase-32-report.md")
# Phase 32, contract criterion 1: `espn_api_host` defaults to the real provider
# and `EspnService` falls back to it, so a construction that forgets `host=`
# builds real provider URLs. Port 9 is discard and nothing listens on it; the
# guard below is what actually enforces it, and this pin means a slip fails
# closed at connect() instead of reaching ESPN.
os.environ["ESPN_API_HOST"] = "https://127.0.0.1:9"

FIXTURES = Path(__file__).parent / "fixtures"

#: Loopback only. Everything else is a test bug, not a network problem.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


@pytest.fixture(autouse=True)
def _forbid_outbound_sockets(monkeypatch):
    """Phase 32 criterion 1: no provider network call, enforced rather than declared.

    "No provider network call is made by this phase at any point" was a
    Forbidden-path statement, and Forbidden paths are enforced by reading a diff
    while this is a runtime property. The contract now requires real HTTP on a
    loopback socket for the truncation cases, which is exactly the configuration
    where a forgotten `host=` leaves the machine. So connect() is guarded: the
    loopback cases pass through untouched and anything else fails the test that
    attempted it, naming the host.
    """
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _ALLOWED_HOSTS:
            raise AssertionError(
                f"outbound connection to {host!r} blocked: the offline suite may "
                f"only reach loopback. A missing host= on EspnService is the usual cause."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def league_fixture() -> dict:
    return load_fixture("public_league.json")


@pytest.fixture
def players_fixture() -> dict:
    return load_fixture("players_pool.json")


@pytest.fixture
def current_roster_fixture() -> dict:
    return load_fixture("current_roster.json")


@pytest.fixture
def pro_schedule_fixture() -> dict:
    return load_fixture("pro_schedule_2026.json")


@pytest.fixture(autouse=True)
def _tenant_exists():
    """Every test starts with the single tenant present.

    Not a convenience. Phase 37's seam refuses to hand out a session when no
    tenant exists, which is the intended production behaviour -- a migrated
    database always has one, because alembic 0003's backfill creates it. The
    offline suite builds its schema with `create_all` and resets it with
    `drop_all`/`create_all` between tests, so without this the guarantee that
    holds in production does not hold here, and 41 tests fail on a condition
    that cannot occur in a real database.

    It runs after the schema exists and is cheap: one SELECT when the row is
    already there.
    """
    from api.db import Base, SessionLocal, engine
    from api.tenancy import ensure_default_tenant

    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        ensure_default_tenant(session)
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def db_session():
    """Fresh schema per test, rolled back / dropped after."""
    from api.db import Base, SessionLocal, engine, init_db

    init_db()
    session = SessionLocal()
    # Bound, because every session the application hands out is bound and a
    # fixture that hands out an unbound one tests a configuration production
    # does not have. `current_tenant_id` raises on an unbound session, so
    # without this any code path that derives ownership fails here only.
    from api.tenancy import bind_session, resolve_tenant_id

    bind_session(session, resolve_tenant_id(session))
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        # Reset tables so tests don't leak rows into each other. The tenant is
        # part of the schema's guarantees, not of a test's data, so it is put
        # back with the tables rather than left for the next test to miss.
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        from api.tenancy import ensure_default_tenant

        reset = SessionLocal()
        try:
            ensure_default_tenant(reset)
            reset.commit()
        finally:
            reset.close()


class FakeEspn:
    """Stand-in for EspnService that replays fixtures — no network (SPEC 12).

    Returns the combined league fixture for any league view (ESPN merges stacked
    views into one response) and the player fixture for kona_player_info.
    """

    def __init__(
        self,
        league_data: dict,
        players_data: dict,
        *,
        auth_error: bool = False,
        pro_schedule_data: dict | None = None,
    ):
        self.league_data = league_data
        self.players_data = players_data
        self.pro_schedule_data = pro_schedule_data or {"settings": {"proTeams": []}}
        self.auth_error = auth_error
        self.calls: list[tuple] = []

    def fetch_views(
        self,
        league_id,
        season,
        views,
        *,
        scoring_period=None,
        cookies=None,
        x_fantasy_filter=None,
        bust_cache=False,
    ):
        from api.services.espn import EspnAuthError

        self.calls.append((tuple(views), scoring_period))
        if self.auth_error:
            raise EspnAuthError("forced auth error")
        if "kona_player_info" in views:
            return self.players_data
        return self.league_data

    def fetch_pro_schedule(self, season):
        self.calls.append((("proTeamSchedules_wl",), None))
        return self.pro_schedule_data
