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


def test_delete_unlinked_account_ok():
    aid = client.post(
        "/api/accounts", json={"label": "Disposable", "swid": "{X-1}", "espn_s2": "s2"}
    ).json()["id"]
    assert client.delete(f"/api/accounts/{aid}").status_code == 204
    labels = [a["label"] for a in client.get("/api/accounts").json()]
    assert "Disposable" not in labels


def test_delete_account_blocked_when_leagues_linked():
    aid = client.post(
        "/api/accounts", json={"label": "Linked", "swid": "{X-2}", "espn_s2": "s2"}
    ).json()["id"]
    add = client.post("/api/leagues", json={"league_ref": "445566", "account_id": aid})
    assert add.status_code == 201

    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 409
    assert "linked league" in r.json()["detail"]
    # account still present (not deleted)
    assert aid in [a["id"] for a in client.get("/api/accounts").json()]
