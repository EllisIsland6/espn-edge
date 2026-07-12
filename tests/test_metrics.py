"""Phase 3 analytics tests — pure computation, persistence, invalidation, API."""

import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.edge_config import INSEASON_WEIGHTS, grade_for, verdict_for
from api.main import app
from api.models import Account, DraftPick, League, Matchup, Metric, Player, Team
from api.services import metrics
from api.services.metrics import TeamStat, team_components, team_edge
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
# Phase 9: edge_score component breakdown (pure)
# --------------------------------------------------------------------------- #
def test_edge_components_in_season_hand_computed():
    comps = metrics.compute_edge_components(_toy_stats(), "in_season")
    # Leader (team 1): win_pct 75, points_for 87.5, point_diff 87.5.
    by_key = {c.key: c for c in comps[1]}
    assert set(by_key) == {"win_pct", "points_for", "point_diff"}
    assert by_key["win_pct"].percentile == 75.0
    assert by_key["points_for"].percentile == 87.5
    assert by_key["point_diff"].percentile == 87.5
    # Weights come straight from INSEASON_WEIGHTS.
    assert by_key["win_pct"].weight == INSEASON_WEIGHTS["win_pct"]
    assert by_key["points_for"].weight == INSEASON_WEIGHTS["points_for"]
    assert by_key["point_diff"].weight == INSEASON_WEIGHTS["point_diff"]


def test_edge_components_reduce_to_edge_score_byte_identical():
    # The weighted component sum must equal the persisted edge_score exactly, for every team.
    stats = _toy_stats()
    comps = metrics.compute_edge_components(stats, "in_season")
    scores = metrics.compute_edge_scores(stats, "in_season")
    for t in stats:
        reduced = round(sum(c.weight * c.percentile for c in comps[t.team_id]), 1)
        assert reduced == scores[t.team_id]
    assert scores[1] == 82.5  # unchanged from Phase 3


def test_edge_components_drafted_single_roster_proj():
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0),
        TeamStat(4, 0, 0, 0, 0, 0, None, None),  # no projection → pending, no components
    ]
    comps = metrics.compute_edge_components(teams, "drafted")
    assert [c.key for c in comps[1]] == ["roster_proj"]
    assert comps[1][0].weight == 1.0
    assert comps[1][0].percentile == metrics._percentile([300.0, 200.0, 100.0], 300.0)
    assert comps[4] == []  # pending team has no components
    # Reduction with weight 1.0 equals the roster-projection edge_score.
    assert round(comps[1][0].weight * comps[1][0].percentile, 1) == metrics.compute_edge_scores(teams, "drafted")[1]


def test_edge_components_pending_when_pre_draft_or_stale():
    assert metrics.compute_edge_components(_toy_stats(), "pre_draft") == {1: [], 2: [], 3: [], 4: []}
    drafted = [TeamStat(1, 0, 0, 0, 0, 0, None, 300.0), TeamStat(2, 0, 0, 0, 0, 0, None, 200.0)]
    assert metrics.compute_edge_components(drafted, "drafted", projections_fresh=False) == {1: [], 2: []}


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


def test_current_week_partial_scores_stay_remaining():
    # Finding 1: mid-week partial scores must not become completed samples.
    from types import SimpleNamespace

    def game(week, h, a, hp, ap):
        return SimpleNamespace(week=week, home_team_id=h, away_team_id=a,
                               home_points=hp, away_points=ap, is_playoff=False)

    ids = {1, 2, 3, 4}
    matchups = [
        game(1, 1, 2, 120.0, 100.0),
        game(1, 3, 4, 90.0, 80.0),
        game(2, 1, 3, 50.0, 30.0),  # current week (2) in progress — partial score
        game(2, 2, 4, 0, 0),  # current week not started
    ]
    # current_week = 2 → only week 1 is completed
    played, remaining = metrics._split_matchups(matchups, ids, completed_weeks={1})
    assert played[1] == [120.0] and played[3] == [90.0]  # week-2 partials excluded
    assert 50.0 not in played[1] and 30.0 not in played[3]
    assert len(remaining) == 2  # both week-2 games remain to be simulated

    # Documents the bug the fix closes: the legacy points>0 fallback WOULD have
    # (wrongly) counted the partial score and dropped that game from remaining.
    played_fb, remaining_fb = metrics._split_matchups(matchups, ids, completed_weeks=None)
    assert 50.0 in played_fb[1] and len(remaining_fb) == 1


def test_no_remaining_games_uses_espn_standing(db_session):
    # Finding 2: in_season with all regular-season games played → deterministic by
    # ESPN standing (which may encode tiebreakers we don't model), not a sim.
    lg = League(espn_league_id="224", season=2026, is_public=True, lifecycle="in_season",
                size=4, playoff_team_count=2)
    db_session.add(lg)
    db_session.flush()
    for i in range(1, 5):
        db_session.add(Team(league_id=lg.id, espn_team_id=i, name=f"T{i}", wins=1, losses=0,
                            ties=0, points_for=100.0, standing=i))
    db_session.flush()
    ids = {
        espn: pk
        for pk, espn in db_session.execute(
            select(Team.id, Team.espn_team_id).where(Team.league_id == lg.id)
        )
    }
    lg.my_team_id = ids[1]
    # Two fully-played weeks, no remaining regular-season games.
    db_session.add_all([
        Matchup(league_id=lg.id, week=1, home_team_id=ids[1], away_team_id=ids[2],
                home_points=110.0, away_points=100.0),
        Matchup(league_id=lg.id, week=1, home_team_id=ids[3], away_team_id=ids[4],
                home_points=95.0, away_points=90.0),
        Matchup(league_id=lg.id, week=2, home_team_id=ids[1], away_team_id=ids[3],
                home_points=105.0, away_points=99.0),
        Matchup(league_id=lg.id, week=2, home_team_id=ids[2], away_team_id=ids[4],
                home_points=101.0, away_points=88.0),
    ])
    db_session.flush()
    metrics.recompute_league(db_session, lg, completed_weeks={1, 2})
    db_session.commit()
    for espn in (1, 2, 3, 4):  # this fixture sets standing == espn_team_id
        odds = team_edge(db_session, lg.id, ids[espn]).playoff_odds
        assert odds == (1.0 if espn <= 2 else 0.0)


def test_recompute_is_idempotent(db_session):
    lg = _make_inseason_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    # No duplicate rows (partial unique index holds). 4 teams × 5 keys
    # (edge_score + playoff_odds + 3 in-season components) = 20, stable across recomputes.
    assert db_session.scalar(select(func.count()).select_from(Metric).where(Metric.league_id == lg.id)) == 20


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


# --------------------------------------------------------------------------- #
# Phase 9: component persistence, invalidation, branch switching
# --------------------------------------------------------------------------- #
def _component_row_count(session, league_id) -> int:
    return session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == league_id, Metric.key.startswith("edge_component_")
        )
    )


def test_components_persist_and_read_back(db_session):
    lg = _make_inseason_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    # 4 teams × 3 in-season components.
    assert _component_row_count(db_session, lg.id) == 12
    comps = team_components(db_session, lg.id, lg.my_team_id)
    assert [c.key for c in comps] == ["win_pct", "points_for", "point_diff"]  # canonical order
    assert comps[0].percentile == 75.0 and comps[1].percentile == 87.5 and comps[2].percentile == 87.5
    # Reduction of the persisted components equals the persisted edge_score.
    assert round(sum(c.weight * c.percentile for c in comps), 1) == team_edge(
        db_session, lg.id, lg.my_team_id
    ).edge_score


def test_components_clear_when_pending(db_session):
    lg = _make_inseason_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert _component_row_count(db_session, lg.id) == 12

    lg.lifecycle = "pre_draft"  # → all pending
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert _component_row_count(db_session, lg.id) == 0
    assert team_components(db_session, lg.id, lg.my_team_id) == []


def test_components_clear_when_projections_stale(db_session):
    lg = _make_drafted_league(db_session)
    metrics.recompute_league(db_session, lg, projections_fresh=True)
    db_session.commit()
    assert [c.key for c in team_components(db_session, lg.id, lg.my_team_id)] == ["roster_proj"]

    metrics.recompute_league(db_session, lg, projections_fresh=False)
    db_session.commit()
    assert _component_row_count(db_session, lg.id) == 0
    assert team_components(db_session, lg.id, lg.my_team_id) == []


def test_components_branch_switch_clears_previous_keys(db_session):
    # Drafted → roster_proj component persisted.
    lg = _make_drafted_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert [c.key for c in team_components(db_session, lg.id, lg.my_team_id)] == ["roster_proj"]

    # League starts playing (give teams records so the record branch activates).
    lg.lifecycle = "in_season"
    for t in db_session.scalars(select(Team).where(Team.league_id == lg.id)):
        t.wins = 1 if t.espn_team_id <= 2 else 0
        t.losses = 0 if t.espn_team_id <= 2 else 1
        t.points_for = 100.0 + t.espn_team_id
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    keys = {c.key for c in team_components(db_session, lg.id, lg.my_team_id)}
    assert keys == {"win_pct", "points_for", "point_diff"}
    # The stale roster_proj row from the drafted branch is gone.
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "edge_component_roster_proj"
        )
    ) == 0


def test_api_overview_exposes_components(synced_league_id):
    ov = client.get(f"/api/leagues/{synced_league_id}/overview").json()
    assert "components" in ov
    keys = {c["key"] for c in ov["components"]}
    assert keys == {"win_pct", "points_for", "point_diff"}
    for c in ov["components"]:
        assert c["label"] and c["weight"] > 0 and 0.0 <= c["percentile"] <= 100.0
    # Existing metrics unchanged (byte-identical edge_score).
    assert ov["edge_score"] == 82.5 and ov["grade"] == "A" and ov["verdict"] == "advantaged"


# --------------------------------------------------------------------------- #
# Phase 10: draft-value foundation (pure + persistence)
# --------------------------------------------------------------------------- #
def test_pick_value_curve():
    assert metrics.pick_value(0) == 100.0  # 100·e^0
    assert metrics.pick_value(34) == pytest.approx(100 * math.exp(-1))  # one decay length
    # Earlier picks are worth more (monotonically decreasing).
    assert metrics.pick_value(1) > metrics.pick_value(10) > metrics.pick_value(100)


def test_pick_surplus_and_team_surplus():
    assert metrics.pick_surplus(None, 5.0) is None
    assert metrics.pick_surplus(5.0, None) is None
    # Player with ADP 10 taken at overall 20 (fell 10 spots) → value captured, positive.
    s = metrics.pick_surplus(10.0, 20.0)
    assert s == pytest.approx(metrics.pick_value(10.0) - metrics.pick_value(20.0))
    assert s > 0
    # Team surplus sums known picks, ignores those missing ADP/overall.
    picks = [(10.0, 20.0), (None, 3.0), (5.0, 4.0)]
    expected = round(
        (metrics.pick_value(10.0) - metrics.pick_value(20.0))
        + (metrics.pick_value(5.0) - metrics.pick_value(4.0)),
        3,
    )
    assert metrics.compute_draft_surplus(picks) == expected
    # No pick has both values → None (metric cleared).
    assert metrics.compute_draft_surplus([(None, 1.0), (2.0, None)]) is None


def test_draft_surplus_persists_and_clears(db_session):
    lg = _make_drafted_league(db_session)
    # Stamp ADP earlier than the pick (adp < overall) so surplus is positive.
    for p in db_session.scalars(select(DraftPick).where(DraftPick.league_id == lg.id)):
        p.adp_at_draft = p.overall * 0.5
        p.value_delta = round(p.adp_at_draft - p.overall, 1)
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    surplus = db_session.scalar(
        select(Metric.value_float).where(
            Metric.league_id == lg.id,
            Metric.team_id == lg.my_team_id,
            Metric.key == "draft_surplus",
            Metric.week.is_(None),
        )
    )
    # My team has one pick (overall 1, adp 0.5) → equals the pure computation, positive.
    assert surplus == pytest.approx(metrics.compute_draft_surplus([(0.5, 1)]))
    assert surplus > 0

    # Remove ADP → no valid pick values → the metric is cleared.
    for p in db_session.scalars(select(DraftPick).where(DraftPick.league_id == lg.id)):
        p.adp_at_draft = None
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "draft_surplus"
        )
    ) == 0
