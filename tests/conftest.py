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

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def league_fixture() -> dict:
    return load_fixture("public_league.json")


@pytest.fixture
def players_fixture() -> dict:
    return load_fixture("players_pool.json")


@pytest.fixture
def db_session():
    """Fresh schema per test, rolled back / dropped after."""
    from api.db import Base, SessionLocal, engine, init_db

    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        # Reset tables so tests don't leak rows into each other.
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


class FakeEspn:
    """Stand-in for EspnService that replays fixtures — no network (SPEC 12).

    Returns the combined league fixture for any league view (ESPN merges stacked
    views into one response) and the player fixture for kona_player_info.
    """

    def __init__(self, league_data: dict, players_data: dict, *, auth_error: bool = False):
        self.league_data = league_data
        self.players_data = players_data
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
