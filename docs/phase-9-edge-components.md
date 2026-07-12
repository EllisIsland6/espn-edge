# Phase 9 — Edge Score component breakdown (contract)

Make `edge_score` **explainable** by surfacing the within-league percentile components that
already drive it. This is an explainability slice only — **not** the full SPEC §6 Edge
Index (LeagueSoftness / MyEdge, lineup efficiency, all-play, luck, draft surplus). Those
remain future work.

## Scope (v1)
- Persist and expose the components that feed the existing v1 `edge_score`, and render them
  as bars in League detail → Overview.

## The components
Same branching as the score (see `compute_edge_components` / `compute_edge_scores`):

- **in_season / complete, with games played** — three within-league percentiles, weighted
  by the existing `INSEASON_WEIGHTS`:
  - `win_pct` (0.40), `points_for` (0.30), `point_diff` (0.30)
- **drafted, or in_season with no games yet** — a single component:
  - `roster_proj` (weight **1.0**), the within-league percentile of the team's summed
    `players.proj_ros`, **only** when projections are fresh this sync and ≥2 teams have a
    known projection.
- **pre_draft / insufficient data / stale projections** — **no components** (pending).

`edge_score` is the weighted mean of exactly these components, rounded once:
`edge_score = round(Σ weightᵢ · percentileᵢ, 1)`. Because the score is now *derived from*
the components, the two can never drift — and the values are **byte-identical** to the
pre-Phase-9 formula (guarded by the existing exact-score tests).

## Persistence
- Each component is a `metrics` row: `key = edge_component_<name>`, `team_id` set,
  `week = NULL`, `value_float = percentile`.
- On every `recompute_league`, each of the four possible component keys is upserted (if
  present) or cleared (if absent) via `_upsert_or_clear`. This guarantees a stale row from
  a previous branch (e.g. `roster_proj` after a league starts playing, or any component
  once a league reverts to pending) cannot survive.
- **No DB/schema change**: the `metrics` table already stores generic `(key, value_float)`
  rows, so no migration and **no `make db-reset`** is required.

## API / UI
- `LeagueOverview.components: list[EdgeComponent]` where
  `EdgeComponent = {key, label, weight, percentile}`, populated for **my team only** from
  the persisted rows in canonical order (`win_pct, points_for, point_diff, roster_proj`).
- League detail → Overview renders one `ValueBar` (max = 100) per component with its label,
  percentile, and weight. React only formats these values — it never recomputes analytics.
- `edge_score` / `grade` / `verdict` / `playoff_odds` are unchanged.

## Acceptance criteria
- Existing `edge_score`/`grade`/`verdict` values stay byte-identical (82.5 etc.).
- Components persist for scored teams, clear on pending, on stale projections, and on a
  drafted→in-season branch switch.
- `/api/leagues/{id}/overview` exposes `components` for my team.
- Overview renders component bars; a mocked Playwright test asserts labels + a bar value.
- No ESPN calls, no sync/parse changes, no DB schema change.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- The remaining SPEC §6 components as additional bars: lineup efficiency (`lineup_slots` +
  slot map), all-play + luck delta (`matchups`), draft surplus (needs `adp_at_draft` /
  `value_delta` populated in sync first), and LeagueSoftness.
- Population-normalized percentiles (across all synced leagues) with p5/p95 winsorization.
