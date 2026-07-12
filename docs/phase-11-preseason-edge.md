# Phase 11 — preseason Edge includes draft surplus (contract)

Use the Phase 10 `draft_surplus` foundation to make the **drafted / no-games** (preseason)
Edge Score more meaningful, by blending it with roster projection. In-season/complete
scoring is **untouched and byte-identical**. This is still a within-league v1 measure — not
the cross-league, population-normalized SPEC §6 Edge Index.

## Scope (v1)
- The preseason branch of `compute_edge_components` (drafted, or in_season with no games
  yet) now blends two within-league percentile components:
  - `roster_proj` — summed `players.proj_ros` of drafted players
  - `draft_surplus` — the Phase 10 team draft surplus
- Each blended component is persisted as its own row (`edge_component_roster_proj`,
  `edge_component_draft_surplus`); the value stored is the **percentile**, not the raw
  surplus. The raw `draft_surplus` metric (key `draft_surplus`) is unchanged.

## Weights
Base weights come from SPEC §6.2 MyEdge (roster strength 0.35, draft surplus 0.25),
renormalized so the pair sums to 1.0 (in `api/edge_config.py`):
- `roster_proj = 0.35 / (0.35 + 0.25) ≈ 0.5833`
- `draft_surplus = 0.25 / (0.35 + 0.25) ≈ 0.4167`

Per team, weights are **renormalized across the components actually present** for that team.

## Component availability
- If `projections_fresh == False`, the preseason branch stays **pending** and clears
  preseason components — stale preseason scores are never served.
- A component is *available* only when **≥2 teams** have a value for it (a percentile needs
  a population).
- `edge_score` is derived from the components present for a team, weights renormalized
  across them.
- **Roster-only is byte-identical**: when `draft_surplus` is unavailable, the sole
  `roster_proj` component renormalizes to weight 1.0, reproducing the exact Phase 9/10
  roster-percentile preseason score.
- When both are available, the preseason `edge_score` **intentionally changes**.

## Invariants (unchanged)
- In-season/complete scoring (win_pct / points_for / point_diff) is byte-identical; the
  exact `82.5` fixtures still pass.
- `grade`/`verdict` derivation, `playoff_odds`, and the raw `draft_surplus` metric are
  unchanged.
- No DB schema change (generic `metrics` rows) → **no `make db-reset`**.

## API / UI
- `LeagueOverview.components` exposes the new `Draft surplus` component automatically for my
  team; the Overview renders it as a bar with no frontend logic change.

## Acceptance criteria
- Preseason both-available blend is hand-computable and equals the weighted mean of the two
  percentiles.
- Roster-only preseason score is unchanged; stale projections → pending + cleared.
- `edge_component_draft_surplus` persists preseason and clears on a branch switch to
  in-season/complete or pending.
- `/api/leagues/{id}/overview` exposes the `draft_surplus` component for my team.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Add the remaining SPEC §6 MyEdge components (lineup efficiency, waiver capture,
  luck-adjusted record) and LeagueSoftness, then cross-league population normalization with
  p5/p95 winsorization — the point at which this becomes the full Edge Index.
