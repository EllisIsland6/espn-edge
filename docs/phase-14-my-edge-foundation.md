# Phase 14 — MyEdge component foundation (contract)

Assemble a **separate, persisted** MyEdge v1 score + component breakdown from the
foundations already built (roster projection, draft surplus, lineup efficiency,
all-play/luck). This is a **new metric** — it does **not** replace or modify the existing
`edge_score`, which stays byte-identical.

## Scope (v1)
- Within-league percentile components, blended with SPEC §6.2 MyEdge weights:
  - `roster_strength` (0.35) — summed `players.proj_ros` of drafted players; **unavailable**
    when projections are stale (`projections_fresh=False`) or fewer than 2 teams have a value.
  - `draft_surplus` (0.25) — the persisted raw `draft_surplus` (Phase 10).
  - `lineup_efficiency` (0.20) — the persisted `lineup_efficiency` (Phase 13).
  - `luck_adjusted_record` (0.10) — the persisted `all_play_win_pct` (Phase 12); `luck_delta`
    stays as supporting context (not a component here).
  - `waiver_capture` (0.10) — intentionally **pending / not included yet**.
- A component is *available* only when **≥2 teams** have a value for it (a percentile needs a
  population). Per team, weights are **renormalized across the components present** for that
  team, so a lone component lands at weight 1.0. `my_edge_score = round(Σ weight·percentile, 1)`.

## Persistence
- Team metrics with `week = NULL`: `my_edge_score`, and `my_edge_component_<name>` for
  `roster_strength` / `draft_surplus` / `lineup_efficiency` / `luck_adjusted_record`.
  Component values are **percentiles**, not raw values.
- Every possible MyEdge key is upserted-or-cleared each recompute, so stale rows can't
  survive an input becoming unavailable. No schema change → **no `make db-reset`**.

## API / UI
- `GET /api/leagues/{id}/my-edge` → rows ordered by `my_edge_score` descending (teams with a
  score only): `team_id`, `team_name`, `is_me`, `my_edge_score`, and `components`
  (`key`, `label`, `weight`, `percentile`).
- League detail → Overview shows a compact **MyEdge (v1)** panel for my team (score +
  component bars). React formats backend values only.

## Invariants (unchanged)
- `edge_score`, `grade`, `verdict`, `playoff_odds`, the Phase 9/11 edge components, Phase 12
  all-play/luck, and Phase 13 lineup efficiency are all untouched; the in-season `82.5`
  fixtures stay byte-identical. MyEdge is a **parallel** score, not a replacement.

## Acceptance criteria
- Pure MyEdge computation is hand-computable (percentiles + weight renormalization).
- Missing-component and stale-projection cases behave (component dropped / roster_strength
  unavailable).
- MyEdge metrics persist and clear when inputs become unavailable.
- `/api/leagues/{id}/my-edge` returns rows ordered by score desc.
- Overview renders the MyEdge panel (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Add `waiver_capture` (0.10) once transaction-derived on-roster scoring lands, then decide
  whether MyEdge (blended with LeagueSoftness) supersedes the current `edge_score` as the
  full SPEC §6 Edge Index — with cross-league population normalization + p5/p95 winsorization.
