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

# playoff_odds heuristic clamp (never assert 0/100% mid-season).
PLAYOFF_ODDS_FLOOR = 0.02
PLAYOFF_ODDS_CEIL = 0.98


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
