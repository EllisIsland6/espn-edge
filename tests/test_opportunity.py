"""Phase 27 Opportunity Analytics: offline import, scoring, and roster truth tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.main import app
from api.models import (
    CurrentRosterEntry,
    CurrentRosterSnapshot,
    League,
    NflversePlayerMap,
    OpportunityImport,
    OpportunityWeek,
    Player,
    Team,
)
from api.services import opportunity as opportunity_service
from api.services.opportunity import (
    OpportunityBusyError,
    OpportunityError,
    build_opportunity_charts,
    build_portfolio_opportunity,
    compute_opportunity_scores,
    opportunity_doctor,
    prepare_opportunity_import,
    refresh_opportunity,
)
from api.services.portfolio_filters import PortfolioFilters
from api.tenancy import current_tenant_id


def _stat(
    player_id: str,
    *,
    week: int = 1,
    position: str = "RB",
    carries: float = 4,
    targets: float = 2,
    carry_team: str = "ATL",
    target_share: float = 0.2,
    air_share: float = 0.1,
    ppr: float = 10,
    receiving_tds: float = 0,
    passing_yards: float = 0,
) -> dict:
    return {
        "season": 2026,
        "season_type": "REG",
        "week": week,
        "game_id": f"2026_{week:02d}_ATL_CAR",
        "player_id": player_id,
        "position": position,
        "team": carry_team,
        "opponent_team": "CAR",
        "carries": carries,
        "targets": targets,
        "receptions": targets,
        "rushing_yards": carries * 4,
        "receiving_yards": targets * 8,
        "receiving_air_yards": targets * 10,
        "receiving_tds": receiving_tds,
        "passing_yards": passing_yards,
        "target_share": target_share,
        "air_yards_share": air_share,
        "wopr": target_share + air_share,
        "rushing_epa": 0.5,
        "receiving_epa": 0.4,
        "fantasy_points_ppr": ppr,
    }


@dataclass
class FakeNflverse:
    players: list[dict]
    stats: list[dict]
    failure: Exception | None = None
    stats_failure: Exception | None = None
    package_version: str = "test-fixture"
    retries: int = 0
    calls: int = 0

    def load_players(self) -> list[dict]:
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.players

    def load_player_stats(self, _season: int) -> list[dict]:
        self.calls += 1
        if self.stats_failure:
            raise self.stats_failure
        if self.failure:
            raise self.failure
        return self.stats

    def close(self) -> None:
        return None


def _add_players(session, count: int = 1) -> None:
    session.add_all(
        Player(
            espn_player_id=1000 + index,
            name=f"Runner {index}",
            position="RB",
            nfl_team="ATL",
        )
        for index in range(count)
    )
    session.flush()


def _registry(count: int = 1) -> list[dict]:
    return [{"espn_id": 1000 + index, "gsis_id": f"00-00000{index:02d}"} for index in range(count)]


def test_prepare_uses_team_carries_before_position_filter_and_deduplicates(db_session):
    _add_players(db_session)
    rb = _stat("00-0000000", carries=4)
    qb = _stat("00-QB", position="QB", carries=6, targets=0, passing_yards=250)
    prepared = prepare_opportunity_import(db_session, 2026, _registry(), [rb, dict(rb), qb])

    assert len(prepared.weeks) == 1
    assert prepared.weeks[0]["carry_share"] == pytest.approx(0.4)
    assert prepared.weeks[0]["team_passing_yards"] == pytest.approx(250)
    assert prepared.matched_players == 1
    assert prepared.unmatched_players == 0
    assert prepared.schema_fingerprint


def test_prepare_rejects_schema_drift_conflicts_and_ambiguous_ids(db_session):
    _add_players(db_session, 2)
    row = _stat("00-0000000")
    conflicting = dict(row, targets=99)
    with pytest.raises(OpportunityError, match="conflicting") as conflict:
        prepare_opportunity_import(db_session, 2026, _registry(2), [row, conflicting])
    assert conflict.value.code == "OPP-DATA-CONFLICT"

    missing = dict(row)
    missing.pop("target_share")
    with pytest.raises(OpportunityError) as schema:
        prepare_opportunity_import(db_session, 2026, _registry(2), [missing])
    assert schema.value.code == "OPP-SOURCE-FORMAT"
    assert "target_share" in schema.value.details["missing_columns"]

    invalid = dict(row, target_share=float("nan"))
    with pytest.raises(OpportunityError) as bad_value:
        prepare_opportunity_import(db_session, 2026, _registry(2), [invalid])
    assert bad_value.value.code == "OPP-SOURCE-FORMAT"
    assert bad_value.value.details["invalid_values"][0]["column"] == "target_share"

    duplicate_id_registry = [
        {"espn_id": 1000, "gsis_id": "00-SHARED"},
        {"espn_id": 1001, "gsis_id": "00-SHARED"},
    ]
    prepared = prepare_opportunity_import(db_session, 2026, duplicate_id_registry, [row])
    assert prepared.matched_players == 0
    assert prepared.unmatched_players == 2
    assert {item["status"] for item in prepared.maps} == {"ambiguous"}


def test_refresh_is_idempotent_correctable_and_preserves_last_good(db_session):
    _add_players(db_session)
    db_session.commit()
    first = FakeNflverse(_registry(), [_stat("00-0000000", carries=4)])
    result = refresh_opportunity(db_session, 2026, force=True, client=first)
    db_session.commit()
    assert result["state"] == "ready"
    assert result["stored_rows"] == 1

    skipped_source = FakeNflverse([], [])
    skipped = refresh_opportunity(db_session, 2026, client=skipped_source)
    db_session.commit()
    assert skipped["state"] == "skipped"
    assert skipped_source.calls == 0

    corrected = FakeNflverse(_registry(), [_stat("00-0000000", carries=8)])
    refresh_opportunity(db_session, 2026, force=True, client=corrected)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(OpportunityWeek)) == 1
    assert db_session.scalar(select(OpportunityWeek.carries)) == 8

    failure = FakeNflverse(
        [],
        [],
        failure=OpportunityError(
            "OPP-SOURCE-UNAVAILABLE",
            "NFL opportunity data is temporarily unavailable; showing the last good import.",
        ),
    )
    failed = refresh_opportunity(db_session, 2026, force=True, client=failure)
    db_session.commit()
    assert failed["state"] == "failed"
    assert failed["last_good_at"] is not None
    assert db_session.scalar(select(func.count()).select_from(OpportunityWeek)) == 1
    assert db_session.scalar(select(OpportunityWeek.carries)) == 8


def test_not_published_is_expected_before_the_season(db_session):
    _add_players(db_session)
    source = FakeNflverse(
        _registry(),
        [],
        stats_failure=OpportunityError(
            "OPP-NOT-PUBLISHED",
            "Opportunity data begins after regular-season games.",
            details={"status_code": 404},
        ),
    )
    result = refresh_opportunity(db_session, 2026, force=True, client=source)
    assert result["state"] == "empty"
    assert result["error_code"] == "OPP-NOT-PUBLISHED"
    assert result["stored_rows"] == 0
    assert result["matched_players"] == 1
    assert db_session.get(NflversePlayerMap, 1000).status == "matched"

    db_session.add(
        League(tenant_id=current_tenant_id(db_session), 
            espn_league_id="in-season-empty",
            season=2026,
            lifecycle="in_season",
        )
    )
    db_session.flush()
    failed = refresh_opportunity(
        db_session,
        2026,
        force=True,
        client=FakeNflverse(_registry(), []),
    )
    assert failed["state"] == "failed"
    assert failed["error_code"] == "OPP-SOURCE-UNAVAILABLE"
    assert db_session.get(NflversePlayerMap, 1000).status == "matched"


def test_scores_are_position_relative_with_gap_sample_and_trend_rules():
    rows = []
    for player_index in range(10):
        for week in range(1, 5):
            share = 0.04 + player_index * 0.025
            if player_index == 9:
                share = 0.10 if week <= 2 else 0.40
            rows.append(
                {
                    "gsis_id": f"p{player_index}",
                    "position": "RB",
                    "week": week,
                    "game_id": f"g{week}",
                    "team": "ATL",
                    "carry_share": share,
                    "target_share": share,
                    "air_yards_share": 0.0,
                    "wopr": share,
                    "rushing_epa": 0.0,
                    "receiving_epa": 0.0,
                    "fantasy_points_ppr": 20 - player_index,
                }
            )
    rows.append(
        {
            "gsis_id": "thin-te",
            "position": "TE",
            "week": 1,
            "game_id": "g1",
            "team": "KC",
            "carry_share": 0.0,
            "target_share": 0.5,
            "air_yards_share": 0.5,
            "wopr": 1.0,
            "rushing_epa": 0.0,
            "receiving_epa": 0.0,
            "fantasy_points_ppr": 20.0,
        }
    )

    scored = compute_opportunity_scores(rows)
    rising = next(row for row in scored if row["gsis_id"] == "p9")
    assert rising["opportunity_score"] == pytest.approx(95.0)
    assert rising["production_percentile"] == pytest.approx(5.0)
    assert rising["opportunity_gap"] == pytest.approx(90.0)
    assert rising["signal"] == "opportunity_ahead"
    assert rising["trend"] == "rising"
    thin = next(row for row in scored if row["gsis_id"] == "thin-te")
    assert thin["sample_games"] == 1
    assert thin["opportunity_score"] is None
    assert thin["signal"] == "pending"


def test_wr_evidence_uses_per_game_means_and_weighted_adot():
    rows = [
        {
            "gsis_id": "wr",
            "position": "WR",
            "week": 1,
            "game_id": "g1",
            "team": "ATL",
            "targets": 10.0,
            "receptions": 6.0,
            "receiving_yards": 80.0,
            "receiving_air_yards": 120.0,
            "receiving_tds": 1.0,
            "team_passing_yards": 260.0,
            "carry_share": 0.0,
            "target_share": 0.25,
            "air_yards_share": 0.35,
            "wopr": 0.6,
            "rushing_epa": 0.0,
            "receiving_epa": 1.0,
            "fantasy_points_ppr": 20.0,
        },
        {
            "gsis_id": "wr",
            "position": "WR",
            "week": 2,
            "game_id": "g2",
            "team": "ATL",
            "targets": 2.0,
            "receptions": 1.0,
            "receiving_yards": 20.0,
            "receiving_air_yards": 12.0,
            "receiving_tds": 0.0,
            "team_passing_yards": 180.0,
            "carry_share": 0.0,
            "target_share": 0.10,
            "air_yards_share": 0.10,
            "wopr": 0.2,
            "rushing_epa": 0.0,
            "receiving_epa": 0.1,
            "fantasy_points_ppr": 3.0,
        },
    ]

    result = compute_opportunity_scores(rows)[0]
    assert result["targets_per_game"] == pytest.approx(6.0)
    assert result["receptions_per_game"] == pytest.approx(3.5)
    assert result["receiving_yards_per_game"] == pytest.approx(50.0)
    assert result["receiving_tds_per_game"] == pytest.approx(0.5)
    assert result["average_depth_of_target"] == pytest.approx(11.0)
    assert result["team_passing_yards_per_game"] == pytest.approx(220.0)


def test_phase29_charts_use_exact_shared_values_stable_population_and_diagnostics(db_session):
    db_session.add_all(
        Player(
            espn_player_id=1000 + index,
            name=f"Receiver {index:02d}",
            position="WR",
            nfl_team="ATL",
            espn_rank_ppr=251 if index == 10 else index + 1,
        )
        for index in range(12)
    )
    db_session.flush()
    stats = []
    for index in range(12):
        for week in range(1, 4):
            target_share = 0.10123 + index * 0.017
            air_share = -0.10 if index == 0 else 1.20 if index == 11 else 0.08 + index * 0.05
            row = _stat(
                f"00-00000{index:02d}",
                week=week,
                position="WR",
                carries=0,
                targets=3 + index,
                target_share=target_share,
                air_share=air_share,
                ppr=6 + index,
                receiving_tds=0,
            )
            row["receiving_air_yards"] = (3 + index) * (5 + index)
            stats.append(row)
    stats.extend(
        _stat(
            f"qb-{week}",
            week=week,
            position="QB",
            carries=2,
            targets=0,
            target_share=0,
            air_share=0,
            passing_yards=225 + week * 10,
        )
        for week in range(1, 4)
    )
    refresh_opportunity(
        db_session,
        2026,
        force=True,
        client=FakeNflverse(_registry(12), stats),
    )
    league = League(tenant_id=current_tenant_id(db_session), 
        espn_league_id="phase29",
        season=2026,
        name="Chart League",
        lifecycle="in_season",
        current_scoring_period=3,
        last_sync_ok=True,
    )
    db_session.add(league)
    db_session.flush()
    mine = Team(league_id=league.id, espn_team_id=1, name="Mine", is_me=True)
    db_session.add(mine)
    db_session.flush()
    league.my_team_id = mine.id
    snapshot = CurrentRosterSnapshot(league_id=league.id, scoring_period=3)
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        CurrentRosterEntry(
            snapshot_id=snapshot.id,
            team_id=mine.id,
            lineup_slot_id=0,
            slot_index=0,
            espn_player_id=1000,
        )
    )
    db_session.commit()

    all_charts = build_opportunity_charts(
        db_session,
        PortfolioFilters(season=2026),
        view="all",
        position="WR",
    )
    rostered = build_opportunity_charts(
        db_session,
        PortfolioFilters(season=2026),
        view="rostered",
        position="WR",
    )
    all_target_air = next(chart for chart in all_charts["charts"] if chart["id"] == "target_air")
    rostered_target_air = next(
        chart for chart in rostered["charts"] if chart["id"] == "target_air"
    )
    assert all_charts["coverage"]["rank_limit"] == 250
    assert all_charts["coverage"]["population_players"] == 11
    assert rostered["coverage"]["returned_players"] == 1
    assert all(point["espn_rank_ppr"] <= 250 for point in all_charts["points"])
    assert not any(point["espn_player_id"] == 1010 for point in all_charts["points"])
    assert rostered_target_air["domain"] == all_target_air["domain"]
    assert rostered_target_air["references"] == all_target_air["references"]
    assert all_target_air["domain"]["y_min"] < -10
    assert all_target_air["domain"]["y_max"] > 120

    table = build_portfolio_opportunity(
        db_session, PortfolioFilters(season=2026), view="all"
    )
    chart_point = next(point for point in all_charts["points"] if point["espn_player_id"] == 1000)
    table_point = next(player for player in table["players"] if player["espn_player_id"] == 1000)
    assert chart_point["target_share_pct"] == pytest.approx(10.123)
    assert chart_point["espn_rank_ppr"] == 1
    assert table_point["avg_target_share"] == round(chart_point["target_share_pct"], 1)
    assert chart_point["average_depth_of_target"] == pytest.approx(5.0)

    yards_tds = next(chart for chart in all_charts["charts"] if chart["id"] == "yards_tds")
    assert yards_tds["domain"]["y_min"] < 0 < yards_tds["domain"]["y_max"]
    assert any(ref["kind"] == "y" and ref["value"] == 0 for ref in yards_tds["references"])
    opportunity_production = next(
        chart for chart in all_charts["charts"] if chart["id"] == "opportunity_production"
    )
    assert opportunity_production["domain"] == {
        "x_min": 0.0,
        "x_max": 100.0,
        "y_min": 0.0,
        "y_max": 100.0,
    }
    assert len(opportunity_production["references"]) == 3

    doctor = opportunity_doctor(db_session, 2026)
    assert doctor["charts"]["source_run_id"]
    assert doctor["charts"]["positions"]["WR"]["target_air"]["eligible_players"] == 11
    assert doctor["charts"]["positions"]["WR"]["ranking_coverage"] == {
        "rank_limit": 250,
        "ranked_players": 11,
        "excluded_players": 1,
    }
    assert "average_depth_of_target" in doctor["charts"]["null_or_non_finite_by_field"]

    client = TestClient(app)
    response = client.get(
        "/api/portfolio/opportunity/charts?season=2026&view=rostered&position=WR"
    )
    assert response.status_code == 200
    assert response.json()["coverage"]["returned_players"] == 1
    unsupported = client.get(
        "/api/portfolio/opportunity/charts?season=2026&position=RB&chart_id=target_air"
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "OPP-CHART-UNSUPPORTED"


def test_roster_availability_requires_a_current_successful_snapshot(db_session):
    _add_players(db_session, 10)
    stats = [
        _stat(
            f"00-00000{index:02d}",
            week=week,
            carries=index + 1,
            target_share=0.05 + index * 0.02,
            air_share=0.02,
            ppr=5 + index,
        )
        for index in range(10)
        for week in range(1, 4)
    ]
    refresh_opportunity(
        db_session,
        2026,
        force=True,
        client=FakeNflverse(_registry(10), stats),
    )
    league = League(tenant_id=current_tenant_id(db_session), 
        espn_league_id="27",
        season=2026,
        name="Opportunity League",
        lifecycle="in_season",
        current_scoring_period=3,
        last_sync_ok=True,
    )
    db_session.add(league)
    db_session.flush()
    mine = Team(league_id=league.id, espn_team_id=1, name="Mine", is_me=True)
    field = Team(league_id=league.id, espn_team_id=2, name="Field", is_me=False)
    db_session.add_all([mine, field])
    db_session.flush()
    league.my_team_id = mine.id
    snapshot = CurrentRosterSnapshot(league_id=league.id, scoring_period=3)
    db_session.add(snapshot)
    db_session.flush()
    db_session.add_all(
        [
            CurrentRosterEntry(
                snapshot_id=snapshot.id,
                team_id=mine.id,
                lineup_slot_id=0,
                slot_index=0,
                espn_player_id=1000,
            ),
            CurrentRosterEntry(
                snapshot_id=snapshot.id,
                team_id=field.id,
                lineup_slot_id=0,
                slot_index=0,
                espn_player_id=1001,
            ),
        ]
    )
    db_session.commit()

    current = build_portfolio_opportunity(db_session, PortfolioFilters(season=2026), view="all")
    by_id = {row["espn_player_id"]: row for row in current["players"]}
    assert by_id[1000]["mine_leagues"] == 1
    assert by_id[1001]["field_leagues"] == 1
    assert by_id[1002]["available_leagues"] == 1
    assert current["coverage"]["current_roster_leagues"] == 1

    league.last_sync_ok = False
    db_session.commit()
    stale = build_portfolio_opportunity(db_session, PortfolioFilters(season=2026), view="all")
    assert all(row["unknown_leagues"] == 1 for row in stale["players"])
    assert all(row["available_leagues"] == 0 for row in stale["players"])
    assert stale["coverage"]["unknown_roster_leagues"] == 1
    available = build_portfolio_opportunity(
        db_session, PortfolioFilters(season=2026), view="available"
    )
    assert available["players"] == []

    api_client = TestClient(app)
    assert api_client.get("/api/opportunity/status?season=2026").status_code == 200
    response = api_client.get("/api/portfolio/opportunity?season=2026&view=all")
    assert response.status_code == 200
    assert response.json()["coverage"]["unknown_roster_leagues"] == 1


def test_refresh_failure_diagnostics_are_bounded_and_secret_free(db_session):
    error = OpportunityError(
        "OPP-SOURCE-FORMAT",
        "NFL opportunity data changed format; previous data was preserved.",
        details={"missing_columns": [f"column-{index}" for index in range(1000)]},
    )
    result = refresh_opportunity(
        db_session, 2026, force=True, client=FakeNflverse([], [], failure=error)
    )
    db_session.flush()
    stored = db_session.get(OpportunityImport, result["run_id"])
    assert stored is not None
    assert len(str(stored.details_json).encode()) < 2048
    assert "cookie" not in str(stored.details_json).lower()
    assert db_session.scalar(select(func.count()).select_from(NflversePlayerMap)) == 0

    unexpected = refresh_opportunity(
        db_session,
        2026,
        force=True,
        client=FakeNflverse([], [], failure=RuntimeError("raw secret response")),
    )
    assert unexpected["error_code"] == "OPP-UNEXPECTED"
    assert "raw secret response" not in str(unexpected)


def test_concurrent_refresh_fails_fast_with_stable_code(db_session):
    assert opportunity_service._refresh_lock.acquire(blocking=False)
    try:
        with pytest.raises(OpportunityBusyError) as busy:
            refresh_opportunity(db_session, 2026, client=FakeNflverse([], []))
        assert busy.value.code == "OPP-REFRESH-BUSY"
    finally:
        opportunity_service._refresh_lock.release()
