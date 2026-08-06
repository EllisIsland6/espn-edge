# Phase 26 - Leverage normalization contract

Phase 26 fixes the Phase 25 leverage denominator without a DB schema change, reset, ESPN
access change, or React-side analytics math. Phase 9-17 metric semantics remain unchanged.

## Leverage denominator

Phase 25 compared unlike bases:

- `my_exposure_pct`: my teams rostering a player divided by my drafted teams.
- `field_exposure_pct`: opponent teams rostering a player divided by opponent teams.

In the current portfolio that meant `114` my teams versus `1026` opponent teams. Because a
player can occupy at most one roster per league, the old field percentage was capped at
`114 / 1026 = 11.1%`. The field subtraction was therefore about nine times too small in
10-team leagues: positive leverage was inflated, and the negative field-favorite tail was
compressed.

Phase 26 compares both sides on a drafted-league basis:

- `my_exposure_pct`: drafted leagues where my team rosters the player divided by drafted
  leagues in scope.
- `field_exposure_pct`: drafted leagues where at least one opponent rosters the player
  divided by drafted leagues in scope.
- `leverage_pp`: `my_exposure_pct - field_exposure_pct`, in signed percentage points.

The old opponent-slot intensity remains separate:

- `field_slot_pct`: opponent roster slots with the player divided by opponent teams in
  scope.
- `field_slot_share`: the corresponding slot count, for example `32 / 1026`.

Current local coverage is `114` drafted leagues, `114` my teams, and `1026` field teams.
`Quentin Johnston` now reads `71.9%` my exposure versus `28.1%` field exposure, while
field-ceiling rows such as `Texans D/ST` read `0.0%` versus `100.0%`.

## Exposure views

The API still returns the complete `players` list, plus `views` row counts:

- `rostered`: players I roster, where `my_exposure_pct > 0`. This is the default table view.
- `field_owned`: players where `my_exposure_pct == 0` and `field_exposure_pct > 0`, sorted
  by field exposure descending.
- `all`: the full drafted-player universe.

Exposure CSV accepts `view=rostered|field_owned|all` and carries the same row fields as the
API. The default CSV view remains `all` for compatibility. The XLSX workbook now includes
`Exposure Rostered`, `Exposure Field Owns`, and `Exposure All` sheets.

## Market move signs

Two ADP quantities intentionally use different sign conventions:

- `market_move = current_ffc_adp - draft_time_adp`. A negative number means the player rose
  in the market after the draft because his ADP number got smaller. That helps an already
  rostered position, so the headline card renders neutral directional wording such as
  `rose 57.5 picks since draft` instead of red/green delta coloring.
- `draft_value_capture_ffc = current_ffc_adp - pick_overall`. A positive number means the
  pick was later than the current market price and is value captured.

The largest-market-move headline is gated to players with at least `5.0%` exposure so a
single dart throw does not dominate the portfolio headline.

## Fingerprint domain

Draft fingerprint small multiples now receive all ten `0-10%` through `90-100%` buckets for
every position. Empty leading deciles render as zero, so K and D/ST panels keep the same
x-domain as QB/RB/WR/TE.

## LAC/JAX verification

The identical `87.7%` concentration values are not a grouping artifact. Both NFL teams are
present on `100 / 114` my teams:

| NFL team | Teams with player | Player-team instances | Penetration | Players/team |
| --- | ---: | ---: | ---: | ---: |
| LAC | 100 / 114 | 182 / 114 | 87.7% | 1.60x |
| JAX | 100 / 114 | 161 / 114 | 87.7% | 1.41x |

The matching penetration count is a coincidence; the underlying player-instance counts
are different.

## Verification

```bash
.venv/bin/python -m pytest
cd web && npm run build
cd web && npm run e2e
```
