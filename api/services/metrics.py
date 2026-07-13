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
    EDGE_INDEX_ORDER,
    LEAGUE_SOFTNESS_ORDER,
    MY_EDGE_ORDER,
    component_label,
    component_weight,
    edge_index_label,
    edge_index_weight,
    grade_for,
    league_softness_label,
    league_softness_weight,
    my_edge_label,
    my_edge_weight,
    verdict_for,
)
from ..models import DraftPick, League, LineupSlot, Matchup, Metric, Player, Team, Transaction
from .espn_constants import slot_name
from .playoff_sim import SimGame, SimTeam, simulate_playoff_odds

EDGE_SCORE = "edge_score"
PLAYOFF_ODDS = "playoff_odds"
# Persisted component rows are keyed edge_component_<name> (Phase 9 breakdown).
COMPONENT_PREFIX = "edge_component_"
# Phase 10: informational team draft-surplus metric (not part of edge_score yet).
DRAFT_SURPLUS = "draft_surplus"
# Phase 12: all-play + luck metrics (informational; not part of edge_score yet).
ALL_PLAY_WINS = "all_play_wins"
ALL_PLAY_LOSSES = "all_play_losses"
ALL_PLAY_TIES = "all_play_ties"
ALL_PLAY_WIN_PCT = "all_play_win_pct"
LUCK_DELTA = "luck_delta"
# Every all-play key, so a team with no completed sample gets all of them cleared.
_ALL_PLAY_KEYS = (
    ALL_PLAY_WINS,
    ALL_PLAY_LOSSES,
    ALL_PLAY_TIES,
    ALL_PLAY_WIN_PCT,
    LUCK_DELTA,
)
# Phase 13: lineup efficiency metrics (informational; not part of edge_score yet).
LINEUP_EFFICIENCY = "lineup_efficiency"
STARTED_POINTS_AVG = "started_points_avg"
OPTIMAL_POINTS_AVG = "optimal_points_avg"
POINTS_LEFT_ON_BENCH_AVG = "points_left_on_bench_avg"
_LINEUP_KEYS = (
    LINEUP_EFFICIENCY,
    STARTED_POINTS_AVG,
    OPTIMAL_POINTS_AVG,
    POINTS_LEFT_ON_BENCH_AVG,
)
# Phase 14: MyEdge v1 — a separate score + component percentiles (not the edge_score).
MY_EDGE_SCORE = "my_edge_score"
MY_EDGE_COMPONENT_PREFIX = "my_edge_component_"
# All persisted MyEdge keys, for reading and for clearing when inputs go unavailable.
_MY_EDGE_ALL_KEYS = (MY_EDGE_SCORE, *(MY_EDGE_COMPONENT_PREFIX + k for k in MY_EDGE_ORDER))
# Phase 15: LeagueSoftness v1 — a separate score + component percentiles (not edge_score).
LEAGUE_SOFTNESS_SCORE = "league_softness_score"
LEAGUE_SOFTNESS_COMPONENT_PREFIX = "league_softness_component_"
_LEAGUE_SOFTNESS_ALL_KEYS = (
    LEAGUE_SOFTNESS_SCORE,
    *(LEAGUE_SOFTNESS_COMPONENT_PREFIX + k for k in LEAGUE_SOFTNESS_ORDER),
)
# Phase 16: full Edge Index v1 — composite of MyEdge + LeagueSoftness (not the edge_score).
EDGE_INDEX_SCORE = "edge_index_score"
EDGE_INDEX_COMPONENT_PREFIX = "edge_index_component_"
_EDGE_INDEX_ALL_KEYS = (
    EDGE_INDEX_SCORE,
    *(EDGE_INDEX_COMPONENT_PREFIX + k for k in EDGE_INDEX_ORDER),
)

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


# --------------------------------------------------------------------------- #
# Phase 12: all-play record + luck delta (pure; not folded into edge_score yet)
# --------------------------------------------------------------------------- #
@dataclass
class AllPlayRow:
    """A team's actual vs all-play record and the luck delta between them."""

    team_id: int
    actual_wins: int
    actual_losses: int
    actual_ties: int
    actual_win_pct: float
    all_play_wins: int
    all_play_losses: int
    all_play_ties: int
    all_play_win_pct: float | None  # None when no completed all-play sample
    luck_delta: float | None  # all_play_win_pct − actual_win_pct; None when no sample


def compute_all_play(
    teams: list[TeamStat], weekly_scores: dict[int, dict[int, float]]
) -> dict[int, AllPlayRow]:
    """All-play record + luck delta per team from completed weekly scores.

    `weekly_scores` maps week → {team_id: score} for **completed regular-season** weeks.
    For each such week with ≥2 scored teams, every team is scored against every other
    scored team (win/loss/tie). `luck_delta = all_play_win_pct − actual_win_pct`; positive
    means the team's scoring quality outran its actual record. A team with no completed
    all-play games gets None win_pct/luck (→ metrics cleared)."""
    ids = {t.team_id for t in teams}
    wins = {tid: 0 for tid in ids}
    losses = {tid: 0 for tid in ids}
    ties = {tid: 0 for tid in ids}
    for scores in weekly_scores.values():
        scored = {tid: s for tid, s in scores.items() if tid in ids and s is not None}
        if len(scored) < 2:  # need a field to play against
            continue
        for tid, s in scored.items():
            for other_tid, other_s in scored.items():
                if other_tid == tid:
                    continue
                if s > other_s:
                    wins[tid] += 1
                elif s < other_s:
                    losses[tid] += 1
                else:
                    ties[tid] += 1

    out: dict[int, AllPlayRow] = {}
    for t in teams:
        tid = t.team_id
        games = wins[tid] + losses[tid] + ties[tid]
        actual_wp = win_pct(t)
        if games == 0:
            out[tid] = AllPlayRow(
                tid, t.wins, t.losses, t.ties, actual_wp, 0, 0, 0, None, None
            )
        else:
            ap_wp = round((wins[tid] + 0.5 * ties[tid]) / games, 4)
            out[tid] = AllPlayRow(
                tid, t.wins, t.losses, t.ties, actual_wp,
                wins[tid], losses[tid], ties[tid], ap_wp, round(ap_wp - actual_wp, 4),
            )
    return out


def _weekly_scores(
    matchups, ids: set[int], completed_weeks: set[int] | None
) -> dict[int, dict[int, float]]:
    """Group completed regular-season matchup scores by week → {team_id: score}.

    Playoff matchups are ignored. A week counts as completed when it's in
    `completed_weeks`; with `completed_weeks is None` (manual/test recompute) fall back to
    the conservative "either side scored > 0" heuristic (mirrors `_split_matchups`)."""
    weekly: dict[int, dict[int, float]] = {}
    for m in matchups:
        if m.is_playoff:
            continue
        if completed_weeks is not None:
            played = m.week in completed_weeks
        else:
            played = (m.home_points or 0) > 0 or (m.away_points or 0) > 0
        if not played:
            continue
        wk = weekly.setdefault(m.week, {})
        if m.home_team_id in ids and m.home_points is not None:
            wk[m.home_team_id] = m.home_points
        if m.away_team_id in ids and m.away_points is not None:
            wk[m.away_team_id] = m.away_points
    return weekly


def _all_play_by_team(
    session: Session, league: League, stats: list[TeamStat], completed_weeks: set[int] | None
) -> dict[int, AllPlayRow]:
    matchups = session.scalars(select(Matchup).where(Matchup.league_id == league.id))
    weekly = _weekly_scores(matchups, {s.team_id for s in stats}, completed_weeks)
    return compute_all_play(stats, weekly)


# --------------------------------------------------------------------------- #
# Phase 13: lineup efficiency (pure solver; not folded into edge_score yet)
# --------------------------------------------------------------------------- #
# Dedicated starting slots → the single position that fills them.
_DEDICATED_SLOTS: dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "D/ST": "D/ST",
}
_FLEX_ELIGIBLE: tuple[str, ...] = ("RB", "WR", "TE")
# Starting slot names the solver understands (everything else — bench/IR/IDP — is ignored).
_SUPPORTED_SLOTS: frozenset[str] = frozenset({*_DEDICATED_SLOTS, "FLEX"})
_KNOWN_POSITIONS: frozenset[str] = frozenset({"QB", "RB", "WR", "TE", "K", "D/ST"})


@dataclass
class LineupEntry:
    """One roster spot in a team's week: player position, points, and whether started."""

    position: str | None
    points: float | None
    is_starter: bool


@dataclass
class LineupWeek:
    started_points: float
    optimal_points: float
    points_left_on_bench: float
    lineup_efficiency: float


def optimal_lineup_points(entries: list[LineupEntry], slot_counts: dict[str, int]) -> float:
    """Best legal lineup total from a team's full roster for one week.

    Fills each dedicated slot with the top scorers of its position, then FLEX slots with the
    best remaining RB/WR/TE — optimal for a single RB/WR/TE flex. Unknown positions (and
    None points) are never placed. Only supported slot types are considered."""
    by_pos: dict[str, list[float]] = {}
    for e in entries:
        if e.position in _KNOWN_POSITIONS and e.points is not None:
            by_pos.setdefault(e.position, []).append(e.points)
    for pts in by_pos.values():
        pts.sort(reverse=True)

    used: dict[str, int] = {}
    total = 0.0
    for slot, pos in _DEDICATED_SLOTS.items():
        avail = by_pos.get(pos, [])
        for _ in range(slot_counts.get(slot, 0)):
            i = used.get(pos, 0)
            if i < len(avail):
                total += avail[i]
                used[pos] = i + 1

    flex_need = slot_counts.get("FLEX", 0)
    if flex_need:
        remaining: list[float] = []
        for pos in _FLEX_ELIGIBLE:
            avail = by_pos.get(pos, [])
            remaining.extend(avail[used.get(pos, 0):])
        remaining.sort(reverse=True)
        total += sum(remaining[:flex_need])
    return total


def compute_lineup_week(
    entries: list[LineupEntry], slot_counts: dict[str, int]
) -> LineupWeek | None:
    """started/optimal/bench/efficiency for one team-week. None (pending) if optimal <= 0."""
    started = sum(e.points for e in entries if e.is_starter and e.points is not None)
    optimal = optimal_lineup_points(entries, slot_counts)
    if optimal <= 0:
        return None
    return LineupWeek(
        started_points=round(started, 2),
        optimal_points=round(optimal, 2),
        points_left_on_bench=round(optimal - started, 2),
        lineup_efficiency=round(started / optimal, 4),
    )


@dataclass
class LineupEfficiencyRow:
    team_id: int
    weeks: int
    lineup_efficiency: float  # points-weighted: Σ started / Σ optimal
    started_points_avg: float
    optimal_points_avg: float
    points_left_on_bench_avg: float


def compute_lineup_efficiency(
    team_weeks: dict[int, dict[int, list[LineupEntry]]], slot_counts: dict[str, int]
) -> dict[int, LineupEfficiencyRow]:
    """Per-team season lineup efficiency from {team_id: {week: entries}}.

    Season efficiency is points-weighted (Σ started / Σ optimal) across valid weeks; the
    other fields are per-week means. Teams with no valid week are omitted (→ cleared)."""
    out: dict[int, LineupEfficiencyRow] = {}
    for team_id, weeks in team_weeks.items():
        started_tot = optimal_tot = bench_tot = 0.0
        n = 0
        for entries in weeks.values():
            wk = compute_lineup_week(entries, slot_counts)
            if wk is None:
                continue
            started_tot += wk.started_points
            optimal_tot += wk.optimal_points
            bench_tot += wk.points_left_on_bench
            n += 1
        if n == 0 or optimal_tot <= 0:
            continue
        out[team_id] = LineupEfficiencyRow(
            team_id=team_id,
            weeks=n,
            lineup_efficiency=round(started_tot / optimal_tot, 4),
            started_points_avg=round(started_tot / n, 2),
            optimal_points_avg=round(optimal_tot / n, 2),
            points_left_on_bench_avg=round(bench_tot / n, 2),
        )
    return out


def _starting_slot_counts(lineup_slots_json: dict | None) -> dict[str, int]:
    """Translate ESPN lineupSlotCounts (slot_id → count) into supported starting slot names."""
    out: dict[str, int] = {}
    if not lineup_slots_json:
        return out
    for sid_str, count in lineup_slots_json.items():
        try:
            name = slot_name(int(sid_str))
        except (ValueError, TypeError):
            continue
        if name in _SUPPORTED_SLOTS and count:
            out[name] = out.get(name, 0) + int(count)
    return out


def _lineup_efficiency_by_team(
    session: Session, league: League, ids: set[int], completed_weeks: set[int] | None
) -> dict[int, LineupEfficiencyRow]:
    slot_counts = _starting_slot_counts(league.lineup_slots_json)
    if not slot_counts:
        return {}
    rows = session.execute(
        select(
            LineupSlot.team_id, LineupSlot.week, LineupSlot.points,
            LineupSlot.is_starter, Player.position,
        )
        .join(Player, Player.espn_player_id == LineupSlot.espn_player_id, isouter=True)
        .where(LineupSlot.league_id == league.id)
    ).all()
    team_weeks: dict[int, dict[int, list[LineupEntry]]] = {}
    for team_id, week, points, is_starter, position in rows:
        if team_id not in ids:
            continue
        if completed_weeks is not None and week not in completed_weeks:
            continue
        team_weeks.setdefault(team_id, {}).setdefault(week, []).append(
            LineupEntry(position=position, points=points, is_starter=bool(is_starter))
        )
    return compute_lineup_efficiency(team_weeks, slot_counts)


# --------------------------------------------------------------------------- #
# Phase 14: MyEdge v1 — a separate score blended from the built foundations
# --------------------------------------------------------------------------- #
@dataclass
class MyEdgeInput:
    """Per-team raw values for the MyEdge components (None = unknown for that team)."""

    team_id: int
    roster_strength: float | None
    draft_surplus: float | None
    lineup_efficiency: float | None
    luck_adjusted_record: float | None


@dataclass
class MyEdgeComponent:
    key: str
    label: str
    weight: float  # renormalized across the components present for the team
    percentile: float


@dataclass
class MyEdgeRow:
    team_id: int
    my_edge_score: float | None
    components: list[MyEdgeComponent]


def _my_edge_components_for(
    present: list[tuple[str, float]]
) -> list[MyEdgeComponent]:
    """Build MyEdge components with weights renormalized across the present keys."""
    base_total = sum(my_edge_weight(k) for k, _ in present) or 1.0
    return [
        MyEdgeComponent(
            key=key, label=my_edge_label(key), weight=my_edge_weight(key) / base_total,
            percentile=pct,
        )
        for key, pct in present
    ]


def compute_my_edge(inputs: list[MyEdgeInput]) -> dict[int, MyEdgeRow]:
    """MyEdge v1 per team: within-league percentile of each available component, blended
    with renormalized SPEC §6.2 weights. A component is available only when ≥2 teams have a
    value; per-team weights renormalize across the components present (a lone component →
    weight 1.0). No inputs present → pending (score None, no components)."""
    populations: dict[str, list[float]] = {}
    for key in MY_EDGE_ORDER:
        vals = [v for i in inputs if (v := getattr(i, key)) is not None]
        if len(vals) >= 2:
            populations[key] = vals

    out: dict[int, MyEdgeRow] = {}
    for i in inputs:
        present: list[tuple[str, float]] = []
        for key in MY_EDGE_ORDER:
            if key in populations:
                v = getattr(i, key)
                if v is not None:
                    present.append((key, _percentile(populations[key], v)))
        if not present:
            out[i.team_id] = MyEdgeRow(i.team_id, None, [])
            continue
        comps = _my_edge_components_for(present)
        out[i.team_id] = MyEdgeRow(
            i.team_id, round(sum(c.weight * c.percentile for c in comps), 1), comps
        )
    return out


# --------------------------------------------------------------------------- #
# Phase 15: LeagueSoftness v1 — a separate per-team "how exploitable are my opponents"
# --------------------------------------------------------------------------- #
@dataclass
class SoftnessTeam:
    """Per-team inputs for LeagueSoftness. Opponents = all other teams in the league."""

    team_id: int
    points_for: float
    played: bool
    autodrafted: bool
    lineup_efficiency: float | None
    draft_surplus: float | None
    transactions: int | None  # None = league has no transaction data (unknown, not zero)


@dataclass
class SoftnessComponent:
    key: str
    label: str
    weight: float  # renormalized across the components present for the team
    percentile: float


@dataclass
class SoftnessRow:
    team_id: int
    league_softness_score: float | None
    components: list[SoftnessComponent]


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _opponent_values(teams: list[SoftnessTeam], self_id: int, attr: str) -> list[float]:
    return [
        v for t in teams if t.team_id != self_id and (v := getattr(t, attr)) is not None
    ]


def _softness_raw(teams: list[SoftnessTeam]) -> dict[str, dict[int, float]]:
    """Per-team raw softness value per component (higher = softer). Missing = omitted."""
    raw: dict[str, dict[int, float]] = {key: {} for key in LEAGUE_SOFTNESS_ORDER}

    for t in teams:
        opp_eff = _opponent_values(teams, t.team_id, "lineup_efficiency")
        if opp_eff:
            raw["opponent_lineup_inefficiency"][t.team_id] = 1.0 - _median(opp_eff)
        opp_surplus = _opponent_values(teams, t.team_id, "draft_surplus")
        if opp_surplus:
            raw["opponent_draft_indiscipline"][t.team_id] = -_median(opp_surplus)
        opp_txn = _opponent_values(teams, t.team_id, "transactions")
        if opp_txn:
            raw["opponent_inactivity"][t.team_id] = -_median(opp_txn)

    # exploitable_weakness_share: fraction of opponents below league_median − 1 SD (played).
    played = [t for t in teams if t.played]
    if len(played) >= 2:
        pfs = [t.points_for for t in played]
        mean = sum(pfs) / len(pfs)
        sd = (sum((x - mean) ** 2 for x in pfs) / len(pfs)) ** 0.5
        threshold = _median(pfs) - sd
        for t in teams:
            opps = [o for o in teams if o.team_id != t.team_id and o.played]
            if opps:
                weak = sum(1 for o in opps if o.points_for < threshold)
                raw["exploitable_weakness_share"][t.team_id] = weak / len(opps)

    # abandoned_proxy: fraction of opponents that autodrafted.
    for t in teams:
        opps = [o for o in teams if o.team_id != t.team_id]
        if opps:
            raw["abandoned_proxy"][t.team_id] = sum(1 for o in opps if o.autodrafted) / len(opps)
    return raw


def _softness_components_for(present: list[tuple[str, float]]) -> list[SoftnessComponent]:
    """Build components with equal weights renormalized across the present keys."""
    base_total = sum(league_softness_weight(k) for k, _ in present) or 1.0
    return [
        SoftnessComponent(
            key=key, label=league_softness_label(key),
            weight=league_softness_weight(key) / base_total, percentile=pct,
        )
        for key, pct in present
    ]


def compute_league_softness(teams: list[SoftnessTeam]) -> dict[int, SoftnessRow]:
    """LeagueSoftness v1 per team: within-league percentile of each available opponent-derived
    softness component, equal-weighted (renormalized across present). A component is available
    only when ≥2 teams have a raw value AND those values are not all identical (a real
    percentile). No component available → pending (score None)."""
    raw = _softness_raw(teams)
    populations = {
        key: vals for key, vals in raw.items() if len(vals) >= 2 and len(set(vals.values())) >= 2
    }
    out: dict[int, SoftnessRow] = {}
    for t in teams:
        present: list[tuple[str, float]] = []
        for key in LEAGUE_SOFTNESS_ORDER:
            vals = populations.get(key)
            if vals is not None and t.team_id in vals:
                present.append((key, _percentile(list(vals.values()), vals[t.team_id])))
        if not present:
            out[t.team_id] = SoftnessRow(t.team_id, None, [])
            continue
        comps = _softness_components_for(present)
        out[t.team_id] = SoftnessRow(
            t.team_id, round(sum(c.weight * c.percentile for c in comps), 1), comps
        )
    return out


def _transaction_counts_by_team(session: Session, league_id: int) -> dict[int, int] | None:
    """Per-team transaction counts, or None when the league has no transaction rows at all
    (an empty feed is unknown, not proof of zero activity — SPEC §2.4 / Phase 15)."""
    total = session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.league_id == league_id)
    )
    if not total:
        return None
    rows = session.execute(
        select(Transaction.team_id, func.count())
        .where(Transaction.league_id == league_id, Transaction.team_id.is_not(None))
        .group_by(Transaction.team_id)
    ).all()
    return {tid: c for tid, c in rows}


# --------------------------------------------------------------------------- #
# Phase 16: full Edge Index composite — 0.5 × MyEdge + 0.5 × LeagueSoftness
# --------------------------------------------------------------------------- #
@dataclass
class EdgeIndexComponent:
    key: str
    label: str
    weight: float  # renormalized across the halves present
    value: float  # the already-0–100 sub-score (not a percentile)


@dataclass
class EdgeIndexRow:
    team_id: int
    edge_index_score: float | None
    grade: str | None
    verdict: str | None
    components: list[EdgeIndexComponent]


def _edge_index_components_for(present: list[tuple[str, float]]) -> list[EdgeIndexComponent]:
    base_total = sum(edge_index_weight(k) for k, _ in present) or 1.0
    return [
        EdgeIndexComponent(
            key=key, label=edge_index_label(key),
            weight=edge_index_weight(key) / base_total, value=value,
        )
        for key, value in present
    ]


def compute_edge_index_row(
    team_id: int, my_edge: float | None, league_softness: float | None
) -> EdgeIndexRow:
    """Edge Index v1 for one team: 0.5·MyEdge + 0.5·LeagueSoftness on the 0–100 sub-scores.
    Only the present halves count (weights renormalized); neither present → pending.
    grade/verdict use the existing edge thresholds."""
    present: list[tuple[str, float]] = []
    if my_edge is not None:
        present.append(("my_edge", my_edge))
    if league_softness is not None:
        present.append(("league_softness", league_softness))
    if not present:
        return EdgeIndexRow(team_id, None, None, None, [])
    comps = _edge_index_components_for(present)
    score = round(sum(c.weight * c.value for c in comps), 1)
    return EdgeIndexRow(team_id, score, grade_for(score), verdict_for(score), comps)


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
    all_play_map = _all_play_by_team(session, league, stats, completed_weeks)
    lineup_map = _lineup_efficiency_by_team(
        session, league, {s.team_id for s in stats}, completed_weeks
    )
    # Phase 14: MyEdge blends the built foundations. roster_strength is gated on fresh
    # projections (mirrors the edge roster branch); the others read the same maps computed
    # above. This is a separate score — it never touches edge_score.
    my_edge_map = compute_my_edge([
        MyEdgeInput(
            team_id=t.id,
            roster_strength=(proj_by_team.get(t.id) if projections_fresh else None),
            draft_surplus=surplus_map.get(t.id),
            lineup_efficiency=(lineup_map[t.id].lineup_efficiency if t.id in lineup_map else None),
            luck_adjusted_record=(
                all_play_map[t.id].all_play_win_pct if t.id in all_play_map else None
            ),
        )
        for t in teams
    ])
    # Phase 15: LeagueSoftness — how exploitable each team's opponents are. Reads the same
    # maps plus per-team transaction counts (None when the feed is empty → unknown, not zero).
    # A separate score — never touches edge_score.
    txn_counts = _transaction_counts_by_team(session, league.id)
    softness_map = compute_league_softness([
        SoftnessTeam(
            team_id=t.id,
            points_for=t.points_for,
            played=(t.wins + t.losses + t.ties) > 0,
            autodrafted=bool(t.autodrafted),
            lineup_efficiency=(lineup_map[t.id].lineup_efficiency if t.id in lineup_map else None),
            draft_surplus=surplus_map.get(t.id),
            transactions=(txn_counts.get(t.id, 0) if txn_counts is not None else None),
        )
        for t in teams
    ])

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
        # Phase 12: all-play + luck (cleared for a team with no completed all-play sample).
        ap = all_play_map.get(stat.team_id)
        has_sample = ap is not None and ap.all_play_win_pct is not None
        _upsert_or_clear(
            session, league.id, stat.team_id, ALL_PLAY_WINS,
            float(ap.all_play_wins) if has_sample else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, ALL_PLAY_LOSSES,
            float(ap.all_play_losses) if has_sample else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, ALL_PLAY_TIES,
            float(ap.all_play_ties) if has_sample else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, ALL_PLAY_WIN_PCT,
            ap.all_play_win_pct if has_sample else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, LUCK_DELTA,
            ap.luck_delta if has_sample else None,
        )
        # Phase 13: lineup efficiency (cleared for a team with no valid lineup sample).
        le = lineup_map.get(stat.team_id)
        _upsert_or_clear(
            session, league.id, stat.team_id, LINEUP_EFFICIENCY,
            le.lineup_efficiency if le else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, STARTED_POINTS_AVG,
            le.started_points_avg if le else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, OPTIMAL_POINTS_AVG,
            le.optimal_points_avg if le else None,
        )
        _upsert_or_clear(
            session, league.id, stat.team_id, POINTS_LEFT_ON_BENCH_AVG,
            le.points_left_on_bench_avg if le else None,
        )
        # Phase 14: MyEdge score + component percentiles; clear every component key that's
        # not present so a stale input can't survive (separate from edge_score).
        me = my_edge_map.get(stat.team_id)
        _upsert_or_clear(
            session, league.id, stat.team_id, MY_EDGE_SCORE,
            me.my_edge_score if me else None,
        )
        present_me = {c.key: c.percentile for c in (me.components if me else [])}
        for key in MY_EDGE_ORDER:
            _upsert_or_clear(
                session, league.id, stat.team_id, MY_EDGE_COMPONENT_PREFIX + key,
                present_me.get(key),
            )
        # Phase 15: LeagueSoftness score + component percentiles; clear absent keys so a
        # stale input can't survive (separate from edge_score).
        sf = softness_map.get(stat.team_id)
        _upsert_or_clear(
            session, league.id, stat.team_id, LEAGUE_SOFTNESS_SCORE,
            sf.league_softness_score if sf else None,
        )
        present_sf = {c.key: c.percentile for c in (sf.components if sf else [])}
        for key in LEAGUE_SOFTNESS_ORDER:
            _upsert_or_clear(
                session, league.id, stat.team_id, LEAGUE_SOFTNESS_COMPONENT_PREFIX + key,
                present_sf.get(key),
            )
        # Phase 16: Edge Index composite = 0.5·MyEdge + 0.5·LeagueSoftness. Reads the two
        # sub-scores computed above; clears when neither half is present (separate from
        # edge_score).
        ei = compute_edge_index_row(
            stat.team_id,
            me.my_edge_score if me else None,
            sf.league_softness_score if sf else None,
        )
        _upsert_or_clear(session, league.id, stat.team_id, EDGE_INDEX_SCORE, ei.edge_index_score)
        present_ei = {c.key: c.value for c in ei.components}
        for key in EDGE_INDEX_ORDER:
            _upsert_or_clear(
                session, league.id, stat.team_id, EDGE_INDEX_COMPONENT_PREFIX + key,
                present_ei.get(key),
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


def read_all_play(session: Session, league_id: int) -> list[AllPlayRow]:
    """Persisted all-play/luck rows for a league (Phase 12), teams with a completed sample
    only, ordered by all-play win% descending. Reads metrics + Team record; no recompute."""
    teams = list(session.scalars(select(Team).where(Team.league_id == league_id)))
    rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.key.in_(_ALL_PLAY_KEYS),
            Metric.week.is_(None),
        )
    ).all()
    by_team: dict[int, dict[str, float]] = {}
    for tid, key, val in rows:
        by_team.setdefault(tid, {})[key] = val

    out: list[AllPlayRow] = []
    for t in teams:
        m = by_team.get(t.id)
        if not m or m.get(ALL_PLAY_WIN_PCT) is None:
            continue  # no completed all-play sample for this team
        stat = TeamStat(
            t.id, t.wins, t.losses, t.ties, t.points_for, t.points_against, t.standing, None
        )
        out.append(
            AllPlayRow(
                team_id=t.id,
                actual_wins=t.wins,
                actual_losses=t.losses,
                actual_ties=t.ties,
                actual_win_pct=round(win_pct(stat), 4),
                all_play_wins=int(m.get(ALL_PLAY_WINS, 0)),
                all_play_losses=int(m.get(ALL_PLAY_LOSSES, 0)),
                all_play_ties=int(m.get(ALL_PLAY_TIES, 0)),
                all_play_win_pct=m[ALL_PLAY_WIN_PCT],
                luck_delta=m.get(LUCK_DELTA),
            )
        )
    out.sort(key=lambda r: r.all_play_win_pct, reverse=True)
    return out


def read_lineup_efficiency(session: Session, league_id: int) -> list[LineupEfficiencyRow]:
    """Persisted lineup-efficiency rows for a league (Phase 13), teams with a completed
    sample only, ordered by efficiency descending. Reads metrics only; no recompute."""
    rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.key.in_(_LINEUP_KEYS),
            Metric.week.is_(None),
        )
    ).all()
    by_team: dict[int, dict[str, float]] = {}
    for tid, key, val in rows:
        by_team.setdefault(tid, {})[key] = val

    out: list[LineupEfficiencyRow] = []
    for tid, m in by_team.items():
        if m.get(LINEUP_EFFICIENCY) is None:
            continue
        out.append(
            LineupEfficiencyRow(
                team_id=tid,
                weeks=0,  # not persisted; the view exposes averages, not the week count
                lineup_efficiency=m[LINEUP_EFFICIENCY],
                started_points_avg=m.get(STARTED_POINTS_AVG, 0.0),
                optimal_points_avg=m.get(OPTIMAL_POINTS_AVG, 0.0),
                points_left_on_bench_avg=m.get(POINTS_LEFT_ON_BENCH_AVG, 0.0),
            )
        )
    out.sort(key=lambda r: r.lineup_efficiency, reverse=True)
    return out


def read_my_edge(session: Session, league_id: int) -> list[MyEdgeRow]:
    """Persisted MyEdge rows for a league (Phase 14), teams with a score only, ordered by
    my_edge_score descending. Reconstructs component weights (renormalized across the present
    components) from edge_config; reads metrics only, no recompute."""
    rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.key.in_(_MY_EDGE_ALL_KEYS),
            Metric.week.is_(None),
        )
    ).all()
    by_team: dict[int, dict[str, float]] = {}
    for tid, key, val in rows:
        by_team.setdefault(tid, {})[key] = val

    out: list[MyEdgeRow] = []
    for tid, m in by_team.items():
        score = m.get(MY_EDGE_SCORE)
        if score is None:
            continue
        present = [
            (key, m[MY_EDGE_COMPONENT_PREFIX + key])
            for key in MY_EDGE_ORDER
            if (MY_EDGE_COMPONENT_PREFIX + key) in m
        ]
        out.append(MyEdgeRow(tid, score, _my_edge_components_for(present)))
    out.sort(key=lambda r: r.my_edge_score, reverse=True)
    return out


def read_league_softness(session: Session, league_id: int) -> list[SoftnessRow]:
    """Persisted LeagueSoftness rows for a league (Phase 15), teams with a score only, ordered
    by league_softness_score descending. Reconstructs weights (renormalized across present
    components) from edge_config; reads metrics only, no recompute."""
    rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.key.in_(_LEAGUE_SOFTNESS_ALL_KEYS),
            Metric.week.is_(None),
        )
    ).all()
    by_team: dict[int, dict[str, float]] = {}
    for tid, key, val in rows:
        by_team.setdefault(tid, {})[key] = val

    out: list[SoftnessRow] = []
    for tid, m in by_team.items():
        score = m.get(LEAGUE_SOFTNESS_SCORE)
        if score is None:
            continue
        present = [
            (key, m[LEAGUE_SOFTNESS_COMPONENT_PREFIX + key])
            for key in LEAGUE_SOFTNESS_ORDER
            if (LEAGUE_SOFTNESS_COMPONENT_PREFIX + key) in m
        ]
        out.append(SoftnessRow(tid, score, _softness_components_for(present)))
    out.sort(key=lambda r: r.league_softness_score, reverse=True)
    return out


def read_edge_index(session: Session, league_id: int) -> list[EdgeIndexRow]:
    """Persisted Edge Index rows for a league (Phase 16), teams with a score only, ordered by
    edge_index_score descending. Reconstructs component weights (renormalized across present
    halves) + grade/verdict from the persisted score; reads metrics only, no recompute."""
    rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.key.in_(_EDGE_INDEX_ALL_KEYS),
            Metric.week.is_(None),
        )
    ).all()
    by_team: dict[int, dict[str, float]] = {}
    for tid, key, val in rows:
        by_team.setdefault(tid, {})[key] = val

    out: list[EdgeIndexRow] = []
    for tid, m in by_team.items():
        score = m.get(EDGE_INDEX_SCORE)
        if score is None:
            continue
        present = [
            (key, m[EDGE_INDEX_COMPONENT_PREFIX + key])
            for key in EDGE_INDEX_ORDER
            if (EDGE_INDEX_COMPONENT_PREFIX + key) in m
        ]
        out.append(
            EdgeIndexRow(
                tid, score, grade_for(score), verdict_for(score),
                _edge_index_components_for(present),
            )
        )
    out.sort(key=lambda r: r.edge_index_score, reverse=True)
    return out


@dataclass
class TeamEdgeIndex:
    edge_index_score: float | None
    grade: str | None
    verdict: str | None


def team_edge_index(session: Session, league_id: int, team_id: int | None) -> TeamEdgeIndex:
    """Persisted Edge Index score + derived grade/verdict for one team (Phase 16 portfolio)."""
    if team_id is None:
        return TeamEdgeIndex(None, None, None)
    score = session.scalar(
        select(Metric.value_float).where(
            Metric.league_id == league_id,
            Metric.team_id == team_id,
            Metric.key == EDGE_INDEX_SCORE,
            Metric.week.is_(None),
        )
    )
    return TeamEdgeIndex(score, grade_for(score), verdict_for(score))
