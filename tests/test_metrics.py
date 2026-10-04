"""Phase 3 analytics tests — pure computation, persistence, invalidation, API."""

import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.edge_config import (
    DRAFTED_WEIGHTS,
    INSEASON_WEIGHTS,
    LEAGUE_SOFTNESS_ORDER,
    MY_EDGE_WEIGHTS,
    grade_for,
    verdict_for,
)
from api.main import app
from api.models import (
    Account,
    DraftPick,
    League,
    LineupSlot,
    Matchup,
    Metric,
    Player,
    Team,
    Transaction,
)
from api.services import metrics
from api.services.metrics import TeamStat, team_components, team_edge
from api.services.playoff_sim import SimGame, SimTeam, simulate_playoff_odds
from api.services.sync import SyncService
from api.tenancy import resolve_tenant_id

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
# Phase 11: preseason Edge blends roster projection + draft surplus
# --------------------------------------------------------------------------- #
def _preseason_stats():
    # 8th positional arg is roster_proj, 9th is draft_surplus.
    return [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0, 10.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0, 5.0),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0, 20.0),
    ]


def test_preseason_components_blend_roster_and_surplus_hand_computed():
    teams = _preseason_stats()
    comps = metrics.compute_edge_components(teams, "drafted")
    assert [c.key for c in comps[1]] == ["roster_proj", "draft_surplus"]
    by = {c.key: c for c in comps[1]}
    # Both components available (3 teams each) → base weights already sum to 1.0.
    assert by["roster_proj"].weight == pytest.approx(DRAFTED_WEIGHTS["roster_proj"])
    assert by["draft_surplus"].weight == pytest.approx(DRAFTED_WEIGHTS["draft_surplus"])
    rp = metrics._percentile([300.0, 200.0, 100.0], 300.0)  # 83.33
    ds = metrics._percentile([10.0, 5.0, 20.0], 10.0)  # 50.0
    assert by["roster_proj"].percentile == rp
    assert by["draft_surplus"].percentile == ds
    expected = round(DRAFTED_WEIGHTS["roster_proj"] * rp + DRAFTED_WEIGHTS["draft_surplus"] * ds, 1)
    assert metrics.compute_edge_scores(teams, "drafted")[1] == expected


def test_preseason_roster_only_unchanged_when_surplus_unavailable():
    # No draft_surplus values → surplus unavailable → roster-only, weight renormalized to 1.0
    # (byte-identical to the pre-Phase-11 roster-only preseason score).
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0),
    ]
    comps = metrics.compute_edge_components(teams, "drafted")
    assert [c.key for c in comps[1]] == ["roster_proj"]
    assert comps[1][0].weight == 1.0
    assert metrics.compute_edge_scores(teams, "drafted")[1] == round(
        metrics._percentile([300.0, 200.0, 100.0], 300.0), 1
    )


def test_preseason_surplus_needs_two_teams_with_values():
    # Only one team has a draft_surplus → not enough population → surplus unavailable.
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0, 10.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0, None),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0, None),
    ]
    comps = metrics.compute_edge_components(teams, "drafted")
    assert [c.key for c in comps[1]] == ["roster_proj"]
    assert comps[1][0].weight == 1.0


def test_preseason_both_pending_when_projections_stale():
    teams = _preseason_stats()
    assert metrics.compute_edge_components(teams, "drafted", projections_fresh=False) == {
        1: [],
        2: [],
        3: [],
    }


def test_preseason_team_missing_surplus_uses_present_component_only():
    # Team 3 lacks a surplus value but the component is available league-wide (teams 1,2);
    # team 3 falls back to roster-only at weight 1.0, others blend both.
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, 300.0, 10.0),
        TeamStat(2, 0, 0, 0, 0, 0, None, 200.0, 5.0),
        TeamStat(3, 0, 0, 0, 0, 0, None, 100.0, None),
    ]
    comps = metrics.compute_edge_components(teams, "drafted")
    assert [c.key for c in comps[1]] == ["roster_proj", "draft_surplus"]
    assert [c.key for c in comps[3]] == ["roster_proj"]
    assert comps[3][0].weight == 1.0


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
    lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="222", season=2026, is_public=True, lifecycle="in_season",
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
    lg = League(tenant_id=resolve_tenant_id(db_session), espn_league_id="223", season=2026, is_public=True, lifecycle="in_season",
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
    lg = League(tenant_id=resolve_tenant_id(db_session), espn_league_id="224", season=2026, is_public=True, lifecycle="in_season",
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
    # No duplicate rows (partial unique index holds). 4 teams × 17 keys (edge_score +
    # playoff_odds + 3 in-season components + 5 all-play/luck + my_edge_score + 1 MyEdge
    # component + league_softness_score + 1 softness component + edge_index_score + 2 Edge
    # Index components) = 68, stable across recomputes.
    assert db_session.scalar(select(func.count()).select_from(Metric).where(Metric.league_id == lg.id)) == 68


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
        acct = Account(
            tenant_id=resolve_tenant_id(session),
            label="Main",
            swid="{AAAA-1111}",
            espn_s2_encrypted=encrypt("s2"),
        )
        session.add(acct)
        session.flush()
        lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="111", season=2026, account_id=acct.id, is_public=False)
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
    # Legacy edge_score aggregates unchanged.
    assert s["scored_count"] == 1
    assert s["advantaged_count"] == 1
    assert s["best_edge_score"] == 82.5 and s["worst_edge_score"] == 82.5
    # Phase 17: Edge Index aggregates present (primary). The synced league scores an Edge
    # Index for my team, so it's counted.
    assert s["edge_index_scored_count"] == 1
    assert "edge_index_advantaged_count" in s
    assert s["best_edge_index_score"] is not None
    assert s["best_edge_index_score"] == s["worst_edge_index_score"]  # single scored league


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
    lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="333", season=2026, is_public=True, lifecycle="drafted",
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


# --------------------------------------------------------------------------- #
# Phase 11: preseason draft-surplus component persistence + API exposure
# --------------------------------------------------------------------------- #
def _stamp_adp(session, league_id) -> None:
    for p in session.scalars(select(DraftPick).where(DraftPick.league_id == league_id)):
        p.adp_at_draft = p.overall + 3.0  # some ADP so the surplus is defined
    session.flush()


def test_preseason_draft_surplus_component_persists_and_clears(db_session):
    lg = _make_drafted_league(db_session)
    _stamp_adp(db_session, lg.id)
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    # Both preseason components persist for my team (roster_proj + draft_surplus).
    assert {c.key for c in team_components(db_session, lg.id, lg.my_team_id)} == {
        "roster_proj",
        "draft_surplus",
    }
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "edge_component_draft_surplus"
        )
    ) == 4
    # edge_score is the weighted mean of the two persisted components.
    comps = team_components(db_session, lg.id, lg.my_team_id)
    assert round(sum(c.weight * c.percentile for c in comps), 1) == team_edge(
        db_session, lg.id, lg.my_team_id
    ).edge_score

    # Branch switch to in_season (with games) clears the preseason draft_surplus component.
    lg.lifecycle = "in_season"
    for t in db_session.scalars(select(Team).where(Team.league_id == lg.id)):
        t.wins = 1 if t.espn_team_id <= 2 else 0
        t.losses = 0 if t.espn_team_id <= 2 else 1
        t.points_for = 100.0 + t.espn_team_id
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "edge_component_draft_surplus"
        )
    ) == 0
    assert {c.key for c in team_components(db_session, lg.id, lg.my_team_id)} == {
        "win_pct",
        "points_for",
        "point_diff",
    }

    # Reverting to pre_draft clears all components.
    lg.lifecycle = "pre_draft"
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert team_components(db_session, lg.id, lg.my_team_id) == []


@pytest.fixture
def preseason_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="909", season=2026, is_public=True, lifecycle="drafted",
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
        for pid, proj in [(201, 300.0), (202, 250.0), (203, 200.0), (204, 150.0)]:
            session.add(Player(espn_player_id=pid, name=f"P{pid}", position="RB", proj_ros=proj))
        session.flush()
        for i, t in enumerate(teams, start=1):
            session.add(
                DraftPick(league_id=lg.id, overall=i, team_id=t.id, espn_player_id=200 + i,
                          adp_at_draft=float(i) + 2.0, value_delta=2.0)
            )
        session.flush()
        metrics.recompute_league(session, lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_api_overview_exposes_draft_surplus_component(preseason_league_id):
    ov = client.get(f"/api/leagues/{preseason_league_id}/overview").json()
    keys = {c["key"] for c in ov["components"]}
    assert {"roster_proj", "draft_surplus"} <= keys
    ds = next(c for c in ov["components"] if c["key"] == "draft_surplus")
    assert ds["label"] == "Draft surplus"
    assert ds["weight"] > 0 and 0.0 <= ds["percentile"] <= 100.0
    # Persisted components reduce to the exposed edge_score.
    reduced = round(sum(c["weight"] * c["percentile"] for c in ov["components"]), 1)
    assert reduced == ov["edge_score"]


# --------------------------------------------------------------------------- #
# Phase 12: all-play record + luck delta
# --------------------------------------------------------------------------- #
def test_all_play_and_luck_hand_computed():
    # 4 teams, 2 completed weeks. Actual records chosen to make luck deltas clean.
    teams = [
        TeamStat(1, 1, 1, 0, 0, 0, None, None),  # actual .500
        TeamStat(2, 2, 0, 0, 0, 0, None, None),  # actual 1.000
        TeamStat(3, 0, 2, 0, 0, 0, None, None),  # actual .000
        TeamStat(4, 1, 1, 0, 0, 0, None, None),  # actual .500
    ]
    weekly = {
        1: {1: 120.0, 2: 100.0, 3: 90.0, 4: 80.0},
        2: {1: 80.0, 2: 110.0, 3: 95.0, 4: 105.0},
    }
    ap = metrics.compute_all_play(teams, weekly)
    # T1: wk1 3-0, wk2 0-3 → 3-3 → .500; luck .500-.500 = 0.
    assert (ap[1].all_play_wins, ap[1].all_play_losses, ap[1].all_play_ties) == (3, 3, 0)
    assert ap[1].all_play_win_pct == 0.5 and ap[1].luck_delta == 0.0
    # T2: wk1 2-1, wk2 3-0 → 5-1 → .8333; luck .8333-1.0 = -.1667.
    assert (ap[2].all_play_wins, ap[2].all_play_losses) == (5, 1)
    assert ap[2].all_play_win_pct == 0.8333 and ap[2].luck_delta == -0.1667
    # T3: wk1 1-2, wk2 1-2 → 2-4 → .3333; luck .3333-0.0 = .3333 (unlucky, better than record).
    assert ap[3].all_play_win_pct == 0.3333 and ap[3].luck_delta == 0.3333
    # T4: wk1 0-3, wk2 2-1 → 2-4 → .3333.
    assert (ap[4].all_play_wins, ap[4].all_play_losses) == (2, 4)


def test_all_play_skips_thin_weeks_and_pending_team():
    teams = [
        TeamStat(1, 0, 0, 0, 0, 0, None, None),
        TeamStat(2, 0, 0, 0, 0, 0, None, None),
        TeamStat(3, 0, 0, 0, 0, 0, None, None),
    ]
    weekly = {
        1: {1: 100.0},           # only one scored team → week skipped
        2: {1: 100.0, 2: 90.0},  # T3 didn't play
    }
    ap = metrics.compute_all_play(teams, weekly)
    assert (ap[1].all_play_wins, ap[1].all_play_losses) == (1, 0)
    assert ap[2].all_play_losses == 1
    # T3 never played → no sample.
    assert ap[3].all_play_win_pct is None and ap[3].luck_delta is None


def test_all_play_metrics_persist_and_clear(db_session):
    lg = _make_inseason_league(db_session)  # week 1 completed, week 2 remaining (0-0)
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    rows = metrics.read_all_play(db_session, lg.id)
    pcts = [r.all_play_win_pct for r in rows]
    assert pcts == sorted(pcts, reverse=True)  # ordered by all-play win% desc
    me = next(r for r in rows if r.team_id == lg.my_team_id)
    # My team (top score week 1) beat all 3 others → 3-0, undefeated all-play, luck 0.
    assert (me.all_play_wins, me.all_play_losses, me.all_play_ties) == (3, 0, 0)
    assert me.all_play_win_pct == 1.0 and me.luck_delta == 0.0
    # 4 teams × 5 all-play keys persisted.
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._ALL_PLAY_KEYS))
        )
    ) == 20

    # Zero the completed scores → no all-play sample → metrics cleared.
    for m in db_session.scalars(select(Matchup).where(Matchup.league_id == lg.id)):
        m.home_points = 0.0
        m.away_points = 0.0
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._ALL_PLAY_KEYS))
        )
    ) == 0
    assert metrics.read_all_play(db_session, lg.id) == []


def test_api_all_play_exposes_rows(synced_league_id):
    rows = client.get(f"/api/leagues/{synced_league_id}/all-play").json()
    assert rows, "expected all-play rows for a league that has played"
    pcts = [r["all_play_win_pct"] for r in rows]
    assert pcts == sorted(pcts, reverse=True)
    fields = {
        "team_id", "team_name", "wins", "losses", "ties", "win_pct",
        "all_play_wins", "all_play_losses", "all_play_ties", "all_play_win_pct", "luck_delta",
    }
    for r in rows:
        assert fields <= set(r)
        assert r["team_name"]


# --------------------------------------------------------------------------- #
# Phase 13: lineup efficiency (pure solver + persistence + API)
# --------------------------------------------------------------------------- #
def test_optimal_lineup_flex_and_positions():
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "D/ST": 1}
    entries = [
        metrics.LineupEntry("QB", 25.0, True),
        metrics.LineupEntry("RB", 20.0, True),
        metrics.LineupEntry("RB", 15.0, True),
        metrics.LineupEntry("RB", 12.0, False),
        metrics.LineupEntry("WR", 18.0, True),
        metrics.LineupEntry("WR", 10.0, True),
        metrics.LineupEntry("WR", 8.0, False),
        metrics.LineupEntry("TE", 9.0, True),
        metrics.LineupEntry("TE", 5.0, False),
        metrics.LineupEntry("K", 7.0, True),
        metrics.LineupEntry("D/ST", 6.0, True),
    ]
    # QB 25 + RB(20,15) + WR(18,10) + TE 9 + K 7 + DST 6 + FLEX best remaining (RB 12) = 122.
    assert metrics.optimal_lineup_points(entries, slots) == 122.0


def test_optimal_ignores_unknown_positions():
    slots = {"RB": 1, "FLEX": 1}
    entries = [
        metrics.LineupEntry("RB", 10.0, True),
        metrics.LineupEntry(None, 99.0, False),  # unknown position → never placed
        metrics.LineupEntry("LB", 88.0, False),  # IDP → never placed
        metrics.LineupEntry("WR", 7.0, False),
    ]
    # RB slot 10; FLEX best remaining RB/WR/TE = WR 7. Unknowns excluded.
    assert metrics.optimal_lineup_points(entries, slots) == 17.0


def test_compute_lineup_week_efficiency_and_pending():
    slots = {"QB": 1, "RB": 1, "FLEX": 1}
    entries = [
        metrics.LineupEntry("QB", 20.0, True),
        metrics.LineupEntry("RB", 8.0, True),  # started a weak RB
        metrics.LineupEntry("RB", 14.0, False),  # better RB benched
        metrics.LineupEntry("WR", 6.0, False),
    ]
    wk = metrics.compute_lineup_week(entries, slots)
    # started 20+8=28; optimal QB 20 + RB 14 + FLEX best remaining (RB 8) = 42.
    assert wk.started_points == 28.0
    assert wk.optimal_points == 42.0
    assert wk.points_left_on_bench == 14.0
    assert wk.lineup_efficiency == round(28 / 42, 4)
    # No known-position points → optimal 0 → pending.
    assert metrics.compute_lineup_week([metrics.LineupEntry(None, 5.0, True)], slots) is None


def test_compute_lineup_efficiency_points_weighted():
    slots = {"QB": 1}
    team_weeks = {
        1: {
            1: [metrics.LineupEntry("QB", 10.0, True), metrics.LineupEntry("QB", 20.0, False)],
            2: [metrics.LineupEntry("QB", 30.0, True), metrics.LineupEntry("QB", 30.0, False)],
        }
    }
    r = metrics.compute_lineup_efficiency(team_weeks, slots)[1]
    assert r.weeks == 2
    assert r.lineup_efficiency == 0.8  # (10+30)/(20+30)
    assert r.started_points_avg == 20.0 and r.optimal_points_avg == 25.0
    assert r.points_left_on_bench_avg == 5.0


def _make_lineup_league(session) -> League:
    lg = League(tenant_id=resolve_tenant_id(session), 
        espn_league_id="777", season=2026, is_public=True, lifecycle="in_season",
        size=2, playoff_team_count=1, lineup_slots_json={"0": 1, "23": 1},  # QB + FLEX
    )
    session.add(lg)
    session.flush()
    t1 = Team(league_id=lg.id, espn_team_id=1, name="A", is_me=True)
    t2 = Team(league_id=lg.id, espn_team_id=2, name="B")
    session.add_all([t1, t2])
    session.flush()
    lg.my_team_id = t1.id
    for pid, pos in [(1, "QB"), (2, "RB"), (3, "WR"), (11, "QB"), (12, "RB")]:
        session.add(Player(espn_player_id=pid, name=f"P{pid}", position=pos))
    session.flush()
    session.add_all([
        # Team 1: started QB 10 + FLEX(RB) 5; WR 8 benched (better FLEX option).
        LineupSlot(league_id=lg.id, team_id=t1.id, week=1, slot="QB", espn_player_id=1, points=10.0, is_starter=True),
        LineupSlot(league_id=lg.id, team_id=t1.id, week=1, slot="FLEX", espn_player_id=2, points=5.0, is_starter=True),
        LineupSlot(league_id=lg.id, team_id=t1.id, week=1, slot="BE", espn_player_id=3, points=8.0, is_starter=False),
        # Team 2: optimal already (QB 20 + FLEX RB 12).
        LineupSlot(league_id=lg.id, team_id=t2.id, week=1, slot="QB", espn_player_id=11, points=20.0, is_starter=True),
        LineupSlot(league_id=lg.id, team_id=t2.id, week=1, slot="FLEX", espn_player_id=12, points=12.0, is_starter=True),
    ])
    session.flush()
    return lg


def test_lineup_efficiency_persists_and_clears(db_session):
    lg = _make_lineup_league(db_session)
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    rows = metrics.read_lineup_efficiency(db_session, lg.id)
    effs = [r.lineup_efficiency for r in rows]
    assert effs == sorted(effs, reverse=True)  # ordered by efficiency desc
    me = next(r for r in rows if r.team_id == lg.my_team_id)
    # Team 1: started 15, optimal QB 10 + FLEX best (WR 8) = 18 → eff 15/18, bench 3.
    assert me.started_points_avg == 15.0 and me.optimal_points_avg == 18.0
    assert me.points_left_on_bench_avg == 3.0
    assert me.lineup_efficiency == round(15 / 18, 4)
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._LINEUP_KEYS))
        )
    ) == 8  # 2 teams × 4 keys

    # No slot map → no sample → all lineup metrics cleared.
    lg.lineup_slots_json = None
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._LINEUP_KEYS))
        )
    ) == 0
    assert metrics.read_lineup_efficiency(db_session, lg.id) == []


@pytest.fixture
def lineup_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        lg = _make_lineup_league(session)
        metrics.recompute_league(session, lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_api_lineup_efficiency_exposes_rows(lineup_league_id):
    rows = client.get(f"/api/leagues/{lineup_league_id}/lineup-efficiency").json()
    assert rows, "expected lineup-efficiency rows"
    effs = [r["lineup_efficiency"] for r in rows]
    assert effs == sorted(effs, reverse=True)
    fields = {
        "team_id", "team_name", "lineup_efficiency",
        "started_points_avg", "optimal_points_avg", "points_left_on_bench_avg",
    }
    for r in rows:
        assert fields <= set(r)
        assert r["team_name"]


# --------------------------------------------------------------------------- #
# Phase 14: MyEdge v1 (pure blend + persistence + API)
# --------------------------------------------------------------------------- #
def test_my_edge_blend_hand_computed():
    inputs = [
        metrics.MyEdgeInput(1, 300.0, 10.0, 0.9, 1.0),
        metrics.MyEdgeInput(2, 200.0, 5.0, 0.8, 0.5),
        metrics.MyEdgeInput(3, 100.0, 20.0, 0.7, 0.0),
    ]
    r = metrics.compute_my_edge(inputs)[1]
    assert [c.key for c in r.components] == [
        "roster_strength", "draft_surplus", "lineup_efficiency", "luck_adjusted_record",
    ]
    base_total = sum(MY_EDGE_WEIGHTS.values())  # 0.90 (waiver_capture pending)
    for c in r.components:
        assert c.weight == pytest.approx(MY_EDGE_WEIGHTS[c.key] / base_total)
    pcts = {
        "roster_strength": metrics._percentile([300.0, 200.0, 100.0], 300.0),
        "draft_surplus": metrics._percentile([10.0, 5.0, 20.0], 10.0),
        "lineup_efficiency": metrics._percentile([0.9, 0.8, 0.7], 0.9),
        "luck_adjusted_record": metrics._percentile([1.0, 0.5, 0.0], 1.0),
    }
    for c in r.components:
        assert c.percentile == pcts[c.key]
    expected = round(sum(MY_EDGE_WEIGHTS[k] / base_total * pcts[k] for k in pcts), 1)
    assert r.my_edge_score == expected


def test_my_edge_drops_unavailable_component_and_renormalizes():
    # lineup_efficiency only on one team → unavailable; the other three are available.
    inputs = [
        metrics.MyEdgeInput(1, 300.0, 10.0, 0.9, 1.0),
        metrics.MyEdgeInput(2, 200.0, 5.0, None, 0.5),
        metrics.MyEdgeInput(3, 100.0, 20.0, None, 0.0),
    ]
    r = metrics.compute_my_edge(inputs)[1]
    assert [c.key for c in r.components] == ["roster_strength", "draft_surplus", "luck_adjusted_record"]
    assert sum(c.weight for c in r.components) == pytest.approx(1.0)  # renormalized


def test_my_edge_roster_strength_absent_when_projections_none():
    # roster_strength None on all teams (stale projections) → dropped from MyEdge.
    inputs = [
        metrics.MyEdgeInput(1, None, 10.0, 0.9, 1.0),
        metrics.MyEdgeInput(2, None, 5.0, 0.8, 0.5),
    ]
    r = metrics.compute_my_edge(inputs)[1]
    assert all(c.key != "roster_strength" for c in r.components)


def test_my_edge_pending_when_nothing_available():
    inputs = [
        metrics.MyEdgeInput(1, None, None, None, None),
        metrics.MyEdgeInput(2, None, None, None, None),
    ]
    r = metrics.compute_my_edge(inputs)[1]
    assert r.my_edge_score is None and r.components == []


def test_my_edge_persists_and_clears(db_session):
    lg = _make_inseason_league(db_session)  # matchups → luck_adjusted_record available
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    rows = metrics.read_my_edge(db_session, lg.id)
    scores = [r.my_edge_score for r in rows]
    assert scores == sorted(scores, reverse=True)
    me = next(r for r in rows if r.team_id == lg.my_team_id)
    # Only luck_adjusted_record is available for this fixture → single component, weight 1.0.
    assert [c.key for c in me.components] == ["luck_adjusted_record"]
    assert me.components[0].weight == 1.0
    assert me.my_edge_score == me.components[0].percentile  # round(1.0 * pct, 1)
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "my_edge_score"
        )
    ) == 4

    # Zero completed scores → no all-play → luck unavailable → MyEdge cleared.
    for m in db_session.scalars(select(Matchup).where(Matchup.league_id == lg.id)):
        m.home_points = 0.0
        m.away_points = 0.0
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._MY_EDGE_ALL_KEYS))
        )
    ) == 0
    assert metrics.read_my_edge(db_session, lg.id) == []


def test_my_edge_stale_projections_drops_roster_strength(db_session):
    lg = _make_drafted_league(db_session)  # roster_proj + (stamped) draft_surplus
    for p in db_session.scalars(select(DraftPick).where(DraftPick.league_id == lg.id)):
        p.adp_at_draft = p.overall + 3.0
    db_session.flush()

    metrics.recompute_league(db_session, lg, projections_fresh=True)
    db_session.commit()
    fresh_keys = {c.key for c in metrics.read_my_edge(db_session, lg.id)[0].components}
    assert {"roster_strength", "draft_surplus"} <= fresh_keys

    metrics.recompute_league(db_session, lg, projections_fresh=False)
    db_session.commit()
    for r in metrics.read_my_edge(db_session, lg.id):
        assert all(c.key != "roster_strength" for c in r.components)
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "my_edge_component_roster_strength"
        )
    ) == 0


def test_api_my_edge_exposes_rows(synced_league_id):
    rows = client.get(f"/api/leagues/{synced_league_id}/my-edge").json()
    assert rows, "expected MyEdge rows for a synced league"
    scores = [r["my_edge_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    assert any(r["is_me"] for r in rows)
    for r in rows:
        assert {"team_id", "team_name", "is_me", "my_edge_score", "components"} <= set(r)
        for c in r["components"]:
            assert {"key", "label", "weight", "percentile"} <= set(c)


# --------------------------------------------------------------------------- #
# Phase 15: LeagueSoftness v1 (pure blend + persistence + API)
# --------------------------------------------------------------------------- #
def _softness_stats():
    # SoftnessTeam: team_id, points_for, played, autodrafted, lineup_eff, draft_surplus, txns.
    return [
        metrics.SoftnessTeam(1, 130.0, True, False, 0.9, 10.0, 3),
        metrics.SoftnessTeam(2, 110.0, True, False, 0.8, 5.0, 2),
        metrics.SoftnessTeam(3, 90.0, True, True, 0.7, 0.0, 1),
        metrics.SoftnessTeam(4, 70.0, True, True, 0.6, -5.0, 0),
    ]


def test_league_softness_blend_hand_computed():
    r = metrics.compute_league_softness(_softness_stats())[1]
    # All five components are available (each has ≥2 distinct opponent-derived values).
    assert [c.key for c in r.components] == list(LEAGUE_SOFTNESS_ORDER)
    for c in r.components:
        assert c.weight == pytest.approx(0.2)  # equal weights, 5 present → 1/5 each
    # T1 opponent-derived percentiles: lineup 75, draft 75, weakness 62.5, abandoned 75,
    # inactivity 75 → mean = 72.5.
    assert r.league_softness_score == 72.5


def test_league_softness_drops_unavailable_and_renormalizes():
    # No lineup/draft data → those two components unavailable; three remain.
    teams = [
        metrics.SoftnessTeam(1, 130.0, True, False, None, None, 3),
        metrics.SoftnessTeam(2, 110.0, True, False, None, None, 2),
        metrics.SoftnessTeam(3, 90.0, True, True, None, None, 1),
        metrics.SoftnessTeam(4, 70.0, True, True, None, None, 0),
    ]
    r = metrics.compute_league_softness(teams)[1]
    keys = {c.key for c in r.components}
    assert keys == {"exploitable_weakness_share", "abandoned_proxy", "opponent_inactivity"}
    assert sum(c.weight for c in r.components) == pytest.approx(1.0)
    for c in r.components:
        assert c.weight == pytest.approx(1 / 3)  # equal, renormalized across the present 3


def test_league_softness_omits_inactivity_when_no_transaction_data():
    # transactions=None on every team → an empty feed is unknown, not zero activity.
    teams = [
        metrics.SoftnessTeam(1, 130.0, True, False, 0.9, 10.0, None),
        metrics.SoftnessTeam(2, 110.0, True, False, 0.8, 5.0, None),
        metrics.SoftnessTeam(3, 90.0, True, True, 0.7, 0.0, None),
        metrics.SoftnessTeam(4, 70.0, True, True, 0.6, -5.0, None),
    ]
    for r in metrics.compute_league_softness(teams).values():
        assert all(c.key != "opponent_inactivity" for c in r.components)


def test_league_softness_pending_when_nothing_available():
    teams = [
        metrics.SoftnessTeam(1, 0.0, False, False, None, None, None),
        metrics.SoftnessTeam(2, 0.0, False, False, None, None, None),
    ]
    r = metrics.compute_league_softness(teams)[1]
    assert r.league_softness_score is None and r.components == []


def test_league_softness_persists_and_clears(db_session):
    lg = _make_inseason_league(db_session)  # distinct PF → exploitable_weakness_share available
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    rows = metrics.read_league_softness(db_session, lg.id)
    scores = [r.league_softness_score for r in rows]
    assert scores == sorted(scores, reverse=True)
    me = next(r for r in rows if r.team_id == lg.my_team_id)
    # Only exploitable_weakness_share is available for this fixture → single component, w=1.0.
    assert [c.key for c in me.components] == ["exploitable_weakness_share"]
    assert me.components[0].weight == 1.0
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "league_softness_score"
        )
    ) == 4

    # Identical points_for → no distinct weakness share, nothing else → all softness cleared.
    for t in db_session.scalars(select(Team).where(Team.league_id == lg.id)):
        t.points_for = 100.0
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._LEAGUE_SOFTNESS_ALL_KEYS))
        )
    ) == 0
    assert metrics.read_league_softness(db_session, lg.id) == []


def _make_softness_league(session) -> League:
    lg = League(tenant_id=resolve_tenant_id(session), 
        espn_league_id="890", season=2026, is_public=True, lifecycle="in_season",
        size=4, playoff_team_count=2,
    )
    session.add(lg)
    session.flush()
    teams = []
    for espn, pf, auto in [(1, 130.0, False), (2, 110.0, False), (3, 90.0, True), (4, 70.0, True)]:
        t = Team(
            league_id=lg.id, espn_team_id=espn, name=f"S{espn}", is_me=(espn == 1),
            autodrafted=auto, wins=1, losses=1, ties=0, points_for=pf,
        )
        session.add(t)
        teams.append(t)
    session.flush()
    lg.my_team_id = teams[0].id
    # Some transactions so the league has real activity data (T1 x2, T2 x1).
    for t, count in [(teams[0], 2), (teams[1], 1)]:
        for _ in range(count):
            session.add(Transaction(league_id=lg.id, team_id=t.id, type="fa_add"))
    session.flush()
    return lg


@pytest.fixture
def softness_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        lg = _make_softness_league(session)
        metrics.recompute_league(session, lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_api_league_softness_exposes_rows(softness_league_id):
    rows = client.get(f"/api/leagues/{softness_league_id}/league-softness").json()
    assert rows, "expected LeagueSoftness rows"
    scores = [r["league_softness_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    assert any(r["is_me"] for r in rows)
    for r in rows:
        assert {"team_id", "team_name", "is_me", "league_softness_score", "components"} <= set(r)
        for c in r["components"]:
            assert {"key", "label", "weight", "percentile"} <= set(c)


# --------------------------------------------------------------------------- #
# Phase 16: full Edge Index composite (0.5·MyEdge + 0.5·LeagueSoftness)
# --------------------------------------------------------------------------- #
def test_edge_index_both_halves():
    r = metrics.compute_edge_index_row(1, 70.0, 60.0)
    assert [c.key for c in r.components] == ["my_edge", "league_softness"]
    assert all(c.weight == 0.5 for c in r.components)
    assert r.components[0].value == 70.0 and r.components[1].value == 60.0
    assert r.edge_index_score == 65.0  # 0.5*70 + 0.5*60
    assert r.grade == grade_for(65.0) and r.verdict == verdict_for(65.0)


def test_edge_index_missing_half_renormalizes():
    r = metrics.compute_edge_index_row(1, 80.0, None)
    assert [c.key for c in r.components] == ["my_edge"]
    assert r.components[0].weight == 1.0 and r.edge_index_score == 80.0
    r2 = metrics.compute_edge_index_row(1, None, 40.0)
    assert [c.key for c in r2.components] == ["league_softness"]
    assert r2.components[0].weight == 1.0 and r2.edge_index_score == 40.0


def test_edge_index_pending_when_neither():
    r = metrics.compute_edge_index_row(1, None, None)
    assert r.edge_index_score is None and r.grade is None
    assert r.verdict is None and r.components == []


def test_edge_index_persists_and_clears(db_session):
    lg = _make_inseason_league(db_session)  # both halves available (MyEdge luck + softness)
    metrics.recompute_league(db_session, lg)
    db_session.commit()

    rows = metrics.read_edge_index(db_session, lg.id)
    scores = [r.edge_index_score for r in rows]
    assert scores == sorted(scores, reverse=True)
    me = next(r for r in rows if r.team_id == lg.my_team_id)
    assert [c.key for c in me.components] == ["my_edge", "league_softness"]
    assert me.grade is not None and me.verdict is not None
    # team_edge_index (portfolio helper) matches the persisted composite.
    assert metrics.team_edge_index(db_session, lg.id, lg.my_team_id).edge_index_score == me.edge_index_score
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key == "edge_index_score"
        )
    ) == 4

    # Remove both halves' inputs (flat PF → no softness; zero scores → no MyEdge luck).
    for t in db_session.scalars(select(Team).where(Team.league_id == lg.id)):
        t.points_for = 100.0
    for m in db_session.scalars(select(Matchup).where(Matchup.league_id == lg.id)):
        m.home_points = 0.0
        m.away_points = 0.0
    db_session.flush()
    metrics.recompute_league(db_session, lg)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Metric).where(
            Metric.league_id == lg.id, Metric.key.in_(list(metrics._EDGE_INDEX_ALL_KEYS))
        )
    ) == 0
    assert metrics.read_edge_index(db_session, lg.id) == []


def test_api_edge_index_exposes_rows(synced_league_id):
    rows = client.get(f"/api/leagues/{synced_league_id}/edge-index").json()
    assert rows, "expected Edge Index rows for a synced league"
    scores = [r["edge_index_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    assert any(r["is_me"] for r in rows)
    fields = {"team_id", "team_name", "is_me", "edge_index_score", "grade", "verdict", "components"}
    for r in rows:
        assert fields <= set(r)
        for c in r["components"]:
            assert {"key", "label", "weight", "value"} <= set(c)


def test_api_portfolio_exposes_edge_index(synced_league_id):
    row = next(
        r for r in client.get("/api/portfolio").json() if r["league_id"] == synced_league_id
    )
    assert {"edge_index_score", "edge_index_grade", "edge_index_verdict"} <= set(row)
    # Existing edge_score display is unchanged (byte-identical).
    assert row["edge_score"] == 82.5
