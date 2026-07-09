"""Monte Carlo playoff-odds simulation (Phase 5, docs/phase-5-playoff-exports.md).

Pure and deterministic: given each team's season-to-date scores, the remaining
regular-season games, and the number of playoff spots, simulate the rest of the season
`n` times with a fixed seed and return each team's fraction of sims where it finishes in
the top `playoff_spots` by (wins, points_for). No DB, no network — unit-testable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from statistics import mean, pstdev

from ..edge_config import SIM_COUNT, SIM_SEED, SIM_SIGMA_FALLBACK_FRAC, SIM_SIGMA_FLOOR


@dataclass
class SimTeam:
    team_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    played_scores: list[float] = field(default_factory=list)


@dataclass
class SimGame:
    home_team_id: int
    away_team_id: int


def _distribution(t: SimTeam) -> tuple[float, float]:
    """(mu, sigma) for a team's weekly score, with a fallback sigma for thin samples."""
    mu = mean(t.played_scores)
    if len(t.played_scores) >= 2:
        sigma = pstdev(t.played_scores)
    else:
        sigma = SIM_SIGMA_FALLBACK_FRAC * mu
    return mu, max(sigma, SIM_SIGMA_FLOOR)


def simulate_playoff_odds(
    teams: list[SimTeam],
    remaining: list[SimGame],
    playoff_spots: int,
    *,
    n: int = SIM_COUNT,
    seed: int = SIM_SEED,
) -> dict[int, float]:
    """Return {team_id: playoff probability in [0, 1]}. Empty if it can't be simulated."""
    if not teams or not playoff_spots:
        return {}
    # Need scores to estimate at least one distribution; teams with no played games
    # can't be modeled, so bail to pending (caller returns None) rather than guess.
    if any(not t.played_scores for t in teams):
        return {}

    dist = {t.team_id: _distribution(t) for t in teams}
    base_wins = {t.team_id: t.wins + 0.5 * t.ties for t in teams}
    base_pf = {t.team_id: t.points_for for t in teams}
    made = dict.fromkeys((t.team_id for t in teams), 0)
    rng = random.Random(seed)

    for _ in range(n):
        wins = dict(base_wins)
        pf = dict(base_pf)
        for g in remaining:
            mh, sh = dist[g.home_team_id]
            ma, sa = dist[g.away_team_id]
            hs = rng.gauss(mh, sh)
            as_ = rng.gauss(ma, sa)
            pf[g.home_team_id] += hs
            pf[g.away_team_id] += as_
            if hs > as_:
                wins[g.home_team_id] += 1
            elif as_ > hs:
                wins[g.away_team_id] += 1
            else:
                wins[g.home_team_id] += 0.5
                wins[g.away_team_id] += 0.5
        # Rank by wins, then points-for; top `playoff_spots` make the playoffs.
        ranked = sorted(teams, key=lambda t: (wins[t.team_id], pf[t.team_id]), reverse=True)
        for t in ranked[:playoff_spots]:
            made[t.team_id] += 1

    return {tid: round(made[tid] / n, 3) for tid in made}
