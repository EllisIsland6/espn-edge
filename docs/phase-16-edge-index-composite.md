# Phase 16 — full Edge Index composite (contract)

Assemble the first true SPEC §6 Edge Index v1: `0.5 × MyEdge + 0.5 × LeagueSoftness`, as a
**separate persisted composite** from the Phase 14 `my_edge_score` and Phase 15
`league_softness_score`. The existing `edge_score` (the Phase 3 within-league v1) stays
**byte-identical** — Edge Index is a parallel score, not a replacement, for now.

## Scope (v1)
- Composite weights (`api/edge_config.py`): `my_edge` 0.5, `league_softness` 0.5.
- Per team, `edge_index_score = round(Σ weight·value, 1)` where the **values are the already
  0–100 sub-scores** `my_edge_score` and `league_softness_score` (NOT percentiles).
- If only one half is present, its weight renormalizes to 1.0. If neither half is present,
  the score/components are pending (cleared).
- `grade` / `verdict` derive from `edge_index_score` using the **existing** `grade_for` /
  `verdict_for` thresholds (same 0–100 bands as `edge_score`).

## Persistence
- Team metrics with `week = NULL`: `edge_index_score`, `edge_index_component_my_edge`,
  `edge_index_component_league_softness`. Component values are the sub-scores (0–100).
  Every key is upserted-or-cleared each recompute, so a stale row can't survive a half
  going unavailable. No schema change → **no db-reset**.

## API / UI
- `GET /api/leagues/{id}/edge-index` → rows ordered by `edge_index_score` descending (teams
  with a score only): `team_id`, `team_name`, `is_me`, `edge_index_score`, `grade`,
  `verdict`, and `components` (`key`, `label`, `weight`, `value`).
- League detail → Overview shows an **Edge Index (v1)** panel for my team, beside MyEdge and
  LeagueSoftness. React formats backend values only.

## Portfolio
- Portfolio rows gain `edge_index_score`, `edge_index_grade`, `edge_index_verdict`
  (nullable). The Portfolio Board keeps showing the existing `edge_score` for now — Edge
  Index is carried alongside so the transition is explicit and non-breaking. The summary
  aggregates are unchanged (still driven by `edge_score`).

## Invariants (unchanged)
- `edge_score`, `grade`, `verdict`, `playoff_odds`, and all Phase 9–15 metric semantics are
  untouched; the in-season `82.5` fixtures stay byte-identical.

## Acceptance criteria
- Pure composite with both halves; missing-half renormalization; pending when neither half
  exists.
- Metrics persist and clear when a half becomes unavailable.
- `/api/leagues/{id}/edge-index` returns rows ordered by score desc with grade/verdict.
- Portfolio rows expose the new fields; the board's `edge_score` display is unchanged.
- Overview renders the Edge Index panel (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Once validated on real leagues, consider promoting Edge Index to the primary board score
  (retiring the Phase 3 `edge_score`), add `waiver_capture` to MyEdge, and layer cross-league
  population normalization + p5/p95 winsorization (full SPEC §6 normalization rule).
