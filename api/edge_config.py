"""Edge Index tunables — one file so weights/thresholds are adjustable without
hunting through code (SPEC §6). See docs/phase-3-analytics.md for the contract.

Phase 3 v1 is deterministic and within-league; the population-normalized model is
future work.
"""

from __future__ import annotations

# In-season / complete edge_score component weights (must sum to 1.0). Each component
# is a within-league percentile in [0, 100]; the score is their weighted mean.
INSEASON_WEIGHTS: dict[str, float] = {
    "win_pct": 0.40,
    "points_for": 0.30,
    "point_diff": 0.30,
}

# Drafted / no-games-yet (preseason) branch weights (Phase 11). Base weights come from
# SPEC §6.2 MyEdge (roster strength 0.35, draft surplus 0.25); we renormalize that pair to
# sum to 1.0 so preseason edge_score stays on the same 0–100 scale. When only one component
# is available for a team the score renormalizes across what's present (see metrics.py), so
# a roster-only preseason league keeps its exact Phase 9/10 roster-percentile behavior.
_DRAFTED_BASE_WEIGHTS: dict[str, float] = {"roster_proj": 0.35, "draft_surplus": 0.25}
_DRAFTED_BASE_TOTAL = sum(_DRAFTED_BASE_WEIGHTS.values())
DRAFTED_WEIGHTS: dict[str, float] = {
    k: v / _DRAFTED_BASE_TOTAL for k, v in _DRAFTED_BASE_WEIGHTS.items()
}

# Human labels for edge_score components (Phase 9 breakdown). Keys match the metric
# rows persisted as edge_component_<key>. Canonical display order.
COMPONENT_LABELS: dict[str, str] = {
    "win_pct": "Win %",
    "points_for": "Points for",
    "point_diff": "Point differential",
    "roster_proj": "Roster projection",
    "draft_surplus": "Draft surplus",
}
# Canonical order for reading/rendering components (in-season trio, then preseason pair).
COMPONENT_ORDER: tuple[str, ...] = (
    "win_pct",
    "points_for",
    "point_diff",
    "roster_proj",
    "draft_surplus",
)


def component_weight(key: str) -> float:
    """Base weight of a component within its branch (in-season sums to 1.0; the preseason
    pair also sums to 1.0). The drafted branch renormalizes across the components actually
    present for a team, so a lone component ends up at weight 1.0."""
    if key in INSEASON_WEIGHTS:
        return INSEASON_WEIGHTS[key]
    return DRAFTED_WEIGHTS.get(key, 0.0)


def component_label(key: str) -> str:
    """Human label for a component key (falls back to the key itself)."""
    return COMPONENT_LABELS.get(key, key)


# --- Phase 14: MyEdge v1 (a separate score; does NOT touch edge_score) --------------
# SPEC §6.2 MyEdge weights. waiver_capture (0.10) is intentionally pending/not included yet,
# so these sum to 0.90 — MyEdge renormalizes across whichever components are present.
MY_EDGE_WEIGHTS: dict[str, float] = {
    "roster_strength": 0.35,
    "draft_surplus": 0.25,
    "lineup_efficiency": 0.20,
    "luck_adjusted_record": 0.10,
}
MY_EDGE_LABELS: dict[str, str] = {
    "roster_strength": "Roster strength",
    "draft_surplus": "Draft surplus",
    "lineup_efficiency": "Lineup efficiency",
    "luck_adjusted_record": "Luck-adjusted record",
}
# Canonical order for reading/rendering MyEdge components.
MY_EDGE_ORDER: tuple[str, ...] = (
    "roster_strength",
    "draft_surplus",
    "lineup_efficiency",
    "luck_adjusted_record",
)


def my_edge_weight(key: str) -> float:
    """Base MyEdge weight for a component (renormalized across present components)."""
    return MY_EDGE_WEIGHTS.get(key, 0.0)


def my_edge_label(key: str) -> str:
    return MY_EDGE_LABELS.get(key, key)


# --- Phase 15: LeagueSoftness v1 (a separate score; does NOT touch edge_score) -------
# SPEC §6.1 uses an equal-weighted mean of the softness components, so base weights are
# equal; the score renormalizes across whichever components are present for a team.
LEAGUE_SOFTNESS_WEIGHTS: dict[str, float] = {
    "opponent_lineup_inefficiency": 0.20,
    "opponent_draft_indiscipline": 0.20,
    "exploitable_weakness_share": 0.20,
    "abandoned_proxy": 0.20,
    "opponent_inactivity": 0.20,
}
LEAGUE_SOFTNESS_LABELS: dict[str, str] = {
    "opponent_lineup_inefficiency": "Opponent lineup inefficiency",
    "opponent_draft_indiscipline": "Opponent draft indiscipline",
    "exploitable_weakness_share": "Exploitable weakness share",
    "abandoned_proxy": "Abandoned teams (proxy)",
    "opponent_inactivity": "Opponent inactivity",
}
LEAGUE_SOFTNESS_ORDER: tuple[str, ...] = (
    "opponent_lineup_inefficiency",
    "opponent_draft_indiscipline",
    "exploitable_weakness_share",
    "abandoned_proxy",
    "opponent_inactivity",
)


def league_softness_weight(key: str) -> float:
    """Base LeagueSoftness weight (equal); renormalized across present components."""
    return LEAGUE_SOFTNESS_WEIGHTS.get(key, 0.0)


def league_softness_label(key: str) -> str:
    return LEAGUE_SOFTNESS_LABELS.get(key, key)

# Verdict thresholds on the 0–100 edge_score (SPEC §6).
VERDICT_ADVANTAGED_MIN = 65.0
VERDICT_NEUTRAL_MIN = 45.0

# Phase 10: draft pick-value curve v(p) = 100·e^(−p/DECAY) (SPEC §6.2). Steep early, flat
# late; ~100 at pick 1, ~5 by pick 100. Used for draft_surplus (not in edge_score yet).
DRAFT_VALUE_DECAY = 34.0

# Letter-grade bands, checked high → low: (min_inclusive, grade).
GRADE_BANDS: tuple[tuple[float, str], ...] = (
    (80.0, "A"),
    (65.0, "B"),
    (50.0, "C"),
    (40.0, "D"),
    (0.0, "F"),
)

# --- Phase 5: Monte Carlo playoff-odds simulation (docs/phase-5-playoff-exports.md) ---
# Number of simulated remaining-season runs. Higher = smoother odds, slower recompute.
SIM_COUNT = 10000
# Stable seed so simulations are reproducible and tests deterministic.
SIM_SEED = 20260101
# When a team has <2 played games, estimate weekly stdev as this fraction of its mean.
SIM_SIGMA_FALLBACK_FRAC = 0.16
# Floor on weekly stdev so a team never gets a degenerate (zero-variance) distribution.
SIM_SIGMA_FLOOR = 5.0


def grade_for(score: float | None) -> str | None:
    """Map an edge_score to a letter grade. None (pending) → None."""
    if score is None:
        return None
    for minimum, grade in GRADE_BANDS:
        if score >= minimum:
            return grade
    return "F"


def verdict_for(score: float | None) -> str | None:
    """Map an edge_score to advantaged/neutral/disadvantaged. None (pending) → None."""
    if score is None:
        return None
    if score >= VERDICT_ADVANTAGED_MIN:
        return "advantaged"
    if score >= VERDICT_NEUTRAL_MIN:
        return "neutral"
    return "disadvantaged"
