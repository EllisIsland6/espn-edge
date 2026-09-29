# Phase 29 — Opportunity Visualizations

## Outcome

Turn the Phase 27 Opportunity page into a compact, publication-quality chart explorer using
only metrics already stored in `opportunity_weeks`. The charts must be as legible as the
provided quadrant examples while improving interaction, collision handling, accessibility,
responsive behavior, provenance, and failure states.

This phase adds no analytics feed, R runtime, paid service, AI call, projection, or proxy
metric. It does not claim to reproduce EPA/play, personnel, coverage, first-read, route,
win-rate, or injury charts. Those require fields that Phase 27 does not currently import.

## Exact chart set

Every chart uses the same last-three-observed-game window as Phase 27. Values stay unrounded
through calculation and are rounded only for display. A player needs at least two observed
games and finite values for both axes. The tooltip always shows the sample and through-week.

| ID | Default position | X | Y | Reference treatment | Honest interpretation |
| --- | --- | --- | --- | --- | --- |
| `target_air` | WR | Mean target share | Mean air-yards share | Position medians | Earning targets versus earning downfield opportunity |
| `yards_tds` | WR | Receiving yards/game | Receiving TDs/game | Position medians | Yardage and TD production shown together; not an expected-TD model |
| `adot_targets` | WR | Target-weighted aDOT | Targets/game | Position medians | Target depth versus target volume |
| `opportunity_production` | WR/RB/TE | Opportunity percentile | Production percentile | Equality line and `±15` gap bands | Whether recent role and PPR production are aligned |
| `passing_environment` | WR | Team QB passing yards/game | Mean target share | Position medians | Passing environment versus the player's share of it |

`target_air`, `adot_targets`, and `passing_environment` may support TE after the WR version is
accepted. `yards_tds` remains WR-first. RB receives only `opportunity_production` in this
phase. Unsupported chart/position pairs are never approximated.

Definitions:

- Shares are percentages in the API and UI. Negative or greater-than-100 air-yards shares
  are valid and must not be clamped.
- aDOT is `sum(receiving_air_yards) / sum(targets)` over games where both values are present.
  Zero total targets produces null and omits the point from `adot_targets`.
- Team passing yards are the sum of player `passing_yards` for the player's team/game,
  averaged over the same observed games. Label the axis **Team QB passing yards/game** so it
  is not mistaken for sack-adjusted net team passing.
- `opportunity_production` uses the existing within-position percentiles and signal
  thresholds. Do not recompute or reinterpret them in React.
- Medians use the full eligible position population for the selected season, not only the
  current rostered/available view. This keeps axes and reference lines stable as portfolio
  filters change. The tooltip calls them **position medians**.

## Read-model and API contract

Add a DB-only endpoint:

`GET /api/portfolio/opportunity/charts?season=N&view=rostered|available|all&position=WR|RB|TE`

It accepts the same Phase 23 portfolio filters as the Opportunity list. It performs no
network calls and reuses Phase 27 identity, roster-truth, latest-position, and observed-game
rules.

The response is chart-ready so the frontend only renders and formats:

```text
season, view, position, window_games, through_week
source: run_id, fetched_at, stale, state, schema_fingerprint
coverage: population_players, returned_players, current_roster_leagues,
          unknown_roster_leagues
charts[]:
  id, title, x_key, y_key, x_label, y_label, supported_positions
  domain: x_min, x_max, y_min, y_max
  references[]: kind, axis/value or line endpoints, label
  quadrants[]: key, label, x_side, y_side
  point_count, omitted_count, omitted_reasons
points[]:
  espn_player_id, player_name, nfl_team, position, sample_games, through_week
  target_share_pct, air_yards_share_pct, targets_per_game
  receptions_per_game, receiving_yards_per_game, receiving_tds_per_game
  average_depth_of_target, team_passing_yards_per_game
  opportunity_score, production_percentile, opportunity_gap, signal, trend
  mine_leagues, field_leagues, available_leagues, unknown_leagues
warnings[]: code, message, count
```

Implementation rules:

- Refactor one private raw summary builder shared by the table and charts. Do not maintain a
  second copy of rolling-window, mapping, or roster logic.
- Chart values use source precision; table and tooltip formatting remain presentation-only.
- Chart definitions, domains, medians, diagonal/band coordinates, eligibility counts, and
  omission reasons are server-built. React does no analytics math.
- Domain padding is deterministic: 5% of range per side, with a metric-specific minimum
  when all values are equal. Percentile domains are fixed at `0..100`.
- Points are stable-sorted by position, player name, and ESPN ID. One NFL player is one point
  regardless of how many fantasy leagues contain them.
- Keep the response bounded. Return only eligible RB/WR/TE players already present in the
  Phase 27 mapped universe; never include raw weekly source rows.

Selecting a point may lazily call the existing player Opportunity endpoint for its weekly
history. Cache that detail in the page session; never fan out requests for every point.

## Visual-quality contract

### Composition

- Add one chart explorer above the existing table, not five stacked charts.
- Use a single horizontally scrollable tab row for chart choice, the dominant chart, and one
  compact selected-player detail strip. The table remains the accessible detailed view.
- Default to `WR` + `target_air`. Preserve the selected chart and position while switching
  rostered/available/all views when the combination remains supported.
- Desktop chart aspect is approximately `16:9`, capped near 520 px high. Mobile uses a
  taller aspect near `4:3`, never causes document-level horizontal scrolling, and displays
  only the selected label until space permits more.

### Marks, labels, and quadrants

- Render every point at its exact coordinate; never jitter, nudge, or spread data values.
- Use a same-origin NFL team-logo marker with a neutral halo and a team-abbreviation vector
  fallback. Add a small selected ring rather than changing the point's coordinate or size.
- Add a bounded same-origin team-logo route backed by the existing ESPN image proxy and a
  fixed team allowlist. Browser caching prevents repeated downloads; an unavailable logo is
  a normal fallback, not a chart failure.
- Use thin neutral grid lines, readable axis titles and units, restrained reference lines,
  and small quadrant labels inside the plot. Do not use decorative diagonal striping.
- Put the season, three-game window, through-week, and a small **NFL data via nflverse**
  source line inside the chart frame so screenshots retain their context.
- Encode the existing opportunity signal with the marker halo and a compact labeled legend;
  preserve the team logo itself. Pending remains visually distinct from aligned.
- Quadrant language stays descriptive (`High yards / fewer TDs`, `Deep + volume`) and never
  becomes a waiver, lineup, breakout, or regression recommendation.
- Tooltips show full player name, team, both exact displayed axis values, sample games,
  through-week, opportunity signal, and portfolio state counts.

Recharts does not provide reliable automatic scatter-label collision avoidance. Implement a
deterministic presentation-only label layout:

1. Project points into plot coordinates after the final responsive dimensions are known.
2. Group identical or near-identical screen coordinates into a cluster marker without
   changing the underlying values.
3. Prioritize selected, my-roster, available, absolute-gap outlier, then alphabetical labels.
4. Try eight fixed anchor positions and accept the first in-bounds, non-overlapping box.
5. Draw a subtle leader line when the label is displaced; omit a non-priority label when no
   legal position exists. Hover, focus, and selection still reveal it.

The layout must be stable for identical input and dimensions. Disable scatter entrance
animation so labels, exports, reduced-motion behavior, and screenshots cannot desynchronize.

### Interaction and accessibility

- Mouse hover, keyboard focus, and touch selection produce the same selected state.
- A selected marker remains visible after pointer exit. `Escape` clears selection.
- Custom SVG markers expose a concise accessible name. The chart has a descriptive
  `role="img"`; the existing data table remains the complete non-visual alternative.
- Reference lines and signal colors are redundant with labels/line styles. Color alone never
  carries meaning.
- Tooltips remain inside the viewport and are not clipped by the table's scrolling panel.
- Respect light/dark themes and `prefers-reduced-motion`.

### Image export

Add one **PNG** action for the active chart:

- Export at 2× rendered resolution with title, season/through-week, axis labels, references,
  selected chart labels, and `NFL data via nflverse (CC BY 4.0)` attribution.
- Inline same-origin logo bytes before drawing to canvas. If an asset cannot be embedded,
  export the vector abbreviation fallback instead of failing the chart.
- Filename:
  `opportunity-{chart-id}-{position}-{season}-w{through_week}.png`.
- Export is client-only and creates no server file or database row.

## Empty, degraded, and error states

Reuse Phase 27 source errors. Charts never hide a usable table or last-good data.

| Code | User message | Behavior / troubleshooting |
| --- | --- | --- |
| `OPP-CHART-NO-SAMPLE` | Not enough comparable players to draw this chart yet. | Show sample and required fields; expected early season. |
| `OPP-CHART-INCOMPLETE` | Some players are missing a metric required by this chart. | Render valid points; expose bounded omitted counts/reasons. |
| `OPP-CHART-UNSUPPORTED` | This chart is not available for the selected position. | Disable impossible tab combinations; API returns stable 422 if called directly. |
| `OPP-CHART-RENDER` | The chart could not be displayed; the data table is still available. | Keep table and retry action; report viewport and chart ID without player payloads. |
| `OPP-CHART-EXPORT` | The chart image could not be created. | Keep chart usable; retry with vector marker fallbacks. |

Additional edge cases:

- Preseason/no rows: retain `OPP-NOT-PUBLISHED`; do not render empty axes.
- One valid point or fewer than three population points: show `OPP-CHART-NO-SAMPLE`, not a
  misleading median/quadrant.
- Byes/inactive/missing rows: preserve Phase 27 observed-game semantics.
- Present zero-usage games remain zero; missing values remain null.
- All-zero TDs or identical coordinates: expand the domain by the fixed minimum and cluster
  overlapping marks; never invent jitter.
- Traded player: one point using the latest eligible position/team and the same three-game
  sample; tooltip may note multiple teams when present in the window.
- Position change: use only latest-position games and retain the Phase 27 warning.
- Stale source: draw last-good points with a visible stale badge and timestamp.
- Stale ESPN rosters: availability stays unknown; never recolor unknown players as free.
- Logo 404/502, slow image, ad blocker, offline browser: immediate vector fallback.
- Very long names, suffixes, duplicate names, missing team/name, negative shares, >100%
  shares, narrow/mobile viewport, browser zoom, font scaling, and theme switching must not
  clip axes or create document overflow.

## Troubleshooting contract

- Extend `python -m api.opportunity doctor --season N --json` with `charts` containing:
  eligible/omitted counts per chart and position, null/non-finite counts by field, computed
  domains/references, largest identical-coordinate cluster, and source run/schema IDs.
- Add one structured completion log for the chart read model only when explicitly diagnosed;
  ordinary GET/render traffic stays quiet.
- Frontend diagnostic reports contain chart ID, position, viewport dimensions, point count,
  render/export stage, and app version. Never include ESPN cookies, league credentials, raw
  upstream payloads, or the full point array.
- Development builds expose stable `data-testid` hooks for plot, reference lines, markers,
  labels, clusters, tooltip, empty state, and export action.

## Verification

### Backend

- Pure tests for every chart's eligibility, medians, domains, fixed percentile bands, null
  handling, identical values, negative/>100 shares, and deterministic ordering.
- Prove roster-view changes affect returned points but not full-position domains/medians.
- Prove table and chart summaries share the same raw builder and values.
- Endpoint contract tests for filters, unsupported combinations, stale/partial/empty states,
  bounded warnings, and zero network calls on GET.
- Doctor fixtures identify missing aDOT, all-zero TDs, non-finite input, and dense clusters.

### Frontend and visual QA

- Mocked Playwright fixtures for normal, dense/clustered, sparse, missing-metric, stale,
  missing-logo, and render/export-failure states.
- Verify chart switching, position/view persistence, hover/focus/tap parity, `Escape`, lazy
  detail fetch, tooltip values, and PNG filename/2× dimensions.
- At 1440 px and 390 px, assert no document overflow, axes and quadrant labels remain in
  bounds, tooltip stays visible, and accepted desktop labels have no intersecting bounding
  boxes.
- Add deterministic cropped visual baselines for dark/light desktop and mobile. Disable
  animation and network images in the baseline fixture; use vector logo fallbacks.
- Retain the existing Opportunity table, refresh, detail, CSV, master export, reduced-motion,
  request-isolation, and full browser suites.

Quality gates:

```bash
make test
make lint
cd web && npm run lint
cd web && npm run build
cd web && npm run e2e
```

Manual acceptance compares the five chart states with the supplied examples at desktop and
mobile sizes. Phase 29 passes only when labels are more legible, dense points are explorable,
units/provenance are clearer, and failures degrade more safely than the references.

## Delivery order

1. Shared raw summary builder, chart schema/endpoint, exact formulas, diagnostics, and tests.
2. Generic responsive scatter shell, axes/references/quadrants, tooltips, and vector markers.
3. Team-logo proxy/fallback, deterministic labels/clusters, keyboard/touch selection, and
   lazy weekly detail.
4. PNG export, visual baselines, dense/mobile/accessibility tests, documentation, and full
   regression gates.

## Explicit non-goals

- No offensive/defensive EPA per play from player receiving/rushing EPA.
- No 12/21/13 personnel, zone/man win rate, first-read share, catchable-target rate, route
  participation, YPRR, TPRR, FDRR, motion, play action, or injury-risk chart.
- No historical season browser, projection, causality claim, automated recommendation, AI
  narrative, ESPN write, database migration, or change to Phase 27 scores/exports.

Those datasets can be planned separately after Phase 29 proves the chart system and each new
source receives its own licensing, freshness, storage, failure, and troubleshooting contract.

## Research notes

- Recharts supports responsive scatter charts, custom SVG shapes/active shapes, virtual
  Z-axes, and reference lines:
  <https://recharts.github.io/en-US/api/Scatter/> and
  <https://recharts.github.io/en-US/api/ResponsiveContainer/>.
- Recharts does not solve dense scatter-label overlap automatically; the long-running issue
  confirms custom layout or clustered interaction is required:
  <https://github.com/recharts/recharts/issues/903>.
- Labels have historically desynchronized with animation/resize, so Phase 29 disables
  scatter animation and tests resize directly:
  <https://github.com/recharts/recharts/issues/1135>.
