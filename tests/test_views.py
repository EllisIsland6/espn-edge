"""Read-only Phase 2 view endpoints — populated offline via FakeEspn (SPEC 8, 12)."""

import pytest
from fastapi.testclient import TestClient

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.main import app
from api.models import Account, League
from api.services.sync import SyncService

from .conftest import FakeEspn, load_fixture

client = TestClient(app)


@pytest.fixture
def league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        acct = Account(label="Main", swid="{AAAA-1111}", espn_s2_encrypted=encrypt("s2"))
        session.add(acct)
        session.flush()
        lg = League(espn_league_id="111", season=2026, account_id=acct.id, is_public=False)
        session.add(lg)
        session.flush()
        SyncService(
            session, espn=FakeEspn(load_fixture("public_league.json"), load_fixture("players_pool.json"))
        ).sync_league(lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_portfolio(league_id):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    rows = r.json()
    row = next(x for x in rows if x["league_id"] == league_id)
    assert row["league_name"] == "Test Public League"
    assert row["my_team_name"] == "Alpha"
    assert row["wins"] == 1 and row["losses"] == 0
    assert row["account_label"] == "Main"
    # Phase 3 metric fields are null (UI must not compute).
    assert row["edge_score"] is None and row["grade"] is None and row["playoff_odds"] is None


def test_league_overview(league_id):
    r = client.get(f"/api/leagues/{league_id}/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["scoring"] == "PPR"
    assert body["league"]["name"] == "Test Public League"
    assert len(body["teams"]) == 4
    # sorted by standing ascending; team with seed 1 first.
    assert body["teams"][0]["standing"] == 1
    me = next(t for t in body["teams"] if t["is_me"])
    assert me["name"] == "Alpha"
    assert body["edge_score"] is None


def test_league_subresources(league_id):
    assert len(client.get(f"/api/leagues/{league_id}/teams").json()) == 4
    assert len(client.get(f"/api/leagues/{league_id}/draft").json()) == 8
    assert len(client.get(f"/api/leagues/{league_id}/matchups").json()) == 4
    activity = client.get(f"/api/leagues/{league_id}/activity").json()
    assert len(activity) == 2
    assert {t["type"] for t in activity} == {"waiver", "fa_add"}


def test_view_404_for_missing_league():
    assert client.get("/api/leagues/999999/overview").status_code == 404
    assert client.get("/api/leagues/999999/draft").status_code == 404
