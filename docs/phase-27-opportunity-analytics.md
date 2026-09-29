# Phase 27 — Opportunity Analytics contract

Implementation status: v1 complete. The adapter, three additive tables, atomic refresh,
deterministic score/read models, typed APIs, Analytics/Status UI, attribution, exports, CLI,
WR evidence layer, and offline failure/Playwright coverage are implemented. A live 2025 dry run on 2026-08-06
validated 19,421 nflverse rows into 5,356 eligible player-games through Week 18, with 808
exact matches and two visible unmatched ESPN players; the dry run did not persist imports.

Add compact, deterministic NFL usage analytics to the portfolio without changing ESPN
access, Edge Index semantics, or AI. nflverse is an enrichment source; ESPN remains the
source of truth for leagues, rosters, availability, scoring, and transactions.

## Outcome and boundaries

- Answer: **Who is earning sustainable volume, whose output trails that volume, and where
  are those players available across my leagues?**
- Cover `RB`, `WR`, and `TE` in v1. Show QB/K/DST/IDP as unsupported, not zero.
- Use `nflreadpy>=0.1.5,<0.2` behind one adapter; no R runtime.
- Download only the nflverse player registry and current-season weekly player stats. Do not
  store raw play-by-play or full source files.
- Add no AI calls or paid dependency. Add `NFL data via nflverse (CC BY 4.0)` to the footer.
- Defer Trade Finder/recap grounding and MyEdge `waiver_capture` until this signal is
  validated in-season. Opportunity is not proof that a waiver add was rostered or started.

## Research-driven constraints

- Weekly player stats update after game days; NFL corrections can land Monday-Wednesday, so
  Thursday's refresh is the cleanest. Always show `fetched_at` and call the latest week
  provisional rather than promising real-time/final data ([update schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html)).
- In-season participation/routes for 2023 onward are not published until after the
  postseason, and post-2024 injury data is unavailable. Do not build current-season route
  or injury features from nflverse in this phase
  ([participation issue](https://github.com/nflverse/nflverse-data/issues/51),
  [injury issue](https://github.com/nflverse/nflreadpy/issues/17)).
- nflreadpy is beta and has open gaps for release metadata, cleaning helpers, and some
  Parquet assets. Keep it replaceable, own team normalization, and provide CSV fallback only
  when the matching Parquet asset returns 404
  ([metadata](https://github.com/nflverse/nflreadpy/issues/14),
  [cleaning helpers](https://github.com/nflverse/nflreadpy/issues/36),
  [Parquet gaps](https://github.com/nflverse/nflreadpy/issues/15)).
- The nflverse player registry includes ESPN and GSIS IDs, but its v2 schema was breaking and
  upstream expects occasional missing/mismatched IDs. Validate columns and surface mapping
  coverage; never silently fall back to fuzzy names
  ([player registry](https://github.com/nflverse/nflverse-players)).
- Weekly stats provide carries, targets, target/air-yards shares, WOPR, EPA, and standard PPR
  points. Raw PBP has had duplicate play IDs; avoiding raw PBP removes that failure mode
  ([stats fields](https://nflfastr.com/reference/nfl_stats_variables.html),
  [duplicate PBP issue](https://github.com/nflverse/nflverse-pbp/issues/9)).

## Storage (new tables only; `create_all` means no DB reset)

`nflverse_player_maps`

- `espn_player_id` PK/FK, nullable `gsis_id`, `status` (`matched|unmatched|ambiguous`),
  `method` (`registry|manual`), `updated_at`.
- Exact registry ESPN ID first; a small explicit `NFLVERSE_ID_OVERRIDES` map second. No fuzzy
  match. D/ST is intentionally unmapped.

`opportunity_weeks`

- Unique `(season, season_type, game_id, gsis_id)`; index `(season, gsis_id, week)`.
- Store only: week/game/player/team/opponent/position, carries, targets, receptions,
  rushing/receiving yards, receiving air yards/TDs, target share, air-yards share, WOPR,
  rushing/receiving EPA, PPR points, derived carry share, and team passing yards.
- Compute carry share before filtering positions: player carries / all team-game carries.
  A zero/missing denominator produces null, never `0` or infinity.

`opportunity_imports`

- `id`, `season`, `state` (`ready|partial|empty|failed|skipped`), attempt/completion times,
  latest week, input/stored/matched/unmatched counts, package version, schema fingerprint,
  error code/message, and bounded `details_json` (first 20 examples; max 2 KB).
- Retain the latest 50 runs per season. Weekly rows are the data cache; nflreadpy's internal
  cache stays off so `force` has one unambiguous meaning.

Imports validate fully in memory, then atomically replace that season's rows. A failed fetch,
parse, validation, or write rolls back and preserves the last good season.

## Import flow

1. `POST /api/opportunity/refresh?season=N&force=false` acquires a process lock. A fresh
   successful import (<24h) returns `skipped`; a second active refresh returns `409`.
2. Load `players/players` and `stats_player/stats_player_week_N` through `NflverseClient`.
   Retry timeouts/429/5xx
   twice with short backoff; do not retry 400/403/404. On Parquet 404 only, try the documented
   CSV asset once.
3. Require and type-check the selected columns; record a sorted column-name fingerprint.
   Filter exactly `season=N`, `season_type=REG`; normalize known team aliases locally.
4. Treat a current-season 404/empty result as expected `empty` only while every scoped ESPN
   league is pre-draft/drafted. Historical or in-season absence is a failure.
5. Resolve ESPN-to-GSIS IDs, derive carry share, deduplicate exact rows, reject conflicting
   duplicate keys, then replace the season and persist the run.
6. Page `GET`s never make network calls. Portfolio **Sync all** may invoke this refresh once
   after its league loop; individual league syncs must not download nflverse repeatedly.

## Deterministic read model

For each mapped player, use the last three **observed regular-season games**, not three
calendar weeks. A missing row is unknown (bye, inactive, or zero involvement), never an
assumed zero. Preserve `sample_games` and `through_week`.

- RB volume index: `100 × (0.60 × mean(carry_share) + 0.40 × mean(target_share))`.
- WR/TE volume index: `100 × (0.60 × mean(target_share) + 0.40 × mean(air_yards_share))`.
- `opportunity_score`: the same weighted mean of within-position midrank percentiles; both
  components, at least 2 observed games, and a position population of at least 10 are
  required. Otherwise null.
- `production_percentile`: within-position percentile of mean nflverse PPR points over the
  same games. It is standard PPR, never presented as the user's ESPN custom score.
- `opportunity_gap = opportunity_score - production_percentile`. With 3 games: `>=15`
  `opportunity_ahead`, `<=-15` `production_ahead`, else `aligned`; otherwise pending.
- `trend`: compare the latest two-game volume index with the prior two observed games;
  `>=+5pp` rising, `<=-5pp` falling, otherwise steady. Require 4 games.

### WR evidence layer

Based on [Fantasy Life's 2026 WR research](https://www.fantasylife.com/articles/fantasy/what-matters-for-wide-receivers-in-2026-fantasy-football),
expose the most predictive current-season fields
alongside the opportunity signal: PPR points, targets, receptions, receiving yards, receiving
TDs, target share, aDOT, and team passing yards, all per observed game over the same
three-game window. Compute aDOT as total receiving air yards divided by total targets; a zero
target denominator is null. Keep yards/game and TD/game side by side so users can inspect TD
regression without inventing a proprietary expected-TD model.

Do not label substitutes as route metrics. YPRR, TPRR, FDRR, route participation, motion
exposure, and two-WR-personnel exposure remain unavailable in-season from this source. The UI
states that limitation and exports use the same nullable server fields. Motion/play-action
charting may be evaluated in a later team-scheme phase, with FTN Data attribution and its own
freshness/coverage contract.

Null components do not become zero and scores are not published with partial components.
Keep unrounded values through computation; round only API fields.

Portfolio roster state is derived at read time from ESPN's latest roster tables:

- Per scoped league: `mine`, `field`, `available`, or `unknown`.
- A league is eligible to say `available` only when its snapshot period matches the league,
  `last_sync_ok is True`, and the player is absent from every roster slot (IR still counts as
  rostered). Otherwise it is `unknown`.
- The list endpoint returns counts only; player detail returns league rows. This keeps the
  common response small.

## API, UI, and exports

- `GET /api/portfolio/opportunity` uses the Phase 23 filters plus
  `view=rostered|available|all` and returns source status, coverage, warnings, and compact
  player rows. Default: `available` in-season, `rostered` otherwise.
- `GET /api/players/{espn_player_id}/opportunity?season=N` returns weekly history and league
  roster states for drilldown.
- `GET /api/opportunity/status?season=N` returns the last run, last good import, age, latest
  week, mapping/schema counts, and last safe error. Status page consumes it without fetching.
- Analytics gets an **Opportunity** section: source/freshness/coverage rail, view/position
  filters, sortable table (score, gap, trend, shares, sample, availability), and a compact
  player drilldown. Empty/preseason/stale/partial/unsupported states are explicit.
- Add Opportunity CSV, master JSON key, and one XLSX sheet using the same server-built rows.
  No React-side calculations.

## Stable high-level errors

| Code | User message | Diagnostic/action |
| --- | --- | --- |
| `OPP-NOT-PUBLISHED` | Opportunity data begins after regular-season games. | Expected preseason 404/empty; no retry banner. |
| `OPP-SOURCE-UNAVAILABLE` | NFL opportunity data is temporarily unavailable; showing the last good import. | Status/host, retry count, elapsed time; retry later. |
| `OPP-SOURCE-FORMAT` | NFL opportunity data changed format; previous data was preserved. | Package version, schema fingerprint, missing/type-mismatched columns. |
| `OPP-ID-COVERAGE` | Some ESPN players could not be linked to nflverse and were excluded. | Counts plus bounded ESPN/GSIS examples; update override only after verification. |
| `OPP-DATA-CONFLICT` | NFL opportunity data contained conflicting player-game rows; previous data was preserved. | Conflicting keys/counts, never full payloads. |
| `OPP-ROSTERS-STALE` | Availability is unknown until the affected ESPN leagues are synced. | League IDs/count; sync ESPN, not nflverse. |
| `OPP-REFRESH-BUSY` | Opportunity refresh is already running. | Return current run ID; disable duplicate UI action. |
| `OPP-SAVE-FAILED` | Opportunity data downloaded but could not be saved; previous data was preserved. | DB exception class, SQLite code, disk space/path; rollback first. |
| `OPP-NO-SAMPLE` | Not enough games to calculate opportunity signals yet. | Expected until thresholds are met. |

Known failures return the envelope/state, not a raw exception or generic 500. Log unexpected
exceptions with stack trace and return one sanitized `OPP-UNEXPECTED` message. Never log ESPN
cookies, headers, source payloads, or arbitrary response bodies.

## Troubleshooting contract

- Every refresh has `run_id`; response, DB row, and structured log share it.
- One completion log: `run_id season state latest_week input/stored mapped/unmatched retries
  schema elapsed_ms last_good_at`. Known warnings get one concise log; avoid per-player spam.
- Add `python -m api.opportunity status|refresh|doctor --season N [--json]`. `doctor` is
  read-only by default and reports Python/package versions, DB/cache paths, source reachability,
  required/actual columns, latest row/key counts, duplicate conflicts, mapping coverage, and
  the last 5 runs. `refresh --dry-run` fetches/validates without DB mutation.
- UI errors show the stable message, run ID, last-good time, and one action (`Retry`, `Sync
  ESPN`, or `View status`). Detailed diagnostics live in Status/doctor, not the main table.

## Edge cases and decisions

- Byes/inactive/missing row: unknown; do not inject zero. A present zero-usage row stays zero.
- Week 1/new rookie: show raw rows; scores/signals remain pending until sample thresholds.
- Stat correction: whole-season atomic replacement removes stale corrected/deleted rows.
- Traded player or multiple games in a week: key by `game_id`; rolling window is by game.
- Duplicate identical rows: dedupe; conflicting duplicates fail the import.
- Position change: use only games matching the latest observed eligible position, expose a
  warning, and never mix position populations in one score.
- Negative/>100% air-yards share can be legitimate; preserve it. Reject non-finite values.
- Team relocation aliases: normalize locally and test; do not wait on nflreadpy helpers.
- Stale roster, partial ESPN sync, IR, and empty slots: never infer free agency from absence.
- Custom ESPN scoring: only availability is league-specific; nflverse PPR output is labeled.
- Postseason: exclude from v1. Historical 2025 is supported for development/backfill.
- Corrupt download, network loss, rate limit, source 5xx, malformed Parquet/CSV, schema drift,
  DB lock/disk full, and concurrent clicks all preserve last-good rows and surface a run ID.

## Delivery order and acceptance

1. Adapter, models, import/status/doctor, offline fixtures and failure tests.
2. Pure rolling math + roster-state read model and typed endpoints.
3. Analytics UI, Status integration, attribution, exports, and mocked Playwright states.
4. Backfill 2025 for a manual sanity check; verify 2026 preseason is expected-empty.

Acceptance requires: idempotent refresh; corrections replace prior rows; no network on GET;
last-good preservation for every known failure; exact-ID coverage is visible; every displayed
number traces to stored weekly rows; stale rosters never claim availability; null/sample rules
hold; source time/attribution display; CLI doctor identifies a forced schema/ID/DB failure;
and all automated tests remain offline.

```bash
make test
make lint
cd web && npm run lint && npm run build && npm run e2e
```

No change to ESPN endpoints/auth/rate limits, `edge_score`, MyEdge, LeagueSoftness, Edge Index,
playoff odds, AI schemas/models, or write behavior against ESPN.
