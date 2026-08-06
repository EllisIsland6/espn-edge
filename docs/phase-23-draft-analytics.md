# Phase 23 — Portfolio Draft Analytics backend contract

Backend foundation for SPEC §8.5 player exposure, SPEC §2.9 FFC ADP, and SPEC §6.3/§7
draft strategy fingerprints. Phase 23 intentionally stopped before the Analytics page,
Recharts, exports, and Playwright UI work; those shipped in Phase 24.

## What changed
- Added portfolio-scope read endpoints:
  - `GET /api/portfolio/exposure?scope=me|opponents&season=&account_id=&verdict=`
  - `GET /api/portfolio/draft-adp?season=&account_id=&verdict=`
  - `GET /api/portfolio/strategies?season=&account_id=&verdict=`
- Added shared backend portfolio filtering (`season`, `account_id`, Edge Index `verdict`) so
  board and analytics scopes do not drift.
- Added `api/services/exposure.py` for player exposure, positional spend, normalized
  draft-percentile fingerprint, NFL-team concentration, and core/dart split.
- Added `api/services/ffc_adp.py` for deduped Fantasy Football Calculator pulls, 24h TTL,
  snapshot persistence, and deterministic ESPN↔FFC name resolution.
- Added ADP value-capture metrics and deterministic strategy fingerprints in `metrics.py`.
- Added a generated sanity table for the current local DB:
  `docs/phase-23-strategy-sanity.md`.

## What did not change
- No schema change and no `make db-reset`.
- No changes to ESPN access, cookies, sync authentication, or SPEC §1/§2 ESPN rules.
- The existing `82.5` fixtures and outputs for `edge_score`, `edge_index_score`,
  `my_edge_score`, `league_softness_score`, `grade_for`, `verdict_for`, and
  `playoff_odds` remain byte-identical.
- No AI output feeds metrics. Strategy labels are deterministic backend facts.
- No React math and no frontend/network calls to FFC. Page reads are read-only.

## Metric keys added
Per team, `week IS NULL`:
- `draft_value_capture_espn`: mean pick `value_delta`, ESPN = "vs. draft-time ADP".
- `draft_value_capture_ffc`: mean `players.ffc_adp - draft_picks.overall`, FFC =
  "vs. current market ADP".
- `draft_adp_source_disagreement`: mean `abs(adp_at_draft - ffc_adp)`.
- `draft_strategy_<snake_label>`: confidence for selected primary/secondary label.
- `draft_strategy_rank_<snake_label>`: `1.0` primary, `2.0` secondary.
- `draft_strategy_trigger_<snake_label>_<n>`: triggering pick overall numbers.

All `draft_strategy_*` rows are cleared and rewritten on recompute so stale labels cannot
survive a reclassification.

## ADP source contract
ESPN and FFC are kept separate:
- ESPN `draft_picks.adp_at_draft` / `value_delta` is the draft-time ESPN market stamped at
  sync.
- FFC REST ADP is the current market snapshot at `adp_snapshots.pulled_at`.
- No consensus/blended ADP is computed.
- `draft_adp_source_disagreement` is a comparison metric only.

FFC pulls are deduped by `(format, teams, year)`. The current portfolio's 10-team PPR leagues
therefore need one logical request: `ppr?teams=10&year=2026`. FFC's docs say the API supports
scoring format, team count, year, and position parameters, asks for attribution, and says data
updates once daily, so the backend TTL is 24h.

`adp_snapshots` dating makes current-market comparisons queryable going forward. It cannot
reconstruct pre-Phase-23 FFC values for drafts that already happened before the first snapshot.

Post-review FFC verification in the local DB after the Phase 24 resolver correction:
- One exact FFC snapshot exists: source `ffc`, `pulled_at`
  `2026-08-05T01:18:15.724128`, format `ppr`, teams `10`.
- The standard response includes K and D/ST (`20` PK and `23` DEF); position-filtered
  `PK` / `DEF` requests return the same sets, so no extra pulls are needed.
- Eddy Pineiro was a resolver bug: FFC publishes `Eddy Piñeiro`. Unicode accent folding
  now resolves him to FFC id `3097`.
- Portfolio snake drafted-player coverage is `157 / 161` with non-null `ffc_id` and
  `157 / 161` with non-null `ffc_adp` (`97.5%`). FFC pick coverage is
  `1,784 / 1,792`; the eight uncovered picks belong to four distinct players.
- The remaining four are named `not_in_snapshot` exclusions, not resolver failures:
  Blake Grupe (K), Buccaneers D/ST, Jets D/ST, and Raiders D/ST. The current FFC
  response does not list them; resolver failures are `0`.
- `GET /api/portfolio/draft-adp?season=2026` returns 114 teams; 112 non-auction snake
  teams have `draft_value_capture_ffc` and `draft_adp_source_disagreement`, with the 2
  auction teams intentionally null for pick-number ADP math.

## Coverage rules
- Pre-draft leagues are excluded from denominators.
- Player exposure includes auction teams because ownership/exposure is true regardless of
  acquisition method.
- ADP value capture excludes auction teams because bid dollars and nomination order are not
  comparable to snake pick numbers.
- Strategy classification excludes auction teams; no round-equivalent snake thresholds are run
  against `bid_amount`.
- Keeper picks are excluded from ADP means and strategy triggers, and are counted in coverage.
- Null ADP means pending/unavailable, never zero. Missing ESPN/FFC ADP counts are surfaced.

## Strategy thresholds
Round-equivalent = `overall_pick / league_size`.

| Label | Rule |
|---|---|
| Autodraft/Absent | Precedence winner when team autodrafted, all valid picks autodrafted, or no valid non-keeper picks. No secondary. |
| Zero RB | No RB through the end of round-equivalent `5.0`. This is a strict "through end of round 5" reading. |
| Hero RB | Exactly one RB through `5.0`, with the second RB absent or after `5.0`. |
| Robust RB | At least three RBs through `5.0`, or at least two RBs through `2.0`. |
| Elite TE | First TE through `3.0`. |
| Late-Round QB | First QB after `8.0`, or no QB drafted in valid picks. |
| Anchor WR | First pick is WR and at least three WRs through `5.0`. |
| Balanced/BPA | Primary fallback only when no RB-structure label fires. |

Precedence:
1. `Autodraft/Absent` wins outright.
2. Primary axis: Zero RB / Hero RB / Robust RB; if none, Balanced/BPA.
3. Secondary axis: Elite TE / Late-Round QB / Anchor WR.
4. Same-axis tie-break: highest confidence, then earliest triggering pick, then config order.

Strategy/Edge Index comparisons are descriptive, not causal. Strategy is confounded with
draft slot, league, and opponent quality.

## Current sanity table
Generated from the local DB after recomputing Phase 23 metrics:
- Non-pre-draft `is_me` teams: 114
- Qualifying snake teams classified: 112
- Auction teams excluded: 2
- Keeper picks excluded: 0
- Eligible picks missing ESPN draft-time ADP: 0
- Eligible picks missing FFC current market ADP: 38

Trigger-input distribution over the 112 qualifying snake teams:

| Column | Min | P10 | P25 | Median | P75 | P90 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1st RB | 0.10 | 0.11 | 0.38 | 1.00 | 1.60 | 1.79 | 4.30 |
| 2nd RB | 1.10 | 1.20 | 1.60 | 2.05 | 3.52 | 5.70 | 7.20 |
| 3rd RB | 2.10 | 2.52 | 3.10 | 5.55 | 7.05 | 8.50 | 13.30 |
| 1st WR | 0.20 | 0.40 | 0.50 | 2.10 | 3.30 | 4.00 | 5.10 |
| 2nd WR | 1.30 | 2.40 | 2.90 | 4.10 | 4.90 | 5.99 | 7.10 |
| 3rd WR | 2.20 | 3.80 | 4.47 | 5.65 | 7.00 | 7.79 | 9.00 |
| 1st QB | 2.10 | 3.23 | 5.20 | 6.45 | 10.43 | 11.49 | 15.70 |
| 1st TE | 1.90 | 3.83 | 4.60 | 8.70 | 9.90 | 11.58 | 15.70 |

Primary distribution:
- Robust RB: 62 (55.4%)
- Balanced/BPA: 23 (20.5%)
- Hero RB: 20 (17.9%)
- Autodraft/Absent: 7 (6.2%)

Secondary distribution:
- Anchor WR: 31 (27.7%)
- Late-Round QB: 28 (25.0%)
- Elite TE: 8 (7.1%)

Primary x secondary:

| Primary | Anchor WR | Elite TE | Late-Round QB | None |
|---|---:|---:|---:|---:|
| Autodraft/Absent | 0 | 0 | 0 | 7 |
| Balanced/BPA | 15 | 1 | 2 | 5 |
| Hero RB | 16 | 2 | 0 | 2 |
| Robust RB | 0 | 5 | 26 | 31 |

The old thresholds left 19 teams as `Balanced/BPA` despite one RB through `5.0` and a
second RB in `(5.0, 7.0]`. Widening Hero RB to "second RB after `5.0`" closes that gap:
all 19 non-autodraft gap teams now classify as `Hero RB`. Zero RB remains strict; the
current DB has 0 teams with first RB after `5.0` and max first RB is `4.30`. No
structurally impossible primary/secondary pairings appear in the cross-tab; `Hero RB +
Anchor WR` is valid, while `Robust RB + Anchor WR` is absent and should be manually
reviewed if it appears in later data.

Full table and spot checks: `docs/phase-23-strategy-sanity.md`.

## Gates
```bash
.venv/bin/python -m pytest tests/test_phase23_draft_analytics.py -q
.venv/bin/python -m ruff check api tests
```

New Phase 23 test coverage:
- Exposure: `test_exposure_truth_table_hand_computed`.
- ADP metrics: `test_draft_adp_means_exclude_auction_and_keepers`.
- Keeper path: `test_keeper_pick_excluded_from_adp_and_strategy_triggers` proves a
  synthetic keeper is excluded from both ADP means and classifier triggers, while
  remaining counted in both coverage blocks.
- FFC ingestion: `test_ffc_ingestion_dedupes_requests_and_surfaces_unmatched`,
  `test_ffc_apply_snapshot_name_resolution_cases`.
- Strategy classifier: `test_strategy_rules_and_league_size_normalization`,
  `test_strategy_second_rb_between_rounds_five_and_seven_is_hero`,
  `test_strategy_shuffle_stable_and_autodraft_precedence`,
  `test_strategy_recompute_idempotent_and_clears_stale_label`.
- Portfolio endpoints: `test_phase23_portfolio_endpoints`.

Existing `82.5` fixtures in `tests/test_metrics.py` and `tests/test_views.py` still pass
byte-identically across every metric/function named in **What did not change**.
