"""Analytics engine (Phase 3 v1) — deterministic, explainable edge metrics.

Isolated from ESPN fetching and UI rendering: pure functions operate on plain
dataclasses (unit-testable with no DB), and the DB-facing `recompute_league` /
`team_edge` read from and write to the `metrics` table only. No cookies/SWID/espn_s2
are touched. Contract: docs/phase-3-analytics.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..edge_config import (
    COMPONENT_ORDER,
    DRAFT_VALUE_DECAY,
    component_label,
    component_weight,
    grade_for,
    verdict_for,
)
from ..models import DraftPick, League, Matchup, Metric, Player, Team
from .playoff_sim import SimGame, SimTeam, simulate_playoff_odds

EDGE_SCORE = "edge_score"
PLAYOFF_ODDS = "playoff_odds"
# Persisted component rows are keyed edge_component_<name> (Phase 9 breakdown).
COMPONENT_PREFIX = "edge_component_"
# Phase 10: informational team draft-surplus metric (not part of edge_score yet).
DRAFT_SURPLUS = "draft_surplus"

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
    draft_surplus: float | None = None  # Phase 11: team draft surplus; None if unknown


def games_played(t: TeamStat) -> int:
    return t.wins + t.losses + t.ties


def win_pct(t: TeamStat) -> float:
    g = games_played(t)
    return (t.wins + 0.5 * t.ties) / g if g else 0.0


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
@dataclass
class EdgeComponent:
    """One weighted, within-league percentile that feeds edge_score (Phase 9).

    `percentile` is the exact (unrounded) value so the reduction to edge_score stays
    byte-identical to the pre-Phase-9 formula; only the final sum is rounded.
    """

    key: str
    label: str
    weight: float
    percentile: float


def _component(key: str, percentile: float) -> EdgeComponent:
    return EdgeComponent(
        key=key, label=component_label(key), weight=component_weight(key), percentile=percentile
    )


def compute_edge_components(
    teams: list[TeamStat], lifecycle: str, projections_fresh: bool = True
) -> dict[int, list[EdgeComponent]]:
    """Per-team weighted component percentiles behind edge_score, or [] (pending).

    Same branching as the score: pre_draft → pending; in_season/complete *with games*
    → the win_pct/points_for/point_diff trio; otherwise the preseason branch (guarded
    by `projections_fresh`) which blends roster_proj and draft_surplus percentiles.
    edge_score is the weighted mean of exactly these components (see `compute_edge_scores`).
    """
    if not teams:
        return {}
    if lifecycle == "pre_draft":
        return {t.team_id: [] for t in teams}

    any_games = any(games_played(t) > 0 for t in teams)
    if lifecycle in _RECORD_LIFECYCLES and any_games:
        wps = [win_pct(t) for t in teams]
        pfs = [t.points_for for t in teams]
        diffs = [t.points_for - t.points_against for t in teams]
        return {
            t.team_id: [
                _component("win_pct", _percentile(wps, win_pct(t))),
                _component("points_for", _percentile(pfs, t.points_for)),
                _component("point_diff", _percentile(diffs, t.points_for - t.points_against)),
            ]
            for t in teams
        }

    # Preseason (drafted, or in_season with no games yet): roster projection + draft surplus.
    if not projections_fresh:
        # Player pool wasn't refreshed → don't publish preseason components from stale data.
        return {t.team_id: [] for t in teams}
    return _preseason_components(teams)


# Preseason components and the per-team value on each (Phase 11).
_PRESEASON_KEYS: tuple[str, ...] = ("roster_proj", "draft_surplus")


def _preseason_value(team: TeamStat, key: str) -> float | None:
    return team.roster_proj if key == "roster_proj" else team.draft_surplus


def _preseason_components(teams: list[TeamStat]) -> dict[int, list[EdgeComponent]]:
    """Blend of within-league percentiles for roster_proj and draft_surplus, with weights
    renormalized across the components actually present for each team.

    A component is *available* only when ≥2 teams have a value for it (a percentile needs a
    population). With only roster_proj available this reduces to weight 1.0 on roster_proj —
    byte-identical to the pre-Phase-11 roster-only score.
    """
    populations: dict[str, list[float]] = {}
    for key in _PRESEASON_KEYS:
        vals = [v for t in teams if (v := _preseason_value(t, key)) is not None]
        if len(vals) >= 2:  # need a population to rank against
            populations[key] = vals
    if not populations:
        return {t.team_id: [] for t in teams}

    out: dict[int, list[EdgeComponent]] = {}
    for t in teams:
        present: list[tuple[str, float]] = []
        for key in _PRESEASON_KEYS:
            if key in populations:
                v = _preseason_value(t, key)
                if v is not None:
                    present.append((key, _percentile(populations[key], v)))
        if not present:
            out[t.team_id] = []
            continue
        base_total = sum(component_weight(key) for key, _ in present)
        out[t.team_id] = [
            EdgeComponent(
                key=key,
                label=component_label(key),
                weight=component_weight(key) / base_total,  # renormalize across present
                percentile=pct,
            )
            for key, pct in present
        ]
    return out


def _reduce_components(components: list[EdgeComponent]) -> float | None:
    """edge_score from components: weighted mean, rounded once. [] → None (pending)."""
    if not components:
        return None
    return round(sum(c.weight * c.percentile for c in components), 1)


# --------------------------------------------------------------------------- #
# Phase 10: draft-surplus foundation (pure; not folded into edge_score yet)
# --------------------------------------------------------------------------- #
def pick_value(overall: float) -> float:
    """Smooth draft pick-value curve v(p)=100·e^(−p/DECAY) (SPEC §6.2): steep early,
    flat late. A lower pick number is worth more."""
    return 100.0 * math.exp(-overall / DRAFT_VALUE_DECAY)


def pick_surplus(adp_at_draft: float | None, overall: float | None) -> float | None:
    """Value captured on one pick: pick_value(adp) − pick_value(overall). None when the
    pick's ADP or overall position is unknown."""
    if adp_at_draft is None or overall is None:
        return None
    return pick_value(adp_at_draft) - pick_value(overall)


def compute_draft_surplus(picks: list[tuple[float | None, float | None]]) -> float | None:
    """Team draft surplus = sum of known per-pick surpluses. None when no pick has both
    an ADP and an overall position (→ metric cleared)."""
    surpluses = [
        s for adp, overall in picks if (s := pick_surplus(adp, overall)) is not None
    ]
    return round(sum(surpluses), 3) if surpluses else None


def compute_edge_scores(
    teams: list[TeamStat], lifecycle: str, projections_fresh: bool = True
) -> dict[int, float | None]:
    """edge_score (0–100) per team, or None (pending) when inputs are insufficient.

    Derived from `compute_edge_components` so score and breakdown never drift; the
    numbers are byte-identical to the pre-Phase-9 formula (same weights/percentiles,
    a single final round). `projections_fresh` guards the roster-projection branch.
    """
    comps = compute_edge_components(teams, lifecycle, projections_fresh=projections_fresh)
    return {tid: _reduce_components(cs) for tid, cs in comps.items()}


def _split_matchups(
    matchups, ids: set[int], completed_weeks: set[int] | None
) -> tuple[dict[int, list[float]], list[SimGame]]:
    """Partition matchups into per-team completed-game scores and remaining games.

    A game counts as *played* (a scoring sample) only if its week is in
    `completed_weeks`. This keeps a **current-week partial score** — which ESPN can
    expose mid-week — out of the samples and in the remaining schedule. When
    `completed_weeks is None` (manual/test recompute with no schedule context), fall
    back to the legacy "either side scored > 0" heuristic.
    """
    played_scores: dict[int, list[float]] = {tid: [] for tid in ids}
    remaining: list[SimGame] = []
    for m in matchups:
        if completed_weeks is not None:
            is_played = m.week in completed_weeks
        else:
            is_played = (m.home_points or 0) > 0 or (m.away_points or 0) > 0
        if is_played:
            if m.home_team_id in ids and m.home_points is not None:
                played_scores[m.home_team_id].append(m.home_points)
            if m.away_team_id in ids and m.away_points is not None:
                played_scores[m.away_team_id].append(m.away_points)
        elif not m.is_playoff and m.home_team_id in ids and m.away_team_id in ids:
            remaining.append(SimGame(m.home_team_id, m.away_team_id))
    return played_scores, remaining


def _standings_odds(teams: list[Team], spots: int) -> dict[int, float]:
    """Deterministic 1.0/0.0 by ESPN standing (which encodes league tiebreakers we
    don't model). Falls back to (wins, points_for) ranking if a standing is missing."""
    if all(t.standing is not None for t in teams):
        return {t.id: (1.0 if t.standing <= spots else 0.0) for t in teams}
    ranked = sorted(teams, key=lambda t: (t.wins + 0.5 * t.ties, t.points_for), reverse=True)
    made = {t.id: 0.0 for t in teams}
    for t in ranked[:spots]:
        made[t.id] = 1.0
    return made


def compute_playoff_odds_for_league(
    session: Session,
    league: League,
    teams: list[Team],
    completed_weeks: set[int] | None = None,
) -> dict[int, float | None]:
    """Playoff odds per team (Phase 5). complete → deterministic 1/0 from final
    standings; in_season with a remaining schedule → seeded Monte Carlo; in_season with
    no remaining regular-season games → deterministic by ESPN standing; otherwise
    pending. Never fabricates a schedule/settings (docs/phase-5-playoff-exports.md)."""
    spots = league.playoff_team_count
    if league.lifecycle == "complete":
        if not spots:
            return {t.id: None for t in teams}
        return _standings_odds(teams, spots)
    if league.lifecycle != "in_season" or not spots:
        return {t.id: None for t in teams}

    ids = {t.id for t in teams}
    matchups = session.scalars(select(Matchup).where(Matchup.league_id == league.id))
    played_scores, remaining = _split_matchups(matchups, ids, completed_weeks)

    if not remaining:
        # No games left to simulate. If we saw any completed games, the regular season
        # is effectively decided → rank by ESPN standing. If there's no schedule at all
        # (nothing played, nothing remaining), we can't say anything → pending.
        if any(played_scores[t.id] for t in teams):
            return _standings_odds(teams, spots)
        return {t.id: None for t in teams}

    # Can't model a team with no completed-game scores → pending for the whole league.
    if any(not played_scores[t.id] for t in teams):
        return {t.id: None for t in teams}

    sim_teams = [
        SimTeam(
            team_id=t.id, wins=t.wins, losses=t.losses, ties=t.ties,
            points_for=t.points_for, played_scores=played_scores[t.id],
        )
        for t in teams
    ]
    odds = simulate_playoff_odds(sim_teams, remaining, spots)
    if not odds:
        return {t.id: None for t in teams}
    return {t.id: odds.get(t.id) for t in teams}


# --------------------------------------------------------------------------- #
# DB-facing recompute + persistence (SPEC §4: everything traces to a metrics row)
# --------------------------------------------------------------------------- #
def recompute_league(
    session: Session,
    league: League,
    projections_fresh: bool = True,
    completed_weeks: set[int] | None = None,
) -> dict:
    """Recompute + persist edge_score/playoff_odds for every team in the league.

    Idempotent: upserts computed values, deletes rows that are now pending. Called
    at the end of every sync so metrics always reflect current DB state.

    `projections_fresh=False` (kona_player_info failed this sync) forces
    roster-projection-based edge scores to pending, so a drafted/no-games league
    can't serve freshly stamped scores derived from stale proj_ros.

    `completed_weeks` (from the sync) scopes which weeks count as played for the
    playoff simulation so current-week partial scores aren't treated as completed
    games; None falls back to a points-based heuristic (see `_split_matchups`).
    """
    teams = list(session.scalars(select(Team).where(Team.league_id == league.id)))
    proj_by_team = _roster_projection_by_team(session, league.id)
    surplus_map = _draft_surplus_by_team(session, league.id)

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
            draft_surplus=surplus_map.get(t.id),  # Phase 11: feeds the preseason blend
        )
        for t in teams
    ]
    comps = compute_edge_components(stats, league.lifecycle, projections_fresh=projections_fresh)
    odds_map = compute_playoff_odds_for_league(session, league, teams, completed_weeks)

    scored = 0
    for stat in stats:
        team_comps = comps.get(stat.team_id, [])
        score = _reduce_components(team_comps)
        if score is not None:
            scored += 1
        _upsert_or_clear(session, league.id, stat.team_id, EDGE_SCORE, score)
        _upsert_or_clear(session, league.id, stat.team_id, PLAYOFF_ODDS, odds_map.get(stat.team_id))
        # Persist each component; clear every other component key so a stale row from a
        # previous branch (e.g. roster_proj after a league starts playing) can't survive.
        present = {c.key: c.percentile for c in team_comps}
        for key in COMPONENT_ORDER:
            _upsert_or_clear(
                session, league.id, stat.team_id, COMPONENT_PREFIX + key, present.get(key)
            )
        # Phase 10: informational draft surplus (cleared when no valid pick values).
        _upsert_or_clear(
            session, league.id, stat.team_id, DRAFT_SURPLUS, surplus_map.get(stat.team_id)
        )
    session.flush()
    return {"teams": len(stats), "scored": scored}


def _draft_surplus_by_team(session: Session, league_id: int) -> dict[int, float | None]:
    """Per-team draft surplus from persisted DraftPick adp_at_draft/overall (Phase 10)."""
    rows = session.execute(
        select(DraftPick.team_id, DraftPick.adp_at_draft, DraftPick.overall).where(
            DraftPick.league_id == league_id, DraftPick.team_id.is_not(None)
        )
    ).all()
    by_team: dict[int, list[tuple[float | None, float | None]]] = {}
    for tid, adp, overall in rows:
        by_team.setdefault(tid, []).append((adp, overall))
    return {tid: compute_draft_surplus(picks) for tid, picks in by_team.items()}


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


def team_components(session: Session, league_id: int, team_id: int | None) -> list[EdgeComponent]:
    """Persisted edge_score components for one team, in canonical order (Phase 9).

    Returns [] when pending. Labels/weights come from edge_config so the view layer
    never recomputes analytics — it only renders what the DB already holds.
    """
    if team_id is None:
        return []
    rows = dict(
        session.execute(
            select(Metric.key, Metric.value_float).where(
                Metric.league_id == league_id,
                Metric.team_id == team_id,
                Metric.key.startswith(COMPONENT_PREFIX),
                Metric.week.is_(None),
            )
        ).all()
    )
    out: list[EdgeComponent] = []
    for key in COMPONENT_ORDER:
        pct = rows.get(COMPONENT_PREFIX + key)
        if pct is not None:
            out.append(_component(key, pct))
    return out
