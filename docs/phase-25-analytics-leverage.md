# Phase 25 - Analytics correctness and leverage contract

Phase 25 keeps the Phase 23/24 analytics additive: no schema reset, no ESPN access
change, no React-side analytics math, and no changes to Phase 9-17 metric semantics.
The page now frames exposure against a local field baseline instead of showing only a
portfolio census.

## Correctness fixes

### NFL team concentration split

The old NFL concentration percentage used player-team instances as the numerator and my
team count as the denominator. That made rows such as `LAC 159.6% - 182 / 114 teams`
possible when multiple Chargers appeared on one roster.

The read model now reports two separate metrics:

- `penetration_pct`: teams with at least one player from that NFL team divided by teams
  in scope. This is the displayed bar and is capped at 100%.
- `players_per_team`: player-team instances divided by teams in scope. This is displayed
  as a rate with an `x` suffix, never as a percentage.

Current local DB check: LAC is `87.7%` penetration (`100 / 114` teams) and `1.60x`
players/team (`182 / 114`). A regression test recursively asserts that percentage-style
fields in the analytics endpoint responses do not exceed 100.

### Draft fingerprint axis

The fingerprint chart is now rendered as per-position small multiples with a fixed
0/50/100 Y axis and enough axis width for `100%`. The Playwright Analytics smoke asserts
that the chart renders and includes the `100%` tick.

### Strategy 47.3 finding

The identical one-decimal `47.3` means for Hero RB and Robust RB are genuine rounding,
not a collapsed groupby or fallback aggregate:

| Label | n | Unrounded mean | Std dev | 95% CI |
| --- | ---: | ---: | ---: | ---: |
| Hero RB | 20 | 47.325 | 14.602 | 40.925-53.725 |
| Robust RB | 62 | 47.34838709677419 | 15.826 | 43.409-51.288 |

Those intervals overlap, so the UI reports standard deviation and CI and keeps the
descriptive-not-causal note. The difference is not distinguishable at this sample.

## Leverage definition

`GET /api/portfolio/exposure` still accepts `scope=me|opponents`, but the default `me`
response includes comparison fields on every player row:

- `my_exposure_pct`: my drafted teams rostering the player divided by my drafted teams in
  scope.
- `field_exposure_pct`: opponent teams rostering the player divided by opponent teams in
  scope.
- `leverage_pp`: `my_exposure_pct - field_exposure_pct`, in signed percentage points.

The field denominator is intentionally different from my denominator. In the current DB,
the default comparison is `114` my teams against `1026` opponent teams. "Field" means the
opponents inside my synced ESPN leagues, not the broader ESPN population.

## UI and exports

- The exposure table defaults to combined leverage and sorts by `leverage_pp` descending.
  Header sorting also supports ascending leverage for field-favorite players I do not
  roster.
- Headline cards are computed server-side and returned in `headlines`: highest leverage,
  RB positional capital versus field, most concentrated NFL team by penetration, and
  largest current FFC ADP versus draft-time ADP move among exposed players.
- Positional draft capital and draft fingerprint rows carry field percentages and
  leverage values from the backend.
- ADP position buckets include portfolio median, p25, p75, and mean-percentile markers;
  team capture rows include portfolio-median comparison columns.
- Strategy summaries include unrounded mean, standard deviation, and 95% CI.
- Player expanders, team-capture rows, and strategy classification rows link through to
  league/team detail pages; clicking a strategy donut segment or legend entry filters the
  classification table.
- CSV row fields include the new exposure/leverage and ADP median columns. The master JSON
  includes the full API blocks, and the XLSX workbook adds exposure headline, NFL
  concentration, and positional-spend sheets alongside the existing analytics sheets.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check api tests
cd web && npm run lint
cd web && npm run build
cd web && npm run e2e
```
