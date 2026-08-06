"""Phase 23 draft analytics tests — exposure, FFC ADP, strategy fingerprints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.db import Base, SessionLocal, engine, init_db
from api.main import app
from api.models import AdpSnapshot, DraftPick, League, Metric, Player, Team
from api.services import metrics
from api.services.draft_analytics import build_draft_adp, build_strategies
from api.services.exposure import build_exposure, players_for_view
from api.services.ffc_adp import apply_ffc_snapshots_to_players, refresh_ffc_adp
from api.services.metrics import StrategyPick, classify_draft_strategy, pick_value
from api.services.portfolio_filters import PortfolioFilters

from .conftest import load_fixture

client = TestClient(app)

_PCT_KEYS = {"pct", "exposure_pct", "my_exposure_pct", "field_exposure_pct", "penetration_pct"}


def _assert_percentages_capped(value, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, int | float) and (key.endswith("_pct") or key in _PCT_KEYS):
                assert child <= 100.0, f"{path}.{key} exceeds 100: {child}"
            _assert_percentages_capped(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_percentages_capped(child, f"{path}[{index}]")


def _ppr_scoring() -> dict:
    return {"scoringItems": [{"statId": 53, "points": 1.0}]}


def _league(session, espn_id: str, *, draft_type: str = "SNAKE") -> League:
    league = League(
        espn_league_id=espn_id,
        season=2026,
        name=f"League {espn_id}",
        size=10,
        scoring_json=_ppr_scoring(),
        draft_type=draft_type,
        lifecycle="drafted",
    )
    session.add(league)
    session.flush()
    return league


def _team(session, league: League, espn_id: int, name: str, *, me: bool = False) -> Team:
    team = Team(league_id=league.id, espn_team_id=espn_id, name=name, is_me=me)
    session.add(team)
    session.flush()
    if me:
        league.my_team_id = team.id
    return team


def _player(
    session,
    player_id: int,
    name: str,
    position: str,
    team: str,
    *,
    ffc_adp: float | None = None,
) -> Player:
    player = Player(
        espn_player_id=player_id,
        name=name,
        position=position,
        nfl_team=team,
        ffc_adp=ffc_adp,
    )
    session.add(player)
    session.flush()
    return player


@pytest.fixture
def phase23_db():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def test_exposure_truth_table_hand_computed(phase23_db):
    # Two my teams. Bijan appears on both → 2/2 = 100.0%.
    # Amon-Ra appears on one → 1/2 = 50.0%.
    # Bijan avg overall = (1 + 10) / 2 = 5.5.
    l1 = _league(phase23_db, "1")
    l2 = _league(phase23_db, "2")
    me1 = _team(phase23_db, l1, 1, "Me 1", me=True)
    me2 = _team(phase23_db, l2, 1, "Me 2", me=True)
    opp = _team(phase23_db, l1, 2, "Opponent")
    _player(phase23_db, 101, "Bijan Robinson", "RB", "ATL")
    _player(phase23_db, 102, "Amon-Ra St. Brown", "WR", "DET")
    _player(phase23_db, 103, "Patrick Mahomes", "QB", "KC")
    phase23_db.add_all(
        [
            DraftPick(league_id=l1.id, team_id=me1.id, overall=1, round=1, round_pick=1, espn_player_id=101),
            DraftPick(league_id=l1.id, team_id=me1.id, overall=20, round=2, round_pick=10, espn_player_id=102),
            DraftPick(league_id=l2.id, team_id=me2.id, overall=10, round=1, round_pick=10, espn_player_id=101),
            DraftPick(league_id=l1.id, team_id=opp.id, overall=5, round=1, round_pick=5, espn_player_id=103),
        ]
    )
    phase23_db.flush()

    out = build_exposure(phase23_db, scope="me", filters=PortfolioFilters(season=2026))
    bijan = next(row for row in out["players"] if row["espn_player_id"] == 101)
    assert bijan["exposure_pct"] == 100.0
    assert bijan["share"] == "2 / 2"
    assert bijan["avg_overall"] == 5.5
    assert bijan["avg_pick_value"] == pytest.approx(round((pick_value(1) + pick_value(10)) / 2, 2))
    amon = next(row for row in out["players"] if row["espn_player_id"] == 102)
    assert amon["exposure_pct"] == 50.0
    assert out["coverage"]["teams_in_scope"] == 2
    fingerprint = out["round_fingerprint"]
    assert {row["bucket"] for row in fingerprint} == {"1", "2"}
    assert all("%" not in row["bucket"] for row in fingerprint)
    round_one_rb = next(
        row for row in fingerprint if row["bucket"] == "1" and row["position"] == "RB"
    )
    assert round_one_rb["pick_count"] == 2
    assert round_one_rb["picks_per_team"] == 1.0
    assert round_one_rb["field_picks_per_team"] == 0.0

    opp_out = build_exposure(phase23_db, scope="opponents", filters=PortfolioFilters(season=2026))
    assert opp_out["teams_in_scope"] == 1
    assert opp_out["players"][0]["player_name"] == "Patrick Mahomes"


def test_field_exposure_counts_league_once_when_multiple_opponents_roster_player(phase23_db):
    l1 = _league(phase23_db, "field-1")
    l2 = _league(phase23_db, "field-2")
    me1 = _team(phase23_db, l1, 1, "Me 1", me=True)
    me2 = _team(phase23_db, l2, 1, "Me 2", me=True)
    opp1 = _team(phase23_db, l1, 2, "Opponent 1")
    opp2 = _team(phase23_db, l1, 3, "Opponent 2")
    opp3 = _team(phase23_db, l2, 2, "Opponent 3")
    _player(phase23_db, 101, "My Anchor", "RB", "ATL")
    _player(phase23_db, 201, "Field Stack", "WR", "DET")
    _player(phase23_db, 202, "Other Field", "QB", "KC")
    phase23_db.add_all(
        [
            DraftPick(league_id=l1.id, team_id=me1.id, overall=1, espn_player_id=101),
            DraftPick(league_id=l2.id, team_id=me2.id, overall=1, espn_player_id=101),
            DraftPick(league_id=l1.id, team_id=opp1.id, overall=2, espn_player_id=201),
            DraftPick(league_id=l1.id, team_id=opp2.id, overall=3, espn_player_id=201),
            DraftPick(league_id=l2.id, team_id=opp3.id, overall=4, espn_player_id=202),
        ]
    )
    phase23_db.flush()

    out = build_exposure(phase23_db, scope="me", filters=PortfolioFilters(season=2026))
    field_stack = next(row for row in out["players"] if row["espn_player_id"] == 201)
    assert field_stack["field_rostered_teams"] == 2
    assert field_stack["field_teams_in_scope"] == 3
    assert field_stack["field_slot_pct"] == 66.7
    assert field_stack["field_rostered_leagues"] == 1
    assert field_stack["field_leagues_in_scope"] == 2
    assert field_stack["field_exposure_pct"] == 50.0
    assert field_stack["field_share"] == "1 / 2"
    assert field_stack["leverage_pp"] == -50.0
    assert out["headlines"]["most_underowned"]["espn_player_id"] == 201
    assert all(row["my_exposure_pct"] > 0 for row in players_for_view(out["players"], "rostered"))
    assert players_for_view(out["players"], "field_owned")[0]["espn_player_id"] == 201
    _assert_percentages_capped(out)


def test_nfl_team_concentration_splits_penetration_from_players_per_team(phase23_db):
    league = _league(phase23_db, "concentration")
    me = _team(phase23_db, league, 1, "Me", me=True)
    _player(phase23_db, 101, "Falcons RB", "RB", "ATL")
    _player(phase23_db, 102, "Falcons WR", "WR", "ATL")
    phase23_db.add_all(
        [
            DraftPick(league_id=league.id, team_id=me.id, overall=1, espn_player_id=101),
            DraftPick(league_id=league.id, team_id=me.id, overall=11, espn_player_id=102),
        ]
    )
    phase23_db.flush()

    out = build_exposure(phase23_db, scope="me", filters=PortfolioFilters(season=2026))
    atl = out["nfl_team_concentration"][0]
    assert atl["nfl_team"] == "ATL"
    assert atl["teams_with_player"] == 1
    assert atl["player_team_instances"] == 2
    assert atl["penetration_pct"] == 100.0
    assert atl["exposure_pct"] == 100.0
    assert atl["players_per_team"] == 2.0
    assert atl["penetration_share"] == "1 / 1"
    assert atl["players_per_team_share"] == "2 / 1"
    _assert_percentages_capped(out)


def test_draft_adp_means_exclude_auction_and_keepers(phase23_db):
    l1 = _league(phase23_db, "1")
    auction = _league(phase23_db, "2", draft_type="AUCTION")
    me1 = _team(phase23_db, l1, 1, "Me 1", me=True)
    me2 = _team(phase23_db, auction, 1, "Auction Me", me=True)
    _player(phase23_db, 101, "Bijan Robinson", "RB", "ATL", ffc_adp=3.0)
    _player(phase23_db, 102, "Amon-Ra St. Brown", "WR", "DET", ffc_adp=15.0)
    _player(phase23_db, 103, "Patrick Mahomes", "QB", "KC", ffc_adp=25.0)
    phase23_db.add_all(
        [
            # ESPN mean = (4 + -2) / 2 = 1.0. FFC mean = ((3-1) + (15-20)) / 2 = -1.5.
            DraftPick(league_id=l1.id, team_id=me1.id, overall=1, round=1, espn_player_id=101, adp_at_draft=5.0, value_delta=4.0),
            DraftPick(league_id=l1.id, team_id=me1.id, overall=20, round=2, espn_player_id=102, adp_at_draft=18.0, value_delta=-2.0),
            # Keeper excluded from means.
            DraftPick(league_id=l1.id, team_id=me1.id, overall=30, round=3, espn_player_id=103, adp_at_draft=31.0, value_delta=1.0, keeper=True),
            # Auction excluded from pick-number ADP means.
            DraftPick(league_id=auction.id, team_id=me2.id, overall=1, round=1, espn_player_id=101, adp_at_draft=5.0, value_delta=4.0, bid_amount=50),
        ]
    )
    phase23_db.flush()
    metrics.recompute_league(phase23_db, l1)
    metrics.recompute_league(phase23_db, auction)
    phase23_db.commit()

    out = build_draft_adp(phase23_db, PortfolioFilters(season=2026))
    team = next(row for row in out["teams"] if row["team_id"] == me1.id)
    assert team["draft_value_capture_espn"] == 1.0
    assert team["draft_value_capture_ffc"] == -1.5
    assert team["draft_adp_source_disagreement"] == 2.5  # (|5-3| + |18-15|) / 2
    assert out["coverage"]["auction_teams"] == 1
    assert out["coverage"]["keeper_picks"] == 1
    assert out["coverage"]["eligible_picks"] == 2


def test_keeper_pick_excluded_from_adp_and_strategy_triggers(phase23_db):
    league = _league(phase23_db, "keepers")
    me = _team(phase23_db, league, 1, "Keeper Me", me=True)
    _player(phase23_db, 101, "Kept RB", "RB", "ATL", ffc_adp=90.0)
    _player(phase23_db, 102, "Early WR", "WR", "DET", ffc_adp=12.0)
    _player(phase23_db, 103, "Second WR", "WR", "DAL", ffc_adp=22.0)
    _player(phase23_db, 104, "Late RB", "RB", "BUF", ffc_adp=62.0)
    phase23_db.add_all(
        [
            # This artificial +89 keeper delta must affect neither mean nor RB timing.
            DraftPick(
                league_id=league.id,
                team_id=me.id,
                overall=1,
                round=1,
                espn_player_id=101,
                adp_at_draft=90.0,
                value_delta=89.0,
                keeper=True,
            ),
            DraftPick(
                league_id=league.id,
                team_id=me.id,
                overall=11,
                round=2,
                espn_player_id=102,
                adp_at_draft=12.0,
                value_delta=1.0,
            ),
            DraftPick(
                league_id=league.id,
                team_id=me.id,
                overall=21,
                round=3,
                espn_player_id=103,
                adp_at_draft=22.0,
                value_delta=1.0,
            ),
            DraftPick(
                league_id=league.id,
                team_id=me.id,
                overall=61,
                round=7,
                espn_player_id=104,
                adp_at_draft=62.0,
                value_delta=1.0,
            ),
        ]
    )
    phase23_db.flush()
    metrics.recompute_league(phase23_db, league)
    phase23_db.commit()

    adp = build_draft_adp(phase23_db, PortfolioFilters(season=2026))
    team = adp["teams"][0]
    assert team["draft_value_capture_espn"] == 1.0
    assert team["draft_value_capture_ffc"] == 1.0
    assert adp["coverage"]["keeper_picks"] == 1
    assert adp["coverage"]["eligible_picks"] == 3

    strategies = build_strategies(phase23_db, PortfolioFilters(season=2026))
    strategy = strategies["teams"][0]
    assert strategy["primary_label"] == "Zero RB"
    assert 1 not in {
        pick["overall"]
        for picks in strategy["triggering_picks"].values()
        for pick in picks
    }
    assert strategies["coverage"]["keeper_picks"] == 1


def _picks(shape: list[tuple[int, str]]) -> list[StrategyPick]:
    return [StrategyPick(overall=overall, position=position) for overall, position in shape]


def test_strategy_rules_and_league_size_normalization():
    zero = classify_draft_strategy(_picks([(1, "WR"), (10, "WR"), (20, "TE"), (30, "QB"), (51, "RB")]), league_size=10)
    assert zero.primary.label == "Zero RB"
    hero = classify_draft_strategy(_picks([(2, "RB"), (11, "WR"), (20, "WR"), (81, "RB")]), league_size=10)
    assert hero.primary.label == "Hero RB"
    robust = classify_draft_strategy(_picks([(3, "RB"), (14, "RB"), (25, "WR"), (46, "RB")]), league_size=10)
    assert robust.primary.label == "Robust RB"
    elite = classify_draft_strategy(_picks([(1, "WR"), (20, "TE"), (90, "QB")]), league_size=10)
    assert elite.secondary.label == "Elite TE"
    late_qb = classify_draft_strategy(_picks([(1, "RB"), (11, "WR"), (82, "QB")]), league_size=10)
    assert late_qb.secondary.label == "Late-Round QB"
    anchor = classify_draft_strategy(_picks([(1, "WR"), (11, "WR"), (21, "WR"), (41, "RB")]), league_size=10)
    assert anchor.secondary.label == "Anchor WR"

    # Same shape: first two picks are RBs through round-equivalent 2.0 in 8-team and 12-team leagues.
    eight = classify_draft_strategy(_picks([(4, "RB"), (16, "RB"), (40, "WR")]), league_size=8)
    twelve = classify_draft_strategy(_picks([(6, "RB"), (24, "RB"), (60, "WR")]), league_size=12)
    assert eight.primary.label == twelve.primary.label == "Robust RB"


def test_strategy_second_rb_between_rounds_five_and_seven_is_hero():
    gap = classify_draft_strategy(_picks([(3, "RB"), (11, "WR"), (21, "WR"), (60, "RB")]), league_size=10)
    assert gap.primary.label == "Hero RB"


def test_strategy_shuffle_stable_and_autodraft_precedence():
    picks = _picks([(1, "WR"), (11, "WR"), (21, "WR"), (90, "QB")])
    a = classify_draft_strategy(picks, league_size=10)
    b = classify_draft_strategy(list(reversed(picks)), league_size=10)
    assert (a.primary.label, a.secondary.label, a.primary.trigger_overalls) == (
        b.primary.label,
        b.secondary.label,
        b.primary.trigger_overalls,
    )
    auto = classify_draft_strategy(picks, league_size=10, team_autodrafted=True)
    assert auto.primary.label == "Autodraft/Absent" and auto.secondary is None


def test_ffc_ingestion_dedupes_requests_and_surfaces_unmatched(phase23_db):
    l1 = _league(phase23_db, "1")
    l2 = _league(phase23_db, "2")
    _player(phase23_db, 101, "Bijan Robinson Jr.", "RB", "ATL")
    _player(phase23_db, 102, "Amon-Ra St. Brown", "WR", "DET")
    _player(phase23_db, -16007, "Broncos D/ST", "D/ST", "7")
    _player(phase23_db, 999, "Unmatched Player", "RB", "DAL")
    payload = load_fixture("ffc_adp_ppr_10_2026.json")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self):
            self.calls = []

        def get(self, url, params):
            self.calls.append((url, params))
            return FakeResponse()

    fake = FakeClient()
    report = refresh_ffc_adp(phase23_db, [l1, l2], force=True, client=fake)
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == {"teams": 10, "year": 2026}
    assert report.matched_players == 3
    assert len(report.unmatched_players) == 1
    bijan = phase23_db.get(Player, 101)
    assert bijan.ffc_id == 9001 and bijan.ffc_adp == 2.2
    dst = phase23_db.get(Player, -16007)
    assert dst.ffc_id == 9004 and dst.ffc_adp == 145.0


def test_ffc_apply_snapshot_name_resolution_cases(phase23_db):
    _player(phase23_db, 101, "Bijan Robinson Jr.", "RB", "ATL")
    _player(phase23_db, 102, "Amon-Ra St. Brown", "WR", "DET")
    _player(phase23_db, -16007, "Broncos D/ST", "D/ST", "7")
    _player(phase23_db, 103, "Eddy Pineiro", "K", "25")
    _player(phase23_db, 999, "Deliberate Miss", "WR", "DAL")
    snap = AdpSnapshot(
        source="ffc",
        format="ppr",
        teams=10,
        payload_json=load_fixture("ffc_adp_ppr_10_2026.json"),
    )
    phase23_db.add(snap)
    phase23_db.flush()
    report = apply_ffc_snapshots_to_players(phase23_db, [snap])
    assert report.matched_players == 4
    assert {row["espn_player_id"] for row in report.unmatched_players} == {999}
    assert phase23_db.get(Player, 103).ffc_id == 9005


def test_strategy_recompute_idempotent_and_clears_stale_label(phase23_db):
    league = _league(phase23_db, "1")
    me = _team(phase23_db, league, 1, "Me", me=True)
    _player(phase23_db, 101, "RB1", "RB", "ATL")
    _player(phase23_db, 102, "RB2", "RB", "BUF")
    _player(phase23_db, 103, "RB3", "RB", "CHI")
    _player(phase23_db, 104, "WR1", "WR", "DAL")
    for overall, player_id in [(1, 101), (12, 102), (23, 103), (40, 104)]:
        phase23_db.add(DraftPick(league_id=league.id, team_id=me.id, overall=overall, espn_player_id=player_id))
    phase23_db.flush()
    metrics.recompute_league(phase23_db, league)
    phase23_db.commit()
    assert phase23_db.scalar(
        select(Metric.value_float).where(Metric.key == "draft_strategy_robust_rb")
    ) is not None

    # Reclassify as Zero RB by moving the RBs after the strict end-of-round-5 threshold.
    for pick in phase23_db.scalars(select(DraftPick).where(DraftPick.league_id == league.id)):
        if pick.espn_player_id in {101, 102, 103}:
            pick.overall = {101: 61, 102: 72, 103: 83}[pick.espn_player_id]
        else:
            pick.overall = 1
    phase23_db.flush()
    metrics.recompute_league(phase23_db, league)
    metrics.recompute_league(phase23_db, league)
    phase23_db.commit()
    assert phase23_db.scalar(
        select(func.count()).select_from(Metric).where(Metric.key == "draft_strategy_robust_rb")
    ) == 0
    assert phase23_db.scalar(
        select(Metric.value_float).where(Metric.key == "draft_strategy_zero_rb")
    ) is not None


def test_phase23_portfolio_endpoints(phase23_db):
    league = _league(phase23_db, "1")
    me = _team(phase23_db, league, 1, "Me", me=True)
    _player(phase23_db, 101, "Bijan Robinson", "RB", "ATL", ffc_adp=3.0)
    _player(phase23_db, 102, "Amon-Ra St. Brown", "WR", "DET", ffc_adp=12.0)
    phase23_db.add_all(
        [
            DraftPick(league_id=league.id, team_id=me.id, overall=1, round=1, espn_player_id=101, adp_at_draft=5.0, value_delta=4.0),
            DraftPick(league_id=league.id, team_id=me.id, overall=11, round=2, espn_player_id=102, adp_at_draft=10.0, value_delta=-1.0),
        ]
    )
    phase23_db.flush()
    metrics.recompute_league(phase23_db, league)
    phase23_db.commit()

    exposure = client.get("/api/portfolio/exposure")
    assert exposure.status_code == 200
    _assert_percentages_capped(exposure.json())
    draft_adp = client.get("/api/portfolio/draft-adp")
    assert draft_adp.status_code == 200
    _assert_percentages_capped(draft_adp.json())
    strategies = client.get("/api/portfolio/strategies")
    assert strategies.status_code == 200
    _assert_percentages_capped(strategies.json())
    assert strategies.json()["coverage"]["teams_in_scope"] == 1
    primary = {row["label"]: row["count"] for row in strategies.json()["primary_distribution"]}
    assert primary["Zero RB"] == 0
    assert set(primary) == {
        "Zero RB",
        "Hero RB",
        "Robust RB",
        "Balanced/BPA",
        "Autodraft/Absent",
    }
