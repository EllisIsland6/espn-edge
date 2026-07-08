"""API smoke tests (Phase 0 AC: /api/health returns season + db path)."""

import pytest
from fastapi.testclient import TestClient

from api.db import init_db
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _ensure_schema():
    # Other tests drop/recreate tables; guarantee the schema exists for API tests.
    init_db()


def test_health_returns_season_and_db_path():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["season"] == 2026
    assert body["db_path"].endswith(".db")


def test_add_account_never_leaks_cookies():
    r = client.post(
        "/api/accounts",
        json={"label": "Main", "swid": "AAAA-1111", "espn_s2": "super-secret-value"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "swid" not in body and "espn_s2" not in body
    assert "super-secret-value" not in r.text
    assert body["label"] == "Main"
    assert body["status"] == "active"


def test_manual_add_league_parses_url():
    r = client.post(
        "/api/leagues",
        json={"league_ref": "https://fantasy.espn.com/football/team?leagueId=778899&seasonId=2026"},
    )
    assert r.status_code == 201
    assert r.json()["espn_league_id"] == "778899"
