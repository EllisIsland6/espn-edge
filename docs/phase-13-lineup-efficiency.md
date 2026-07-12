# Phase 13 — lineup efficiency foundation (contract)

Compute and display each team's **started-vs-optimal** lineup efficiency from persisted
`lineup_slots`. This is the foundation for SPEC §6.1 opponent lineup inefficiency and §6.2
"my lineup efficiency" — but it is **not** folded into `edge_score` yet.

## Scope (v1)
- Pure helpers in `api/services/metrics.py`:
  - Per team-week: `started_points` (points of the actual starters), `optimal_points` (best
    legal lineup from the full roster that week), `points_left_on_bench =
    optimal_points − started_points`, and `lineup_efficiency = started_points / optimal_points`.
  - If `optimal_points <= 0`, the team-week is **pending** (returned as None, skipped).
- **Legal-lineup solver**: supports QB, RB, WR, TE, FLEX (RB/WR/TE), K, D/ST. Respects ESPN
  `lineupSlotCounts` (from `leagues.lineup_slots_json`). Ignores bench/IR/reserve and any
  other slot type. **Unknown player positions are never placed in the optimal lineup.**
  Fills dedicated position slots with the top scorers of that position, then FLEX slots with
  the best remaining RB/WR/TE — optimal for a single RB/WR/TE flex.

## Weeks
- Uses completed **regular-season** weeks. `lineup_slots` only ever holds completed weeks
  (sync writes boxscores per `completed_weeks`); when `completed_weeks` is provided we
  intersect with it, else use every week present.

## Aggregation
- `lineup_efficiency` is season-level **points-weighted**: `Σ started / Σ optimal` over the
  team's valid weeks (more stable than averaging weekly ratios).
- `started_points_avg`, `optimal_points_avg`, `points_left_on_bench_avg` are per-week means.

## Persistence
- Team metrics with `week = NULL`: `lineup_efficiency`, `started_points_avg`,
  `optimal_points_avg`, `points_left_on_bench_avg`. Cleared (all four) for a team with **no
  valid completed lineup sample**. No schema change → **no `make db-reset`**.

## API / UI
- `GET /api/leagues/{id}/lineup-efficiency` → rows ordered by `lineup_efficiency` descending
  (teams with a sample only): `team_id`, `team_name`, `lineup_efficiency`,
  `started_points_avg`, `optimal_points_avg`, `points_left_on_bench_avg`.
- League detail shows a compact Lineup Efficiency table. React formats backend values only.

## Invariants (unchanged)
- `edge_score`, `grade`, `verdict`, `playoff_odds`, Phase 9/11 components, and Phase 12
  all-play/luck are untouched; the in-season `82.5` fixtures stay byte-identical.

## Acceptance criteria
- Pure solver tests: normal lineup, FLEX choice, unknown positions excluded, zero-optimal
  pending.
- Metrics persist and clear when no valid sample exists.
- `/api/leagues/{id}/lineup-efficiency` returns rows ordered by efficiency desc.
- League detail renders the table (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Fold my-team lineup efficiency into SPEC §6.2 MyEdge (weight 0.20) and opponent lineup
  inefficiency into §6.1 LeagueSoftness; add points-left-on-bench trends.
