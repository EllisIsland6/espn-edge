"""Analytics engine (Phase 3 v1) — deterministic, explainable edge metrics.

Isolated from ESPN fetching and UI rendering: pure functions operate on plain
dataclasses (unit-testable with no DB), and the DB-facing `recompute_league` /
`team_edge` read from and write to the `metrics` table only. No cookies/SWID/espn_s2
are touched. Contract: docs/phase-3-analytics.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..edge_config import (
    INSEASON_WEIGHTS,
    PLAYOFF_ODDS_CEIL,
    PLAYOFF_ODDS_FLOOR,
    grade_for,
    verdict_for,
)
from ..models import DraftPick, League, Metric, Player, Team

EDGE_SCORE = "edge_score"
PLAYOFF_ODDS = "playoff_odds"

# Lifecycles with completed games (record-based edge is meaningful).
_RECORD_LIFECYCLES = ("in_season", "complete")


@dataclass
class TeamStat:
    """Minimal per-team inputs for the analytics (no DB/ORM dependency)."""

    team_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    standing: int | None
    roster_proj: float | None  # summed proj_ros of drafted players; None if unknown


def games_played(t: TeamStat) -> int:
    return t.wins + t.losses + t.ties


def win_pct(t: TeamStat) -> float:
    g = games_played(t)
    return (t.wins + 0.5 * t.ties) / g if g else 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _percentile(values: list[float], v: float) -> float:
    """Within-population percentile of `v` in `values`, scaled 0–100 with mid-rank
    for ties. A single-element population → 50 (neutral)."""
    n = len(values)
    if n <= 1:
        return 50.0
    less = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    return (less + 0.5 * equal) / n * 100.0


# --------------------------------------------------------------------------- #
# Pure computation
# --------------------------------------------------------------------------- #
def compute_edge_scores(teams: list[TeamStat], lifecycle: str) -> dict[int, float | None]:
    """edge_score (0–100) per team, or None (pending) when inputs are insufficient."""
    if not teams:
        return {}
    if lifecycle == "pre_draft":
        return {t.team_id: None for t in teams}

    any_games = any(games_played(t) > 0 for t in teams)
    if lifecycle in _RECORD_LIFECYCLES and any_games:
        wps = [win_pct(t) for t in teams]
        pfs = [t.points_for for t in teams]
        diffs = [t.points_for - t.points_against for t in teams]
        out: dict[int, float | None] = {}
        for t in teams:
            score = (
                INSEASON_WEIGHTS["win_pct"] * _percentile(wps, win_pct(t))
                + INSEASON_WEIGHTS["points_for"] * _percentile(pfs, t.points_for)
                + INSEASON_WEIGHTS["point_diff"]
                * _percentile(diffs, t.points_for - t.points_against)
            )
            out[t.team_id] = round(score, 1)
        return out

    # drafted (or in_season with no games yet): roster-projection percentile.
    valid = [t.roster_proj for t in teams if t.roster_proj is not None]
    if len(valid) < 2:
        return {t.team_id: None for t in teams}
    out2: dict[int, float | None] = {}
    for t in teams:
        out2[t.team_id] = (
            round(_percentile(valid, t.roster_proj), 1) if t.roster_proj is not None else None
        )
    return out2


def compute_playoff_odds(
    t: TeamStat, lifecycle: str, size: int | None, playoff_spots: int | None
) -> float | None:
    """v1 heuristic playoff probability (NOT a simulation). None when pending."""
    if t.standing is None or not size or not playoff_spots:
        return None
    if lifecycle == "complete":
        return 1.0 if t.standing <= playoff_spots else 0.0
    if lifecycle == "in_season" and games_played(t) > 0:
        seed = _clamp((size - t.standing) / (size - 1), 0.0, 1.0) if size > 1 else 0.5
        odds = 0.5 * win_pct(t) + 0.5 * seed
        return round(_clamp(odds, PLAYOFF_ODDS_FLOOR, PLAYOFF_ODDS_CEIL), 3)
    return None


# --------------------------------------------------------------------------- #
# DB-facing recompute + persistence (SPEC §4: everything traces to a metrics row)
# --------------------------------------------------------------------------- #
def recompute_league(session: Session, league: League) -> dict:
    """Recompute + persist edge_score/playoff_odds for every team in the league.

    Idempotent: upserts computed values, deletes rows that are now pending. Called
    at the end of every sync so metrics always reflect current DB state.
    """
    teams = list(session.scalars(select(Team).where(Team.league_id == league.id)))
    proj_by_team = _roster_projection_by_team(session, league.id)

    stats = [
        TeamStat(
            team_id=t.id,
            wins=t.wins,
            losses=t.losses,
            ties=t.ties,
            points_for=t.points_for,
            points_against=t.points_against,
            standing=t.standing,
            roster_proj=proj_by_team.get(t.id),
        )
        for t in teams
    ]
    edges = compute_edge_scores(stats, league.lifecycle)

    scored = 0
    for stat in stats:
        score = edges.get(stat.team_id)
        if score is not None:
            scored += 1
        _upsert_or_clear(session, league.id, stat.team_id, EDGE_SCORE, score)
        odds = compute_playoff_odds(stat, league.lifecycle, league.size, league.playoff_team_count)
        _upsert_or_clear(session, league.id, stat.team_id, PLAYOFF_ODDS, odds)
    session.flush()
    return {"teams": len(stats), "scored": scored}


def _roster_projection_by_team(session: Session, league_id: int) -> dict[int, float]:
    """Sum of drafted players' proj_ros per team (only players with a projection)."""
    rows = session.execute(
        select(DraftPick.team_id, func.sum(Player.proj_ros))
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id)
        .where(
            DraftPick.league_id == league_id,
            DraftPick.team_id.is_not(None),
            Player.proj_ros.is_not(None),
        )
        .group_by(DraftPick.team_id)
    ).all()
    return {tid: float(total) for tid, total in rows if total is not None}


def _upsert_or_clear(
    session: Session, league_id: int, team_id: int, key: str, value: float | None
) -> None:
    existing = session.scalar(
        select(Metric).where(
            Metric.league_id == league_id,
            Metric.team_id == team_id,
            Metric.key == key,
            Metric.week.is_(None),
        )
    )
    if value is None:
        if existing is not None:
            session.delete(existing)
        return
    if existing is not None:
        existing.value_float = value
        existing.computed_at = datetime.now(UTC)
    else:
        session.add(
            Metric(league_id=league_id, team_id=team_id, key=key, week=None, value_float=value)
        )


# --------------------------------------------------------------------------- #
# Read helper for the view layer (derives grade/verdict; no React math)
# --------------------------------------------------------------------------- #
@dataclass
class TeamEdge:
    edge_score: float | None
    playoff_odds: float | None
    grade: str | None
    verdict: str | None


def team_edge(session: Session, league_id: int, team_id: int | None) -> TeamEdge:
    """Persisted edge_score/playoff_odds + derived grade/verdict for one team."""
    if team_id is None:
        return TeamEdge(None, None, None, None)
    edge = session.scalar(
        select(Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.team_id == team_id,
            Metric.key == EDGE_SCORE,
            Metric.week.is_(None),
        )
    )
    odds = session.scalar(
        select(Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.team_id == team_id,
            Metric.key == PLAYOFF_ODDS,
            Metric.week.is_(None),
        )
    )
    return TeamEdge(
        edge_score=edge,
        playoff_odds=odds,
        grade=grade_for(edge),
        verdict=verdict_for(edge),
    )
