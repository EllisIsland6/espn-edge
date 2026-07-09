"""Phase 3 analytics tests — pure computation, persistence, invalidation, API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.edge_config import INSEASON_WEIGHTS, grade_for, verdict_for
from api.main import app
from api.models import Account, DraftPick, League, Matchup, Metric, Player, Team
from api.services import metrics
from api.services.metrics import TeamStat, team_edge
from api.services.playoff_sim import SimGame, SimTeam, simulate_playoff_odds
from api.services.sync import SyncService

from .conftest import FakeEspn, load_fixture

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Pure functions (no DB)
# --------------------------------------------------------------------------- #
def test_weights_sum_to_one():
    assert round(sum(INSEASON_WEIGHTS.values()), 6) == 1.0


def test_percentile_midrank_and_single():
    assert metrics._percentile([1.0], 1.0) == 50.0
    # 120.5 vs [120.5,100,90,80]: 3 below + itself → 87.5
    assert metrics._percentile([120.5, 100, 90, 80], 120.5) == 87.5
    # worst value → 12.5 (mid-rank of the single equal element)
    assert metrics._percentile([120.5, 100, 90, 80], 80) == 12.5


def test_grade_and_verdict_bands():
    assert grade_for(None) is None and verdict_for(None) is None
    assert grade_for(82.5) == "A" and verdict_for(82.5) == "advantaged"
    assert grade_for(66) == "B" and verdict_for(66) == "advantaged"
    assert grade_for(55) == "C" and verdict_for(55) == "neutral"
    assert grade_for(42) == "D" and verdict_for(42) == "disadvantaged"
    assert grade_for(10) == "F" and verdict_for(10) == "disadvantaged"


def _toy_stats():
    # Mirrors public_league.json after week 1 (Alpha=team 1 leads).
    return [
        TeamStat(1, 1, 0, 0, 120.5, 100.0, 1, None),
        TeamStat(2, 0, 1, 0, 100.0, 120.5, 3, None),
        TeamStat(3, 1, 0, 0, 90.0, 80.0, 2, None),
        TeamStat(4, 0, 1, 0, 80.0, 90.0, 4, None),
    ]


def test_edge_scores_pre_draft_all_pending():
    assert metrics.compute_edge_scores(_toy_stats(), "pre_draft") == {1: None, 2: None, 3: None, 4: None}


def test_edge_scores_in_season_ranks_by_strength():
    scores = metrics.compute_edge_scores(_toy_stats(), "in_season")
    # Hand-computed: leader 0.4*75 + 0.3*87.5 + 0.3*87.5 = 82.5; then 60.0, 32.5, 25.0.
    assert scores[1] == 82.5
    assert scores[1] > scores[3] > scores[2] > scores[4]


def test_edge_scores_drafted_uses_roster_projection():
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0),
        TeamStat(4, 0, 0, 0, 0, 0, None, None),  # no projection → pending
    ]
    scores = metrics.compute_edge_scores(teams, "drafted")
    assert scores[1] == max(v for v in scores.values() if v is not None)
    assert scores[4] is None


def test_edge_scores_drafted_pending_without_enough_projections():
    teams = [TeamStat(1, 0, 0, 0, 0, 0, None, None), TeamStat(2, 0, 0, 0, 0, 0, None, 100.0)]
    assert metrics.compute_edge_scores(teams, "drafted") == {1: None, 2: None}


# --------------------------------------------------------------------------- #
# Phase 5 Monte Carlo simulation (pure, seeded, deterministic)
# --------------------------------------------------------------------------- #
def test_simulate_playoff_odds_deterministic_and_orders_by_strength():
    teams = [
        SimTeam(1, 2, 0, 0, 260, [130, 130]),  # strong
        SimTeam(2, 2, 0, 0, 250, [125, 125]),  # strong
        SimTeam(3, 0, 2, 0, 180, [90, 90]),  # weak
        SimTeam(4, 0, 2, 0, 170, [85, 85]),  # weak
    ]
    remaining = [SimGame(1, 3), SimGame(2, 4), SimGame(1, 4), SimGame(2, 3)]
    a = simulate_playoff_odds(teams, remaining, 2, n=2000, seed=42)
    b = simulate_playoff_odds(teams, remaining, 2, n=2000, seed=42)
    assert a == b  # reproducible with a fixed seed
    assert all(0.0 <= v <= 1.0 for v in a.values())
    assert a[1] > a[3] and a[2] > a[4]  # strong teams likelier than weak
    assert a[1] > 0.8 and a[4] < 0.2  # dominant / weak extremes


def test_simulate_pending_when_a_team_has_no_scores():
    teams = [SimTeam(1, 0, 0, 0, 0, [100]), SimTeam(2, 0, 0, 0, 0, [])]
    assert simulate_playoff_odds(teams, [], 1, n=100, seed=1) == {}


def test_simulate_pending_when_no_spots():
    assert simulate_playoff_odds([SimTeam(1, 0, 0, 0, 0, [100])], [], 0, n=100, seed=1) == {}


# --------------------------------------------------------------------------- #
# Persistence + invalidation (DB)
# --------------------------------------------------------------------------- #
def _make_inseason_league(session) -> League:
    lg = League(espn_league_id="222", season=2026, is_public=True, lifecycle="in_season",
                size=4, playoff_team_count=2)
    session.add(lg)
    session.flush()
    for stat in _toy_stats():
        session.add(
            Team(league_id=lg.id, espn_team_id=stat.team_id, name=f"T{stat.team_id}",
                 is_me=(stat.team_id == 1), wins=stat.wins, losses=stat.losses, ties=stat.ties,
                 points_for=stat.points_for, points_against=stat.points_against, standing=stat.standing)
        )
    session.flush()
    ids = {
        espn: pk
        for pk, espn in session.execute(
            select(Team.id, Team.espn_team_id).where(Team.league_id == lg.id)
        )
    }
    lg.my_team_id = ids[1]
    # Week 1 played (mirrors the toy records/points), week 2 remaining — gives the
    # Monte Carlo sim real scores + a remaining schedule.
    session.add_all([
        Matchup(league_id=lg.id, week=1, home_team_id=ids[1], away_team_id=ids[2],
                home_points=120.5, away_points=100.0),
        Matchup(league_id=lg.id, week=1, home_team_id=ids[3], away_team_id=ids[4],
                home_points=90.0, away_points=80.0),
        Matchup(league_id=lg.id, week=2, home_team_id=ids[1], away_team_id=ids[3],
                home_points=0, away_points=0),
        Matchup(league_id=lg.id, week=2, home_team_id=ids[2], away_team_id=ids[4],
                home_points=0, away_points=0),
    ])
    session.flush()
    return lg


def test_recompute_persists_and_derives(db_session):
    lg = _make_inseason_league(db_session)
    result = metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert result["teams"] == 4 and result["scored"] == 4

    edge = team_edge(db_session, lg.id, lg.my_team_id)
    assert edge.edge_score == 82.5
    assert edge.grade == "A"
    assert edge.verdict == "advantaged"
    # Phase 5: simulated playoff odds (a valid probability), not the old heuristic band.
    assert edge.playoff_odds is not None and 0.0 <= edge.playoff_odds <= 1.0
    # one edge_score + one playoff_odds row per team.
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(Metric.league_id == lg.id, Metric.key == "edge_score")
    ) == 4


def test_recompute_invalidates_when_pending(db_session):
    lg = _make_inseason_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).edge_score is not None

    # League reverts to pre_draft → every edge becomes pending → rows deleted.
    lg.lifecycle = "pre_draft"
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).edge_score is None
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(Metric.league_id == lg.id, Metric.key == "edge_score")
    ) == 0


def test_playoff_odds_complete_is_deterministic(db_session):
    lg = _make_inseason_league(db_session)
    lg.lifecycle = "complete"
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    # standings 1,2 (espn 1,3) make the 2 playoff spots; espn 2,4 miss.
    for espn, standing in [(1, 1), (3, 2), (2, 3), (4, 4)]:
        tid = db_session.scalar(
            select(Team.id).where(Team.league_id == lg.id, Team.espn_team_id == espn)
        )
        assert team_edge(db_session, lg.id, tid).playoff_odds == (1.0 if standing <= 2 else 0.0)


def test_playoff_odds_pending_without_playoff_team_count(db_session):
    lg = _make_inseason_league(db_session)
    lg.playoff_team_count = None
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).playoff_odds is None


def test_playoff_odds_pending_without_schedule(db_session):
    # in_season but no matchups → can't model scores → pending (never fabricated).
    lg = League(espn_league_id="223", season=2026, is_public=True, lifecycle="in_season",
                size=4, playoff_team_count=2)
    db_session.add(lg)
    db_session.flush()
    for i in range(1, 5):
        db_session.add(Team(league_id=lg.id, espn_team_id=i, name=f"T{i}", wins=1, losses=0,
                            ties=0, points_for=100.0, standing=i))
    db_session.flush()
    lg.my_team_id = db_session.scalar(
        select(Team.id).where(Team.league_id == lg.id, Team.espn_team_id == 1)
    )
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).playoff_odds is None


def test_recompute_is_idempotent(db_session):
    lg = _make_inseason_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    # No duplicate rows (partial unique index holds).
    assert db_session.scalar(select(func.count()).select_from(Metric).where(Metric.league_id == lg.id)) == 8


# --------------------------------------------------------------------------- #
# API exposure (sync toy fixture end-to-end, offline)
# --------------------------------------------------------------------------- #
@pytest.fixture
def synced_league_id():
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
            session,
            espn=FakeEspn(load_fixture("public_league.json"), load_fixture("players_pool.json")),
        ).sync_league(lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_api_portfolio_exposes_computed_metrics(synced_league_id):
    row = next(r for r in client.get("/api/portfolio").json() if r["league_id"] == synced_league_id)
    assert row["edge_score"] == 82.5
    assert row["grade"] == "A"
    assert row["verdict"] == "advantaged"
    assert row["playoff_odds"] is not None


def test_api_overview_and_summary_expose_metrics(synced_league_id):
    ov = client.get(f"/api/leagues/{synced_league_id}/overview").json()
    assert ov["edge_score"] == 82.5 and ov["grade"] == "A" and ov["verdict"] == "advantaged"
    # Finding 1: overview must expose playoff_odds like the portfolio endpoint.
    assert "playoff_odds" in ov
    assert ov["playoff_odds"] is not None and 0.0 <= ov["playoff_odds"] <= 1.0

    s = client.get("/api/portfolio/summary").json()
    assert s["scored_count"] == 1
    assert s["advantaged_count"] == 1
    assert s["best_edge_score"] == 82.5 and s["worst_edge_score"] == 82.5


# --------------------------------------------------------------------------- #
# Finding 2: stale projections must not produce freshly-stamped edge scores
# --------------------------------------------------------------------------- #
def test_stale_projections_make_drafted_edge_pending_pure():
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0),
    ]
    # fresh → computed; not fresh → pending for the projection branch.
    assert metrics.compute_edge_scores(teams, "drafted", projections_fresh=True)[1] is not None
    assert metrics.compute_edge_scores(teams, "drafted", projections_fresh=False) == {1: None, 2: None}


def test_stale_projections_do_not_block_record_scoring_pure():
    # in_season with games does not depend on proj_ros → still computed.
    scores = metrics.compute_edge_scores(_toy_stats(), "in_season", projections_fresh=False)
    assert scores[1] == 82.5


def _make_drafted_league(session) -> League:
    lg = League(espn_league_id="333", season=2026, is_public=True, lifecycle="drafted",
                size=4, playoff_team_count=2)
    session.add(lg)
    session.flush()
    teams = []
    for i in range(1, 5):
        t = Team(league_id=lg.id, espn_team_id=i, name=f"T{i}", is_me=(i == 1))
        session.add(t)
        teams.append(t)
    session.flush()
    lg.my_team_id = teams[0].id
    for pid, proj in [(101, 300.0), (102, 250.0), (103, 200.0), (104, 150.0)]:
        session.add(Player(espn_player_id=pid, name=f"P{pid}", proj_ros=proj))
    session.flush()
    for i, t in enumerate(teams, start=1):
        session.add(DraftPick(league_id=lg.id, overall=i, team_id=t.id, espn_player_id=100 + i))
    session.flush()
    return lg


def test_recompute_clears_stale_projection_edge_when_players_not_fresh(db_session):
    lg = _make_drafted_league(db_session)
    # Fresh sync computed a projection-based score.
    metrics.recompute_league(db_session, lg, projections_fresh=True)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).edge_score is not None

    # A later sync where kona_player_info failed must NOT re-publish from stale
    # proj_ros — it clears the score to pending.
    metrics.recompute_league(db_session, lg, projections_fresh=False)
    db_session.commit()
    assert team_edge(db_session, lg.id, lg.my_team_id).edge_score is None
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(Metric.league_id == lg.id, Metric.key == "edge_score")
    ) == 0
