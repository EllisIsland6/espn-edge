"""Read-only Phase 2 view endpoints — populated offline via FakeEspn (SPEC 8, 12)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.main import app
from api.models import Account, League, Player, Team
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
        session.add_all(
            [
                Player(espn_player_id=2001, name="Adds Player", position="WR"),
                Player(espn_player_id=2002, name="Drops Player", position="RB"),
                Player(espn_player_id=2003, name="Free Agent", position="TE"),
            ]
        )
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture
def current_team_detail_ids():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        acct = Account(label="Main", swid="{ABC}", espn_s2_encrypted=encrypt("s2"))
        session.add(acct)
        session.flush()
        league = League(
            espn_league_id="current-roster",
            season=2026,
            account_id=acct.id,
            is_public=False,
        )
        session.add(league)
        session.flush()
        SyncService(
            session,
            espn=FakeEspn(
                load_fixture("current_roster.json"),
                load_fixture("players_pool.json"),
                pro_schedule_data=load_fixture("pro_schedule_2026.json"),
            ),
        ).sync_league(league)
        session.commit()
        my_team = session.scalar(
            select(Team).where(Team.league_id == league.id, Team.is_me.is_(True))
        )
        yield league.id, my_team.id
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def test_portfolio(league_id):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    rows = r.json()
    row = next(x for x in rows if x["league_id"] == league_id)
    assert row["league_name"] == "Test Public League"
    assert row["my_team_name"] == "Alpha"
    assert row["my_team_logo_url"] == "http://x/1.png"
    assert row["wins"] == 1 and row["losses"] == 0
    assert row["account_label"] == "Main"
    # Phase 3 metrics are computed on sync (in_season fixture); UI only formats them.
    assert row["edge_score"] == 82.5
    assert row["grade"] == "A" and row["verdict"] == "advantaged"
    assert row["playoff_odds"] is not None
    assert row["edge_index_momentum"] == {
        "status": "first_sync",
        "delta": None,
        "streak_direction": None,
        "streak_count": 0,
        "history_count": 1,
    }
    assert "sync_healthy" in {item["key"] for item in row["achievements"]}

    league = next(x for x in client.get("/api/leagues").json() if x["id"] == league_id)
    assert league["my_team_name"] == "Alpha"
    assert league["my_team_logo_url"] == "http://x/1.png"

    overview = client.get(f"/api/leagues/{league_id}/overview").json()
    assert overview["league"]["my_team_name"] == "Alpha"
    assert overview["league"]["my_team_logo_url"] == "http://x/1.png"


def test_league_identity_rejects_cross_league_team_reference(league_id):
    with SessionLocal() as session:
        league = session.get(League, league_id)
        for team in session.scalars(select(Team).where(Team.league_id == league_id)):
            team.is_me = False
        other = League(
            espn_league_id="identity-other",
            season=2026,
            lifecycle="drafted",
            is_public=True,
        )
        session.add(other)
        session.flush()
        foreign_team = Team(
            league_id=other.id,
            espn_team_id=99,
            name="Wrong League Team",
            logo_url="https://example.test/wrong.png",
            is_me=True,
        )
        session.add(foreign_team)
        session.flush()
        league.my_team_id = foreign_team.id
        session.commit()

    listed = next(x for x in client.get("/api/leagues").json() if x["id"] == league_id)
    assert listed["my_team_name"] is None
    assert listed["my_team_logo_url"] is None

    overview = client.get(f"/api/leagues/{league_id}/overview").json()
    assert overview["league"]["my_team_name"] is None
    assert overview["league"]["my_team_logo_url"] is None

    portfolio = next(x for x in client.get("/api/portfolio").json() if x["league_id"] == league_id)
    assert portfolio["my_team_id"] is None
    assert portfolio["my_team_name"] is None
    assert portfolio["my_team_logo_url"] is None


def test_portfolio_summary(league_id):
    assert league_id  # ensures the one-league fixture is populated
    r = client.get("/api/portfolio/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["total_leagues"] == 1
    # my team Alpha is 1-0-0 in the fixture -> aggregates come from the DB, not React.
    assert s["aggregate_wins"] == 1
    assert s["aggregate_losses"] == 0
    assert s["aggregate_ties"] == 0
    # Phase 3: my team (Alpha) is advantaged and scored.
    assert s["advantaged_count"] == 1
    assert s["scored_count"] == 1
    assert s["best_edge_score"] == 82.5 and s["worst_edge_score"] == 82.5


def test_portfolio_summary_empty():
    # Clean DB (no fixture) → zeroed summary, still 200.
    from api.db import Base, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = client.get("/api/portfolio/summary").json()
    assert s["total_leagues"] == 0
    assert s["aggregate_wins"] == 0 and s["advantaged_count"] == 0


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
    # Phase 3: overview exposes my team's computed edge.
    assert body["edge_score"] == 82.5 and body["grade"] == "A" and body["verdict"] == "advantaged"
    assert body["momentum"]["status"] == "first_sync"
    assert body["momentum"]["history_count"] == 1
    assert "sync_healthy" in {item["key"] for item in body["achievements"]}


def test_league_subresources(league_id):
    assert len(client.get(f"/api/leagues/{league_id}/teams").json()) == 4
    assert len(client.get(f"/api/leagues/{league_id}/draft").json()) == 8
    assert len(client.get(f"/api/leagues/{league_id}/matchups").json()) == 4
    activity = client.get(f"/api/leagues/{league_id}/activity").json()
    assert len(activity) == 2
    assert {t["type"] for t in activity} == {"waiver", "fa_add"}
    waiver = next(t for t in activity if t["type"] == "waiver")
    assert waiver["player_in_name"] == "Adds Player"
    assert waiver["player_in_position"] == "WR"
    assert waiver["player_out_name"] == "Drops Player"
    assert waiver["player_out_position"] == "RB"


def test_team_detail_uses_configured_slots_and_current_matchup(current_team_detail_ids):
    league_id, team_id = current_team_detail_ids
    response = client.get(f"/api/leagues/{league_id}/teams/{team_id}")
    assert response.status_code == 200
    body = response.json()

    assert body["team"]["name"] == "My Team"
    assert body["team"]["is_me"] is True
    assert body["current_scoring_period"] == 1
    assert body["current_matchup_period"] == 1
    assert body["roster_status"] == "current"
    assert [row["slot_label"] for row in body["starters"]] == [
        "QB",
        "RB",
        "RB",
        "WR",
        "TE",
        "FLEX",
        "D/ST",
        "K",
    ]
    quarterback = body["starters"][0]
    assert quarterback["player_name"] == "Alpha Quarterback"
    assert quarterback["actual_points"] == 0.0
    assert quarterback["projected_points"] == 18.5
    assert quarterback["opponent"] == "BUF"
    assert quarterback["game_status"] == "pregame"
    flex = next(row for row in body["starters"] if row["slot_label"] == "FLEX")
    assert flex["espn_player_id"] is None
    assert len(body["bench"]) == 2
    assert body["bench"][0]["player_name"] == "Bench Receiver"
    assert body["bench"][1]["espn_player_id"] is None
    assert len(body["ir"]) == 1
    assert body["ir"][0]["injury_status"] == "INJURY_RESERVE"

    matchup = body["matchup"]
    assert matchup["matchup_period"] == 1
    assert matchup["home"]["team"]["name"] == "My Team"
    assert matchup["away"]["team"]["name"] == "Rival"
    assert matchup["home"]["points"] == 0.0
    assert matchup["home"]["projected_points"] == 108.4
    assert matchup["next_kickoff_at"] is not None


def test_team_detail_marks_failed_sync_stale(current_team_detail_ids):
    league_id, team_id = current_team_detail_ids
    with SessionLocal() as session:
        league = session.get(League, league_id)
        league.last_sync_ok = False
        league.last_sync_error = "current_roster_failed"
        session.commit()

    body = client.get(f"/api/leagues/{league_id}/teams/{team_id}").json()
    assert body["roster_status"] == "stale"


def test_team_detail_rejects_team_outside_league(current_team_detail_ids):
    league_id, _team_id = current_team_detail_ids
    with SessionLocal() as session:
        other = League(espn_league_id="other", season=2026, is_public=True)
        session.add(other)
        session.flush()
        foreign = Team(league_id=other.id, espn_team_id=99, name="Foreign")
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    assert client.get(f"/api/leagues/{league_id}/teams/{foreign_id}").status_code == 404


def test_view_404_for_missing_league():
    assert client.get("/api/leagues/999999/overview").status_code == 404
    assert client.get("/api/leagues/999999/draft").status_code == 404
