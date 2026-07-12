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

# Drafted / no-games-yet branch: the single roster-projection component carries the
# whole score (weight 1.0), so edge_score == its within-league percentile.
DRAFTED_WEIGHTS: dict[str, float] = {"roster_proj": 1.0}

# Human labels for edge_score components (Phase 9 breakdown). Keys match the metric
# rows persisted as edge_component_<key>. Canonical display order.
COMPONENT_LABELS: dict[str, str] = {
    "win_pct": "Win %",
    "points_for": "Points for",
    "point_diff": "Point differential",
    "roster_proj": "Roster projection",
}
# Canonical order for reading/rendering components (in-season trio, then drafted).
COMPONENT_ORDER: tuple[str, ...] = ("win_pct", "points_for", "point_diff", "roster_proj")


def component_weight(key: str) -> float:
    """Weight of a component within its branch's edge_score (in-season or drafted)."""
    if key in INSEASON_WEIGHTS:
        return INSEASON_WEIGHTS[key]
    return DRAFTED_WEIGHTS.get(key, 0.0)


def component_label(key: str) -> str:
    """Human label for a component key (falls back to the key itself)."""
    return COMPONENT_LABELS.get(key, key)

# Verdict thresholds on the 0–100 edge_score (SPEC §6).
VERDICT_ADVANTAGED_MIN = 65.0
VERDICT_NEUTRAL_MIN = 45.0

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
