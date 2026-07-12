# Phase 15 — LeagueSoftness component foundation (contract)

Assemble a **separate, persisted** LeagueSoftness v1 score + component breakdown from data
already in the DB. It complements Phase 14 MyEdge — the two halves of the SPEC §6 Edge Index
— but it does **not** replace or modify the existing `edge_score`, which stays byte-identical.

## Perspective
LeagueSoftness is **per team**: for each team, its "opponents" are all other teams in the
league. A higher score means softer (more exploitable) opponents. Each component is a
within-league percentile of a team's opponent-derived raw softness value.

## Scope (v1) — SPEC §6.1 components (equal base weights)
- `opponent_lineup_inefficiency` — from opponents' persisted `lineup_efficiency` (Phase 13):
  raw = `1 − median(opponent lineup_efficiency)`.
- `opponent_draft_indiscipline` — from opponents' persisted raw `draft_surplus` (Phase 10):
  raw = `−median(opponent draft_surplus)` (weaker/negative surplus = softer).
- `exploitable_weakness_share` — fraction of opponents with
  `points_for < league_median − 1 SD`; only meaningful with played games (≥2 played teams).
- `abandoned_proxy` — fraction of opponents with `autodrafted == true` (preseason/current v1
  proxy for abandonment).
- `opponent_inactivity` — from transaction rows, raw = `−median(opponent transaction count)`.
  **Only computed when the league actually has transaction rows** — an empty ESPN
  transaction feed is treated as *unknown*, never as proof of zero activity (SPEC §2.4).

## Availability & weights
- A component is *available* only when it yields a real within-league percentile: **≥2 teams
  have a raw value and those values are not all identical**. Unavailable components are
  omitted; weights are **renormalized across the components present** for a team. Base
  weights are equal, so a team's score is the mean of its present component percentiles.
- `league_softness_score = round(Σ weight·percentile, 1)`.

## Persistence
- Team metrics with `week = NULL`: `league_softness_score` and
  `league_softness_component_<name>` for each of the five components. Values are
  **percentiles**, not raw. Every possible key is upserted-or-cleared each recompute, so a
  stale row can't survive an input becoming unavailable. No schema change → **no db-reset**.

## API / UI
- `GET /api/leagues/{id}/league-softness` → rows ordered by `league_softness_score`
  descending (teams with a score only): `team_id`, `team_name`, `is_me`,
  `league_softness_score`, and `components` (`key`, `label`, `weight`, `percentile`).
- League detail → Overview shows a compact **LeagueSoftness (v1)** panel for my team.

## Invariants (unchanged)
- `edge_score`, `grade`, `verdict`, `playoff_odds`, Phase 9/11 edge components, Phase 12
  all-play/luck, Phase 13 lineup efficiency, and Phase 14 MyEdge are all untouched; the
  in-season `82.5` fixtures stay byte-identical.

## Acceptance criteria
- Pure LeagueSoftness is hand-computable (percentiles + equal-weight renormalization).
- Missing-component and empty-transaction-feed cases behave (component omitted, not zeroed).
- Metrics persist and clear when inputs become unavailable.
- `/api/leagues/{id}/league-softness` returns rows ordered by score desc.
- Overview renders the LeagueSoftness panel (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Real opponent-inactivity from the activity/communication feed, per-week trends, and
  eventually combining MyEdge + LeagueSoftness into the full SPEC §6 Edge Index (cross-league
  population normalization + p5/p95 winsorization).
