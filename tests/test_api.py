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


def test_reauth_updates_cookie_swid_and_status_without_leaking():
    from api.crypto import decrypt
    from api.db import SessionLocal
    from api.models import Account

    aid = client.post(
        "/api/accounts", json={"label": "Expiring", "swid": "OLD-1", "espn_s2": "old-s2"}
    ).json()["id"]
    # Force the account into needs_reauth so we can prove reauth clears it.
    with SessionLocal() as s:
        s.get(Account, aid).status = "needs_reauth"
        s.commit()

    r = client.post(
        f"/api/accounts/{aid}/reauth",
        json={"swid": "NEW-2", "espn_s2": "brand-new-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    # Response never echoes the secrets back.
    assert "swid" not in body and "espn_s2" not in body
    assert "brand-new-secret" not in r.text and "NEW-2" not in r.text
    assert body["status"] == "active"

    with SessionLocal() as s:
        acct = s.get(Account, aid)
        assert acct.status == "active"
        assert acct.swid == "{NEW-2}"  # normalized to braced form
        assert decrypt(acct.espn_s2_encrypted) == "brand-new-secret"


def test_reauth_missing_account_404():
    r = client.post(
        "/api/accounts/999999/reauth", json={"swid": "X-1", "espn_s2": "s2"}
    )
    assert r.status_code == 404


def test_reauth_empty_swid_400():
    aid = client.post(
        "/api/accounts", json={"label": "T", "swid": "{Y-1}", "espn_s2": "s2"}
    ).json()["id"]
    r = client.post(f"/api/accounts/{aid}/reauth", json={"swid": "{}", "espn_s2": "s2"})
    assert r.status_code == 400


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
