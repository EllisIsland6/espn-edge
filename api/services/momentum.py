"""Persisted score momentum and presentation-only achievement rules.

The analytics engine remains the sole source of metric values. This module
records those values by completed fantasy period and derives display metadata
without changing any score formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import League, Metric, MetricSnapshot

EDGE_SCORE = "edge_score"
EDGE_INDEX_SCORE = "edge_index_score"
TRACKED_SCORE_KEYS = (EDGE_SCORE, EDGE_INDEX_SCORE)

LINEUP_EFFICIENCY = "lineup_efficiency"
DRAFT_SURPLUS = "draft_surplus"
ALL_PLAY_WIN_PCT = "all_play_win_pct"

LINEUP_ELITE_MIN = 0.90
ALL_PLAY_EDGE_MIN = 0.60


@dataclass(frozen=True)
class Momentum:
    status: str  # pending | first_sync | up | down | flat
    delta: float | None
    streak_direction: str | None
    streak_count: int
    history_count: int


@dataclass(frozen=True)
class Achievement:
    key: str
    label: str
    detail: str


def _write_snapshot(
    session: Session,
    *,
    league_id: int,
    team_id: int,
    batch_id: str,
    key: str,
    period: int,
    value: float,
    recorded_at: datetime,
) -> None:
    session.add(
        MetricSnapshot(
            league_id=league_id,
            team_id=team_id,
            batch_id=batch_id,
            key=key,
            period=period,
            value_float=value,
            recorded_at=recorded_at,
        )
    )


def record_metric_snapshots(
    session: Session,
    league_id: int,
    period: int,
    *,
    recorded_at: datetime | None = None,
    batch_id: str | None = None,
) -> int:
    """Append current tracked metrics atomically, returning values written.

    The nested transaction is intentional: if any one row fails, the whole
    snapshot batch rolls back while the caller may still finish the broader
    sync and report a non-fatal snapshot error.
    """
    now = recorded_at or datetime.now(UTC)
    sync_batch = batch_id or uuid4().hex
    session.flush()
    values = list(
        session.execute(
            select(Metric.team_id, Metric.key, Metric.value_float).where(
                Metric.league_id == league_id,
                Metric.team_id.is_not(None),
                Metric.key.in_(TRACKED_SCORE_KEYS),
                Metric.value_float.is_not(None),
                Metric.week.is_(None),
            )
        )
    )
    with session.begin_nested():
        for team_id, key, value in values:
            _write_snapshot(
                session,
                league_id=league_id,
                team_id=team_id,
                batch_id=sync_batch,
                key=key,
                period=period,
                value=float(value),
                recorded_at=now,
            )
        session.flush()
    return len(values)


def metric_momentum(
    session: Session,
    league_id: int,
    team_id: int | None,
    key: str,
    current_value: float | None,
) -> Momentum:
    """Return period-over-period delta and consecutive movement streak.

    A flat period breaks momentum. A reversal starts a new one-move direction;
    the UI hides streaks below two moves. Pending current metrics never diff
    against stale history.
    """
    if team_id is None or current_value is None:
        return Momentum("pending", None, None, 0, 0)
    events = list(
        session.scalars(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.league_id == league_id,
                MetricSnapshot.team_id == team_id,
                MetricSnapshot.key == key,
            )
            .order_by(
                MetricSnapshot.period.desc(),
                MetricSnapshot.recorded_at.desc(),
                MetricSnapshot.id.desc(),
            )
        )
    )
    rows: list[MetricSnapshot] = []
    seen_periods: set[int] = set()
    for event in events:
        if event.period not in seen_periods:
            rows.append(event)
            seen_periods.add(event.period)
    if len(rows) < 2:
        return Momentum("first_sync", None, None, 0, len(rows))

    delta = round(rows[0].value_float - rows[1].value_float, 1)
    if delta == 0:
        return Momentum("flat", 0.0, None, 0, len(rows))

    direction = "up" if delta > 0 else "down"
    streak = 0
    for newer, older in zip(rows, rows[1:], strict=False):
        movement = round(newer.value_float - older.value_float, 1)
        if movement == 0 or (movement > 0) != (direction == "up"):
            break
        streak += 1
    return Momentum(direction, delta, direction, streak, len(rows))


def team_achievements(
    session: Session,
    league: League,
    team_id: int | None,
) -> list[Achievement]:
    """Small status badges derived from persisted facts and inclusive thresholds."""
    if team_id is None:
        return []
    session.flush()
    metric_values = dict(
        session.execute(
            select(Metric.key, Metric.value_float).where(
                Metric.league_id == league.id,
                Metric.team_id == team_id,
                Metric.key.in_((LINEUP_EFFICIENCY, DRAFT_SURPLUS, ALL_PLAY_WIN_PCT)),
                Metric.week.is_(None),
            )
        ).all()
    )
    out: list[Achievement] = []
    if league.last_sync_ok is True:
        out.append(Achievement("sync_healthy", "Sync healthy", "Most recent sync completed"))
    efficiency = metric_values.get(LINEUP_EFFICIENCY)
    if efficiency is not None and efficiency >= LINEUP_ELITE_MIN:
        out.append(
            Achievement(
                "lineup_elite",
                "Lineup 90%+",
                f"Lineup efficiency is {efficiency * 100:.1f}%",
            )
        )
    surplus = metric_values.get(DRAFT_SURPLUS)
    if surplus is not None and surplus > 0:
        out.append(Achievement("draft_value", "Draft value", "Draft surplus is positive"))
    all_play = metric_values.get(ALL_PLAY_WIN_PCT)
    if all_play is not None and all_play >= ALL_PLAY_EDGE_MIN:
        out.append(
            Achievement(
                "all_play_edge",
                "All-play 60%+",
                f"All-play win rate is {all_play * 100:.1f}%",
            )
        )
    return out
