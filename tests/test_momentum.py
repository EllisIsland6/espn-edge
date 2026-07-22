"""Offline tests for score snapshots, momentum, and achievement presentation facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.models import League, Metric, MetricSnapshot, Team
from api.services import momentum


def _league_team(session, league_ref: str = "momentum-1") -> tuple[League, Team]:
    league = League(
        espn_league_id=league_ref,
        season=2026,
        lifecycle="in_season",
        is_public=True,
    )
    session.add(league)
    session.flush()
    team = Team(league_id=league.id, espn_team_id=1, name="Mine", is_me=True)
    session.add(team)
    session.flush()
    league.my_team_id = team.id
    return league, team


def _metric(session, league: League, team: Team, key: str, value: float) -> Metric:
    row = Metric(
        league_id=league.id,
        team_id=team.id,
        key=key,
        value_float=value,
    )
    session.add(row)
    session.flush()
    return row


def test_same_period_syncs_append_but_momentum_uses_latest_event(db_session):
    league, team = _league_team(db_session)
    edge = _metric(db_session, league, team, momentum.EDGE_SCORE, 50.0)
    index = _metric(db_session, league, team, momentum.EDGE_INDEX_SCORE, 60.0)
    t0 = datetime(2026, 9, 8, tzinfo=UTC)

    assert momentum.record_metric_snapshots(
        db_session, league.id, 1, recorded_at=t0, batch_id="sync-a"
    ) == 2
    edge.value_float = 55.0
    index.value_float = 65.0
    assert momentum.record_metric_snapshots(
        db_session,
        league.id,
        1,
        recorded_at=t0 + timedelta(hours=1),
        batch_id="sync-b",
    ) == 2
    same_week = list(db_session.scalars(select(MetricSnapshot)))
    assert len(same_week) == 4
    assert {(row.batch_id, row.value_float) for row in same_week} == {
        ("sync-a", 50.0),
        ("sync-a", 60.0),
        ("sync-b", 55.0),
        ("sync-b", 65.0),
    }
    assert {row.recorded_at for row in same_week} == {
        t0.replace(tzinfo=None),
        (t0 + timedelta(hours=1)).replace(tzinfo=None),
    }
    same_period = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_INDEX_SCORE, index.value_float
    )
    assert same_period.status == "first_sync"
    assert same_period.history_count == 1

    # A later fantasy period with the same closing score is a genuine flat week.
    momentum.record_metric_snapshots(
        db_session,
        league.id,
        2,
        recorded_at=t0 + timedelta(days=7),
        batch_id="sync-c",
    )
    flat = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_INDEX_SCORE, index.value_float
    )
    assert flat.status == "flat"
    assert flat.delta == 0.0
    assert flat.streak_direction is None and flat.streak_count == 0
    assert flat.history_count == 2


def test_first_sync_pending_and_streak_rules(db_session):
    league, team = _league_team(db_session)
    score = _metric(db_session, league, team, momentum.EDGE_SCORE, 50.0)

    pending = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_SCORE, None
    )
    assert pending.status == "pending" and pending.delta is None

    momentum.record_metric_snapshots(db_session, league.id, 1)
    first = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_SCORE, score.value_float
    )
    assert first.status == "first_sync" and first.delta is None and first.streak_count == 0

    for period, value in ((2, 55.0), (3, 60.0), (4, 65.0)):
        score.value_float = value
        momentum.record_metric_snapshots(db_session, league.id, period)
    rising = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_SCORE, score.value_float
    )
    assert rising.status == "up" and rising.delta == 5.0
    assert rising.streak_direction == "up" and rising.streak_count == 3

    score.value_float = 62.0
    momentum.record_metric_snapshots(db_session, league.id, 5)
    reversal = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_SCORE, score.value_float
    )
    assert reversal.status == "down" and reversal.streak_count == 1

    score.value_float = 62.0
    momentum.record_metric_snapshots(db_session, league.id, 6)
    tied = momentum.metric_momentum(
        db_session, league.id, team.id, momentum.EDGE_SCORE, score.value_float
    )
    assert tied.status == "flat" and tied.streak_count == 0


def test_snapshot_batch_is_atomic_and_next_write_recovers(db_session, monkeypatch):
    league, team = _league_team(db_session)
    _metric(db_session, league, team, momentum.EDGE_SCORE, 70.0)
    _metric(db_session, league, team, momentum.EDGE_INDEX_SCORE, 72.0)
    original = momentum._write_snapshot
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        original(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated interruption")

    monkeypatch.setattr(momentum, "_write_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        momentum.record_metric_snapshots(db_session, league.id, 1)
    assert db_session.scalar(select(func.count()).select_from(MetricSnapshot)) == 0

    monkeypatch.setattr(momentum, "_write_snapshot", original)
    assert momentum.record_metric_snapshots(db_session, league.id, 1) == 2
    assert db_session.scalar(select(func.count()).select_from(MetricSnapshot)) == 2


def test_league_delete_cascades_history_before_id_reuse(db_session):
    league, team = _league_team(db_session)
    score = _metric(db_session, league, team, momentum.EDGE_SCORE, 64.0)
    momentum.record_metric_snapshots(db_session, league.id, 1)
    old_league_id = league.id

    db_session.delete(league)
    db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(MetricSnapshot)) == 0

    replacement = League(
        id=old_league_id,
        espn_league_id="momentum-readded",
        season=2026,
        lifecycle="in_season",
        is_public=True,
    )
    db_session.add(replacement)
    db_session.flush()
    replacement_team = Team(
        league_id=replacement.id,
        espn_team_id=1,
        name="New team",
        is_me=True,
    )
    db_session.add(replacement_team)
    db_session.flush()
    fresh = momentum.metric_momentum(
        db_session,
        replacement.id,
        replacement_team.id,
        momentum.EDGE_SCORE,
        score.value_float,
    )
    assert replacement.id == old_league_id
    assert fresh.status == "first_sync" and fresh.history_count == 0


def test_achievement_thresholds_are_exact_and_inclusive(db_session):
    league, team = _league_team(db_session)
    league.last_sync_ok = True
    lineup = _metric(db_session, league, team, momentum.LINEUP_EFFICIENCY, 0.90)
    draft = _metric(db_session, league, team, momentum.DRAFT_SURPLUS, 0.0)
    all_play = _metric(db_session, league, team, momentum.ALL_PLAY_WIN_PCT, 0.60)

    assert [a.key for a in momentum.team_achievements(db_session, league, team.id)] == [
        "sync_healthy",
        "lineup_elite",
        "all_play_edge",
    ]

    lineup.value_float = 0.8999
    draft.value_float = 0.001
    all_play.value_float = 0.5999
    assert [a.key for a in momentum.team_achievements(db_session, league, team.id)] == [
        "sync_healthy",
        "draft_value",
    ]


def test_snapshot_schema_cannot_store_account_credentials(db_session):
    columns = set(MetricSnapshot.__table__.columns.keys())
    assert columns == {
        "id",
        "league_id",
        "team_id",
        "batch_id",
        "key",
        "period",
        "value_float",
        "recorded_at",
    }
    assert not {"swid", "espn_s2", "cookie", "payload_json"} & columns
