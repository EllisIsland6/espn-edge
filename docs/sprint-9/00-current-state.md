# Sprint 9, Stage 0 — current state as built

Status: repository review only; no cloud design or production code change. This describes the
working tree as read on 2026-08-09. Where prose and implementation differ, this document follows
the implementation.

## Executive read

ESPN Edge is a localhost-only, single-user application: a Vite development server calls an
unauthenticated FastAPI process, which performs synchronous ESPN syncs and stores the whole
portfolio in one SQLite file. Docker Compose preserves that shape with loopback-only published
ports and a bind-mounted `./data` directory; it is development parity, not a production image
([`docker-compose.yml:1`](../../docker-compose.yml#L1), [`docker-compose.yml:8`](../../docker-compose.yml#L8),
[`Makefile:36`](../../Makefile#L36)). As built, AWS cost is **$0/month**: every component runs on
the developer machine. Anthropic, when enabled, is usage-priced outside AWS.

The most important correction is that there is **no application authentication, authorization,
tenant context, scheduler, or durable job queue**. CORS permits the two localhost Vite origins,
but CORS is a browser policy, not access control. FastAPI registers only routers and a validation
redaction handler; route dependencies create a database session, not a principal
([`api/main.py:29`](../../api/main.py#L29), [`api/main.py:52`](../../api/main.py#L52),
[`api/main.py:68`](../../api/main.py#L68)). This is coherent only while loopback is the security
boundary.

## Everything that holds state

| State | Where it lives | Lifecycle and important properties |
| --- | --- | --- |
| Configuration and master secrets | Plaintext `.env`, loaded once into a process-cached `Settings` object | Includes season, DB path, Fernet key, optional Anthropic key, hosts and TTLs. `.env` is gitignored, but there is no secret manager or rotation metadata ([`api/config.py:18`](../../api/config.py#L18), [`.env.example:1`](../../.env.example#L1)). |
| Primary durable store | One SQLite file, default `data/edge.db`, plus SQLite journal/WAL/SHM files | SQLAlchemy creates tables on startup with `create_all`; foreign keys are enabled per connection. There is no Alembic. Two opportunity columns have a hand-written additive `ALTER TABLE`; other incompatible changes require `make db-reset` ([`api/db.py:22`](../../api/db.py#L22), [`api/db.py:39`](../../api/db.py#L39), [`api/db.py:45`](../../api/db.py#L45), [`README.md:158`](../../README.md#L158)). |
| Identity/league source state | `accounts`, `leagues`, `teams` | Accounts hold labels, plaintext SWIDs and encrypted `espn_s2`; leagues hold ESPN identity/config/lifecycle/current periods/sync diagnostics; teams hold records, logos and plaintext owner SWID arrays ([`api/models.py:32`](../../api/models.py#L32), [`api/models.py:46`](../../api/models.py#L46), [`api/models.py:76`](../../api/models.py#L76)). |
| ESPN facts | `draft_picks`, `matchups`, `lineup_slots`, `current_roster_snapshots`, `current_roster_entries`, `transactions`, `players` | Most league collections are delete-and-replace during sync; teams and players are upserted. `players` is a global mutable row per ESPN player, not season-, league- or tenant-scoped ([`api/models.py:103`](../../api/models.py#L103), [`api/models.py:125`](../../api/models.py#L125), [`api/models.py:147`](../../api/models.py#L147), [`api/models.py:169`](../../api/models.py#L169), [`api/models.py:186`](../../api/models.py#L186), [`api/models.py:221`](../../api/models.py#L221), [`api/models.py:237`](../../api/models.py#L237)). |
| Derived/product state | `metrics`, append-only `metric_snapshots`, `ai_reports` | Metrics are recomputed on sync; snapshots accumulate per clean sync batch; validated Anthropic outputs are cached by fact/model hash and stored as JSON ([`api/models.py:339`](../../api/models.py#L339), [`api/models.py:383`](../../api/models.py#L383), [`api/models.py:411`](../../api/models.py#L411), [`api/services/ai.py:361`](../../api/services/ai.py#L361)). |
| External enrichment state | `adp_snapshots`, `nflverse_player_maps`, `opportunity_weeks`, `opportunity_imports`, plus `players.ffc_*` | FFC snapshots are append-only when its service is called. Opportunity refresh replaces one season atomically and retains the latest 50 import diagnostics per season ([`api/models.py:253`](../../api/models.py#L253), [`api/models.py:267`](../../api/models.py#L267), [`api/models.py:304`](../../api/models.py#L304), [`api/models.py:328`](../../api/models.py#L328)). |
| Raw upstream cache | `raw_cache` table in the same SQLite database, six-hour default TTL | Cache keys include a short hash of SWID, never the SWID itself; payloads are raw ESPN JSON and can contain league/member data. `data/raw_cache/` is created and gitignored but no code writes payload files there ([`api/services/cache.py:1`](../../api/services/cache.py#L1), [`api/services/espn.py:316`](../../api/services/espn.py#L316), [`api/db.py:48`](../../api/db.py#L48)). |
| Browser-persistent preferences | `localStorage`: `espn-edge.theme`, `espn-edge.sidebar-collapsed` | Shared by every person using the same browser origin; no user namespace ([`web/src/lib/theme.ts:3`](../../web/src/lib/theme.ts#L3), [`web/src/components/Layout.tsx:5`](../../web/src/components/Layout.tsx#L5)). |
| Ephemeral process/browser state | Settings LRU, per-client ESPN throttle timestamps, one process-local opportunity lock/run ID, React component state and a page-session opportunity-detail cache | Lost on restart and not coordinated across processes. Exports and chart PNGs are generated in memory/client-side; no server-side export files are retained ([`api/services/espn.py:105`](../../api/services/espn.py#L105), [`api/services/opportunity.py:537`](../../api/services/opportunity.py#L537), [`web/src/components/OpportunityChartExplorer.tsx:714`](../../web/src/components/OpportunityChartExplorer.tsx#L714)). |

## Secrets and protection today

| Secret/sensitive value | Protection actually present | Residual exposure |
| --- | --- | --- |
| `espn_s2` per account | Fernet-encrypted before SQLite storage; never in `AccountOut`; account/reauth validation errors are replaced with a generic response ([`api/crypto.py:20`](../../api/crypto.py#L20), [`api/routers/accounts.py:23`](../../api/routers/accounts.py#L23), [`api/schemas.py:31`](../../api/schemas.py#L31), [`api/main.py:52`](../../api/main.py#L52)). | One global Fernet key sits in plaintext `.env` on the same host. Host or `.env` compromise decrypts every account. Plaintext exists in request/process memory and is posted over loopback HTTP, not TLS. There is no key version, rotation path or per-account blast-radius boundary. |
| `SWID` | Not returned by account APIs and explicit code avoids logging it. | Stored plaintext in `accounts`; all league-owner SWIDs are also plaintext JSON in `teams`. Discovery embeds the account SWID in the upstream URL path and suppresses HTTPX request logging only around that call ([`api/models.py:37`](../../api/models.py#L37), [`api/models.py:89`](../../api/models.py#L89), [`api/services/discovery.py:57`](../../api/services/discovery.py#L57)). Treat it as sensitive even though the current crypto module calls it an opaque identifier. |
| `FERNET_KEY` | Generated by `make setup`; `.env` is gitignored ([`Makefile:19`](../../Makefile#L19), [`.gitignore:10`](../../.gitignore#L10)). | Plaintext local file/environment; possession of DB plus key defeats the at-rest boundary. File permissions are not set or checked by application code. |
| `ANTHROPIC_API_KEY` | Backend-only environment value; absent means AI disabled. Error logging records exception classes, not the key/prompt/output ([`api/services/ai.py:44`](../../api/services/ai.py#L44), [`api/services/ai.py:277`](../../api/services/ai.py#L277)). | Plaintext `.env`/process environment; no scoped secret store or rotation. When invoked, non-cookie league facts and player/team/activity data are sent to Anthropic ([`api/services/ai.py:372`](../../api/services/ai.py#L372)). |
| Verify-only `ESPN_SWID` / `ESPN_S2` | Optional plaintext `.env` values or hidden terminal input; never command-line arguments. The CLI encrypts and persists them to `accounts` if used ([`api/verify.py:49`](../../api/verify.py#L49), [`api/verify.py:68`](../../api/verify.py#L68), [`.env.example:15`](../../.env.example#L15)). | If placed in `.env`, they duplicate account credentials in plaintext. Hidden input avoids shell history but still enters process memory and the shared database. |

Everything else in SQLite—including league membership, owners, rosters, transactions,
reports and raw responses—is plaintext at the application layer. Loopback binding and local
OS access are therefore part of the current security model. The server performs only ESPN
GETs; there is no ESPN login automation, HTML scraping or write path
([`api/services/espn.py:132`](../../api/services/espn.py#L132)).

## Outbound calls and scheduled work

| Destination | Trigger and data sent | Controls as built |
| --- | --- | --- |
| `lm-api-reads.fantasy.espn.com` | Manual per-league sync/verify: stacked settings/team/draft, schedule, current roster/scoreboard, pro schedule, one boxscore per completed week, transactions and player pool; private calls include both cookies ([`api/services/sync.py:95`](../../api/services/sync.py#L95), [`api/services/sync.py:159`](../../api/services/sync.py#L159), [`api/services/sync.py:174`](../../api/services/sync.py#L174), [`api/services/sync.py:217`](../../api/services/sync.py#L217), [`api/services/sync.py:232`](../../api/services/sync.py#L232), [`api/services/sync.py:248`](../../api/services/sync.py#L248)). | HTTPS, 30s timeout, four attempts for network/429/5xx with exponential backoff, raw JSON cache. **Correction:** 1 rps is per `EspnService` instance, not per app; each route sync constructs a new instance, so concurrent requests/workers bypass a shared ceiling ([`api/services/espn.py:94`](../../api/services/espn.py#L94), [`api/services/espn.py:124`](../../api/services/espn.py#L124), [`api/services/sync.py:55`](../../api/services/sync.py#L55)). |
| `fan.api.espn.com` | Manual account discovery; SWID appears in URL and both cookies are sent ([`api/services/discovery.py:24`](../../api/services/discovery.py#L24), [`api/services/discovery.py:60`](../../api/services/discovery.py#L60)). | Best-effort, 30s timeout, no retry/cache; auth failure marks account for reauth. |
| `a.espncdn.com` | On-demand same-origin player portrait/team-logo proxy ([`api/routers/player_images.py:31`](../../api/routers/player_images.py#L31), [`api/routers/player_images.py:44`](../../api/routers/player_images.py#L44)). | Fixed ESPN host/ID or team allowlist, 10s timeout; browser receives cache headers. Separately, ESPN-provided fantasy-team `logo_url` is loaded directly by the browser, so that host—not the backend—receives the browser request ([`web/src/components/TeamIdentity.tsx:53`](../../web/src/components/TeamIdentity.tsx#L53)). |
| Anthropic API | User-triggered AI generation only; sends serialized DB-derived facts, model/system/task/schema configuration, never ESPN cookies ([`api/services/ai.py:60`](../../api/services/ai.py#L60), [`api/services/ai.py:372`](../../api/services/ai.py#L372)). | Disabled without key; schema validation and input-hash cache. No queue, timeout budget or global spend/rate guard in this repo. |
| nflverse via `nflreadpy` / GitHub release assets | Explicit `POST /api/opportunity/refresh` or CLI refresh loads player registry and weekly player stats ([`api/routers/opportunity.py:41`](../../api/routers/opportunity.py#L41), [`api/services/opportunity.py:241`](../../api/services/opportunity.py#L241)). | 24h DB TTL, short bounded retries, CSV fallback, process-local concurrency lock, last-good preservation. GETs are DB-only. |
| Fantasy Football Calculator | `refresh_ffc_adp()` can GET current ADP and persist snapshots ([`api/services/ffc_adp.py:220`](../../api/services/ffc_adp.py#L220), [`api/services/ffc_adp.py:400`](../../api/services/ffc_adp.py#L400)). | **Dormant in the product:** no router, CLI, sync path or startup path calls it; only tests call the refresh service. Current FFC data therefore depends on a prior/manual code invocation. |
| ESPN through `espn-api` | Verify CLI only when `--cross-check` is supplied; it may receive plaintext cookies ([`api/services/cross_check.py:45`](../../api/services/cross_check.py#L45), [`api/verify.py:154`](../../api/verify.py#L154)). | Never app request path or offline tests. |

**Scheduled jobs: none.** `apscheduler` is installed and `SYNC_CRON` is parsed, but there is no
scheduler construction, job registration or lifespan startup beyond `init_db`
([`pyproject.toml:14`](../../pyproject.toml#L14), [`api/config.py:29`](../../api/config.py#L29),
[`api/main.py:18`](../../api/main.py#L18)). The advertised nightly and in-season schedules in
SPEC §5 are not implemented. “Sync all” is a manual, sequential loop in the current browser
tab; reload loses its progress ([`web/src/pages/Manage.tsx:654`](../../web/src/pages/Manage.tsx#L654)).
CI is event-driven on push/PR, not scheduled ([`.github/workflows/ci.yml:3`](../../.github/workflows/ci.yml#L3)).

## Assumptions that break with more than one user — sprint scope

1. **Loopback equals identity.** Exposing the API makes every account, league, sync, AI,
   refresh and export route callable without authentication. CORS does not repair this.
2. **There is one global portfolio.** No model has `user_id`/`tenant_id`; list, analytics,
   status and export endpoints query the global database. IDs are guessable integers.
3. **A league has one custodian globally.** `UNIQUE(espn_league_id, season)` prevents two
   users from separately owning the same league, and adding an existing league can repoint
   its single `account_id` to the caller's account ([`api/models.py:48`](../../api/models.py#L48),
   [`api/routers/leagues.py:65`](../../api/routers/leagues.py#L65)).
4. **An ESPN account is not an app principal.** Account labels need not be unique; there is
   no mapping between an authenticated app user and permitted account/league rows.
5. **Authorization is absent at both object and query level.** A forgotten filter is not the
   failure mode yet—there is no tenant filter to forget and no DB policy to catch it.
6. **Exports are global exfiltration endpoints.** Portfolio CSV/JSON/XLSX and analytics CSVs
   have no ownership boundary ([`api/routers/exports.py:29`](../../api/routers/exports.py#L29)).
7. **One encryption key means one breach domain.** A key leak exposes every stored ESPN
   session. There is no per-user key, envelope encryption, revocation or cryptographic delete.
8. **Raw and derived caches are globally readable.** Raw ESPN JSON, AI reports, snapshots
   and sync diagnostics have no tenant ownership column; cache key hashing prevents key-name
   disclosure, not cross-tenant database access.
9. **Global `players` rows are last-writer-wins.** League-scoped ESPN projection/ADP values
   and FFC mappings are overwritten across seasons/leagues/users. Shared reference data can
   be pooled, but these mutable fields do not currently preserve source context.
10. **The rate limiter is not shared.** It resets with each `EspnService`; simultaneous syncs,
    multiple Uvicorn workers or multiple hosts multiply request rate. The public throttle key
    also merges all public work only within one client instance.
11. **SQLite is the concurrency coordinator.** Long synchronous network syncs run in request
    handlers while mutating one database session. Concurrent writers risk lock contention;
    there is no queue, fair scheduling, backpressure, job identity or cancellation.
12. **In-memory locks are single-process illusions.** Opportunity refresh exclusion and ESPN
    throttle state do not cross process/host boundaries. Force-refresh races can occur with
    multiple workers.
13. **“Sync all” is a UI workflow, not a server job.** It depends on one live browser tab,
    cannot resume, and supplies no durable per-user job status or fairness.
14. **Configuration is process-global.** Season, DB, encryption key, AI key, TTLs and provider
    hosts cannot vary by tenant; settings are LRU-cached.
15. **Status/error surfaces are globally visible.** Health returns the absolute DB path;
    portfolio/status expose cross-account operational state. Sync diagnostics have no run
    history or actor attribution ([`api/routers/health.py:13`](../../api/routers/health.py#L13)).
16. **Privacy lifecycle is absent.** There is no user-scoped export, account/league data
    deletion workflow, retention policy, consent record or audit log. Account deletion is
    blocked while leagues remain linked, and there is no league-delete API
    ([`api/routers/accounts.py:61`](../../api/routers/accounts.py#L61)).
17. **Browser state shares the origin.** Theme/sidebar preferences are harmless but not
    user-scoped; credential forms and all fetched data rely on transient component memory.
18. **Third-party disclosure is one user's decision today.** Any future user whose data is
    included in AI facts needs an explicit product policy; currently a single server-level
    Anthropic key and unauthenticated generation endpoints make that decision globally.
19. **Operational ownership is singular.** There are no quotas, tenant usage/cost attribution,
    per-tenant observability, abuse controls or administrative boundaries.
20. **The frontend hardcodes opportunity seasons `2025` and `2026`.** This violates the
    guardrail against hardcoded seasons and will age immediately; it also prevents a tenant's
    valid historical/current selection outside that pair
    ([`web/src/pages/Analytics.tsx:1807`](../../web/src/pages/Analytics.tsx#L1807)).

## SPEC/documentation drift (code wins)

| Written contract | Implementation |
| --- | --- |
| SPEC §3/§5 says APScheduler runs nightly full sync plus 6-hour in-season jobs. | No scheduler exists. `SYNC_CRON` and the dependency are inert. Sync is manual HTTP/CLI only. |
| SPEC §4's 12-table schema and `models.py`'s “mirrors exactly” claim. | There are 18 tables plus many added columns: sync diagnostics/current periods/projections, current rosters, metric history, and nflverse/opportunity state. The docstring is false ([`api/models.py:1`](../../api/models.py#L1)). |
| SPEC §5 says transactions fall back to the communication feed. | Sync requests only `mTransactions2` + `mPendingTransactions`; no communication endpoint/fallback is implemented ([`api/services/sync.py:232`](../../api/services/sync.py#L232)). |
| SPEC §5 says “idempotent, upsert everything.” | End result is mostly idempotent, but picks, matchups, all historical lineups and transactions are delete-and-reinserted; current roster is delete-and-replace. Teams are upserted and absent teams are not deleted. |
| SPEC §5's pipeline ends with the original league facts and metrics. | Code additionally fetches current rosters/current matchup projections and the pro schedule, and persists momentum snapshots. Opportunity/nflverse refresh is separate and explicit. |
| README/layout says raw cache is in `data/raw_cache/` and the table. | Only the SQLite `raw_cache` table is used; the filesystem directory is merely created. Raw cache payloads do not store cookie headers, although they may store member SWIDs/private league data. |
| README presents current-market FFC analytics as a live feature. | The ingestion service exists, but no product path invokes it. Reads use whatever snapshot/player fields already happen to be present. |
| SPEC §11 says the season must not be hardcoded. | Backend season is centralized, but Opportunity UI hardcodes the 2025/2026 selector and defaults to 2025. |

Guardrails that do match code: ESPN access is backend GET-only, cookie-based, JSON-only; the
workhorse host is centralized; account response schemas omit credentials; AI output is stored
separately and does not feed deterministic metrics; automated backend tests use injected
fixtures/fakes and CI is intended to be offline.

## What a staff engineer would ask about this

1. **“What is the real security boundary?”** Today it is loopback plus control of the local
   OS account—not Fernet and not CORS. Fernet helps only if `edge.db` is stolen without `.env`.
2. **“Can you prove the 1 rps contract under concurrency?”** No. The code proves spacing only
   inside one short-lived client. A shared/distributed limiter and queued sync ownership are
   required before horizontal or even multi-worker execution.
3. **“What is authoritative if the DB is deleted?”** ESPN can rebuild much league data, but
   not all app state: encrypted account configuration must be re-entered; AI reports, metric
   history, opportunity import history, historical FFC snapshots and any data no longer
   returned upstream are lost. The README's “all source data lives on ESPN” is now too broad.

**Whiteboard cold for a senior interview:** trust boundary versus CORS; authentication versus
authorization; tenant-keyed data access; application/envelope encryption and blast radius;
global versus process-local rate limiting; synchronous request work versus durable queues;
idempotency versus delete-and-replace; cache ownership/freshness; SQLite writer concurrency.

**Implementation detail to look up:** exact SQLAlchemy/Alembic syntax, Fernet token format,
nflreadpy asset paths, APScheduler APIs, and the precise ESPN view/filter shapes.

## Stage 0b — measurements

Status: measurements only, taken from the working database on 2026-08-09. No live ESPN request,
Anthropic generation, production-code change, or cloud design was performed. Latency and RSS
were measured on an Apple M2 Pro MacBook Pro (12 cores, 16 GB), Python 3.14.3 and SQLite 3.51.0;
they describe this machine and dataset, not an AWS instance ([E0](#e0)). `ru_maxrss` on macOS is a
process high-water mark, so the memory figures are deliberately whole-process peaks.

### 0b.1 Data size

`data/edge.db` is **727,855,104 bytes**. Neither `edge.db-wal` nor `edge.db-shm` existed at the
measurement instant, so their measured size was **0 bytes**. The database has 177,699 4,096-byte
pages, including 1,004 free pages (4,112,384 bytes). `dbstat` accounts for 723,742,720 live bytes;
the remaining 4,112,384 bytes are exactly the freelist ([E1](#e1)).

The table sizes below include each table's indexes by joining `dbstat.name` through
`sqlite_schema.tbl_name`. The unlisted `sqlite_schema` consumes 16,384 bytes; table rows plus that
schema equal the 723,742,720-byte `dbstat` total ([E2](#e2)).

| Table | Rows | Bytes including indexes | Evidence |
| --- | ---: | ---: | --- |
| `raw_cache` | 576 | 709,222,400 | [E2](#e2) |
| `metric_snapshots` | 18,020 | 4,030,464 | [E2](#e2) |
| `metrics` | 25,635 | 3,350,528 | [E2](#e2) |
| `current_roster_entries` | 18,459 | 2,060,288 | [E2](#e2) |
| `draft_picks` | 18,400 | 1,167,360 | [E2](#e2) |
| `transactions` | 20,384 | 1,040,384 | [E2](#e2) |
| `opportunity_weeks` | 5,356 | 974,848 | [E2](#e2) |
| `leagues` | 115 | 950,272 | [E2](#e2) |
| `matchups` | 8,050 | 368,640 | [E2](#e2) |
| `teams` | 1,150 | 212,992 | [E2](#e2) |
| `players` | 1,026 | 106,496 | [E2](#e2) |
| `nflverse_player_maps` | 810 | 77,824 | [E2](#e2) |
| `ai_reports` | 48 | 73,728 | [E2](#e2) |
| `adp_snapshots` | 1 | 49,152 | [E2](#e2) |
| `current_roster_snapshots` | 115 | 16,384 | [E2](#e2) |
| `opportunity_imports` | 2 | 12,288 | [E2](#e2) |
| `lineup_slots` | 0 | 8,192 | [E2](#e2) |
| `accounts` | 5 | 4,096 | [E2](#e2) |

The correction to the earlier qualitative description is stark: the SQLite `raw_cache` allocation
is **97.44% of the physical DB file** and **97.994% of live `dbstat` bytes**. Its 576 JSON payloads
contain 707,560,025 bytes: largest 4,003,908 bytes, median 244,651 bytes, arithmetic mean
1,228,402.8 bytes. The separately named filesystem directory `data/raw_cache/` contains only its
zero-byte `.gitkeep` and measures 0 KiB ([E3](#e3)). The database, not that directory, is the cache.

#### Observed growth and arithmetic projection

All 115 current leagues are season 2026, so **bytes per additional historical season cannot be
measured from this database** ([E4](#e4)). The correct measurement would be: clone and compact the
DB, import the same representative league for a second real season, compact again, and subtract;
doing that now would require a live historical ESPN call or a stored second-season fixture that
does not exist.

Two different growth numbers answer two different questions:

- A compact clone of the full DB is 724,570,112 bytes. Removing the median-cache-size league and
  all of its cascaded/owned rows from another clone, then `VACUUM`ing, produces 718,286,848 bytes.
  The measured first-season footprint of that representative current league is therefore
  **6,283,264 bytes (5.992 MiB)**. It contained 10 teams, 160 draft picks, 70 matchups, 160 current
  roster entries, 180 transactions, 221 metrics, 160 metric snapshots and five private cache
  payloads ([E5](#e5)).
- Replaying one subsequent full sync of that league from the existing six payloads, committing and
  compacting added **4,096 bytes** and **20 metric-snapshot rows**. Replace/upsert work reused pages;
  the fake provider deliberately bypassed `raw_cache`, so this is the measured marginal DB growth
  of the sync engine, not the byte change of a live cache refresh ([E6](#e6)). A live refresh may
  replace payloads with different-sized JSON; measuring that needs before/after compact clones
  around an authorized live sync.

The explicitly shared reference tables requested for the split—`players`, `nflverse_*`,
`opportunity_*`, plus the globally shared `adp_snapshots`—occupy **1,187,840 bytes** in the compact
clone. Fixed schema/account/shared-cache overhead is another **806,912 bytes**. Using only the
measured 6,283,264-byte representative-league delta, the transparent linear arithmetic is:

| League count | Shared reference | Other fixed | Per-league component | Projected compact total | Evidence |
| ---: | ---: | ---: | ---: | ---: | --- |
| 115 (current) | 1,187,840 B | 806,912 B | 722,575,360 B | 724,570,112 B | [E5](#e5) |
| 1,150 (10×) | 1,187,840 B | 806,912 B | 7,225,753,600 B | 7,227,748,352 B (6.731 GiB) | [E5](#e5) |
| 11,500 (100×) | 1,187,840 B | 806,912 B | 72,257,536,000 B | 72,259,530,752 B (67.297 GiB) | [E5](#e5) |

This is a projection the prompt explicitly requested, not a claim that tenant data, cache payload
shape, shared-player cardinality or history stays linear. What the measurement does establish is
that pooling the named shared tables saves about 1.19 MB today; cache policy dominates storage by
orders of magnitude. `players` is also not clean reference data: its projection/ADP fields are
global last-writer-wins values ([`api/models.py:240`](../../api/models.py#L240)).

### 0b.2 Work profile

#### Representative league sync

An offline replay of the median-cache-size league exercised the production `SyncService`, parsers,
SQLAlchemy writes and metric recomputation against a compact clone. It loaded the exact persisted
payloads but replaced network I/O with an in-process provider ([E6](#e6)).

| Component | Wall time | What the number includes | Evidence |
| --- | ---: | --- | --- |
| Provider replay | 0.013 ms | Six dict lookups; **not ESPN network time** | [E6](#e6) |
| Named parse functions | 2.409 ms | Settings, teams, draft, schedule, roster, transactions, players | [E6](#e6) |
| Named DB helper functions | 122.328 ms | Reset/upsert/delete-replace/stamp operations | [E6](#e6) |
| Metrics recomputation | 50.414 ms | Deterministic metrics plus playoff/snapshot path reached by sync | [E6](#e6) |
| Other service work | 39.153 ms | Control flow, flushes and work outside the instrumented functions | [E6](#e6) |
| `sync_league` wall clock | 214.317 ms | Sum of the above | [E6](#e6) |
| Commit after return | 2.147 ms | Transaction commit | [E6](#e6) |
| End-to-end replay plus commit | 216.464 ms | No external network latency | [E6](#e6) |

The replay made **six provider method calls** and loaded **6,271,487 payload bytes**: five
league/account-scoped payloads totalling 6,151,842 bytes and one 119,645-byte shared pro-schedule
payload ([E6](#e6)). **ESPN network time, live response bytes and a live cache-hit rate were not
measured**, because that requires real ESPN calls. The app records payload and fetch time, not
per-sync request/hit telemetry. The method is to add monotonic HTTPX event-hook timing and byte
counters plus an explicit cache hit/miss counter around
[`EspnService._request_json`](../../api/services/espn.py#L163), run one controlled sync, and remove
or feature-gate the instrumentation.

The persisted timestamps provide only a bounded observation, not a runtime hit ratio: the five
league payloads were written within 4.354 seconds, while the shared pro schedule had been written
122.343 seconds earlier and was still within its six-hour TTL ([E7](#e7)). If those records are one
sync sequence, it was one reusable entry out of six lookups (16.7%); the repo has no event record
that proves that inference. At measurement time every cache row was older than six hours, so the
current cache contained no fresh row ([E7](#e7)).

#### Recompute and read paths

Standalone recomputation was run 21 times against a rollback-only clone: **p50 44.715 ms, worst
56.130 ms**, process RSS high-water **68.766 MiB** ([E8](#e8)). Ordinary endpoints were warmed once
and then measured 11 times each. “Rows scanned” below is SQLite `.scanstats`' sum across plan nodes;
it is not physical disk-page I/O. SQL rows returned is the sum of DBAPI result rows over all SELECTs
in one request ([E9](#e9)).

| Read path | p50 | Worst | SELECTs | SQL rows returned | `.scanstats` rows | Response bytes | Peak RSS | Evidence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Portfolio exposure | 234.947 ms | 239.625 ms | 707 | 23,792 | 192,708 | 600,591 | 143.125 MiB | [E8](#e8), [E9](#e9) |
| Portfolio strategies | 207.145 ms | 219.473 ms | 929 | 9,434 | 2,997,716 | 68,400 | 119.984 MiB | [E8](#e8), [E9](#e9) |
| Opportunity charts, 2025 WR | 194.000 ms | 223.463 ms | 702 | 8,456 | 21,339 | 46,724 | 129.469 MiB | [E8](#e8), [E9](#e9) |
| Portfolio board | 79.317 ms | 93.074 ms | 697 | 1,482 | 3,648 | 101,099 | 118.953 MiB | [E8](#e8), [E9](#e9) |

Those are the three most expensive ordinary analytics reads among the requested surfaces. The
hundreds of SELECTs are real N+1-style amplification: portfolio construction iterates 115 leagues
and repeatedly calls account/team/metric readers ([`api/services/portfolio.py:14`](../../api/services/portfolio.py#L14)).
The strategy endpoint's 2,997,716 plan-node rows make it the largest query-work outlier even though
exposure has the higher wall-clock p50 ([E9](#e9)).

The heaviest measured read overall is the portfolio JSON export: **p50 2.130 s, worst 2.157 s,
14,351,435 response bytes and 435.312 MiB peak process RSS**. The XLSX export measured p50 1.238 s,
worst 1.250 s, 311,616 response bytes and 159.031 MiB peak RSS ([E8](#e8)). During the full sync,
the whole process peaked at **114.422 MiB** ([E6](#e6)); during the heaviest read it peaked at the
JSON export's **435.312 MiB**.

#### CPU-bound request work

The premise that pandas or NumPy currently drive sizing is wrong. `pandas` is installed but no
application module imports it; NumPy is neither declared nor imported. `openpyxl` is the only
heavy tabular library used in a request path ([E10](#e10)).

| CPU/materialization path | Measured workload | p50 / worst | Whole-process peak RSS | Evidence |
| --- | --- | ---: | ---: | --- |
| Sync parsing + Python/ORM transforms | Representative full cached replay | 216.464 / 216.464 ms | 114.422 MiB | [E6](#e6) |
| Full metric recompute | Representative league, 21 rollback runs | 44.715 / 56.130 ms | 68.766 MiB | [E8](#e8) |
| Monte Carlo playoff simulation | 10 teams, 50 remaining games, 10,000 simulations, 11 runs | 250.620 / 253.675 ms | 24.891 MiB | [E11](#e11) |
| Exposure assembly | 115-league portfolio, 11 warm runs | 234.947 / 239.625 ms | 143.125 MiB | [E8](#e8) |
| Draft-ADP assembly | 115-league portfolio, 11 warm runs | 123.886 / 146.537 ms | 123.375 MiB | [E8](#e8) |
| Strategy classification/assembly | 115-league portfolio, 11 warm runs | 207.145 / 219.473 ms | 119.984 MiB | [E8](#e8) |
| Opportunity portfolio assembly | 5,356 stored rows, 11 warm runs | 186.632 / 200.102 ms | 134.390 MiB | [E8](#e8) |
| Opportunity chart assembly | 2025 WR view, 11 warm runs | 194.000 / 223.463 ms | 129.469 MiB | [E8](#e8) |
| Portfolio JSON materialization/encoding | Entire portfolio, 11 warm runs | 2.130 / 2.157 s | 435.312 MiB | [E8](#e8) |
| XLSX/openpyxl materialization | Entire portfolio, 11 warm runs | 1.238 / 1.250 s | 159.031 MiB | [E8](#e8) |

The Monte Carlo case is a controlled fixture shape because all current leagues are drafted and
have no remaining in-season schedule to simulate. The opportunity *refresh/import* CPU and RSS
cannot be measured without a live nflverse load because the 19,421-row source frame is not stored;
only 5,356 processed rows remain ([E2](#e2),
[`api/services/opportunity.py:241`](../../api/services/opportunity.py#L241)). The correct offline
method is to capture one permitted source response as a sanitized fixture, replay
`refresh_opportunity` into a clone, and measure elapsed time and `ru_maxrss`.

### 0b.3 Tenant-retrofit surface

An AST inventory counted calls to `Session.get`, `scalar`, `scalars`, `execute` and legacy
`query` in `api/`. It found **148 database-read call sites: 122 touch tenant-sensitive tables and
26 are shared-reference-only** ([E12](#e12)). “Call site” is static source location, not runtime
query count; one call can join multiple tables. There are no application raw SELECT strings and no
SQLAlchemy relationships outside the models that add a second hidden query syntax. The 122 sites
replace the qualitative Stage 0 prose as the measured retrofit surface.

This is the complete tenant-sensitive inventory. A parenthesized number is the call-site count for
that symbol; line numbers are the source snapshot measured by [E12](#e12).

| File | Sites | Symbol → line(s) → tables |
| --- | ---: | --- |
| [`api/routers/accounts.py`](../../api/routers/accounts.py) | 4 | `list_accounts` → 20 → Account (1); `reauth_account` → 47 → Account (1); `delete_account` → 67,71 → Account, League (2) |
| [`api/routers/ai.py`](../../api/routers/ai.py) | 5 | `_get_league` → 45 → League (1); `_my_team` → 54 → Team (1); `generate_draft_recaps` → 116,124 → DraftPick, Team (2); `_valid_opponent` → 247 → Team (1) |
| [`api/routers/leagues.py`](../../api/routers/leagues.py) | 5 | `list_leagues` → 28 → League (1); `discover` → 35 → Account (1); `add_league` → 60,63 → Account, League (2); `sync_league` → 90 → League (1) |
| [`api/routers/views.py`](../../api/routers/views.py) | 13 | `_get_league` → 125 → League (1); `league_overview` → 134,136 → Account, Team (2); `league_teams` → 163 → Team (1); `league_team_detail` → 177 → Team (1); `league_draft` → 189 → DraftPick, Player (1); `league_matchups` → 218 → Matchup (1); `league_all_play` → 228 → Team (1); `league_my_edge` → 253 → Team (1); `league_edge_index` → 279 → Team (1); `league_softness` → 305 → Team (1); `league_lineup_efficiency` → 330 → Team (1); `league_activity` → 351 → Transaction (1) |
| [`api/services/ai.py`](../../api/services/ai.py) | 3 | `_find_by_hash` → 292 → AiReport (1); `latest` → 301 → AiReport (1); `all_reports` → 309 → AiReport (1) |
| [`api/services/ai_inputs.py`](../../api/services/ai_inputs.py) | 10 | `_picks_for_team` → 74 → DraftPick, Player (1); `league_brief_input` → 128 → Team (1); `advantage_verdict_input` → 163 → Team (1); `_week_transactions` → 273 → Transaction (1); `weekly_recap_input` → 306,309 → Matchup, Team (2); `_newest_lineup_week` → 332 → LineupSlot (1); `_latest_common_lineup_week` → 341 → LineupSlot (1); `_roster_from_lineup` → 370 → LineupSlot, Player (1); `_roster_from_draft` → 390 → DraftPick, Player (1) |
| [`api/services/cache.py`](../../api/services/cache.py) | 2 | `get` → 19 → RawCache (1); `set` → 29 → RawCache (1) |
| [`api/services/cross_check.py`](../../api/services/cross_check.py) | 2 | `from_db` → 34,42 → DraftPick, Team (2) |
| [`api/services/draft_analytics.py`](../../api/services/draft_analytics.py) | 8 | `_league_scope` → 130 → League (1); `_my_teams` → 137 → Team (1); `build_draft_adp` → 222,255 → DraftPick, League, Metric, Player, Team (2); `_trigger_pick_details` → 482 → DraftPick, Player (1); `build_strategies` → 519,561 → DraftPick, Metric, Team (2); `classify_current_team_without_persisting` → 638 → DraftPick, Player (1) |
| [`api/services/exports.py`](../../api/services/exports.py) | 6 | `_teams_sorted` → 100 → Team (1); `portfolio_json` → 170,172,175,178 → DraftPick, League, Matchup, Transaction (4); `portfolio_xlsx` → 319 → League (1) |
| [`api/services/exposure.py`](../../api/services/exposure.py) | 6 | `_drafted_league_ids` → 91 → DraftPick (1); `_teams_in_scope` → 107,111 → DraftPick, Team (2); `_pick_rows` → 123 → DraftPick, League, Player, Team (1); `_team_count_with_auctions` → 155 → League (1); `build_exposure` → 716 → League (1) |
| [`api/services/metrics.py`](../../api/services/metrics.py) | 23 | `_all_play_by_team` → 699 → Matchup; `_lineup_efficiency_by_team` → 852 → LineupSlot, Player; `_transaction_counts_by_team` → 1065,1070 → Transaction (2); `compute_playoff_odds_for_league` → 1158 → Matchup; `recompute_league` → 1208 → Team; `_draft_surplus_by_team` → 1384 → DraftPick; `_draft_adp_capture_by_team` → 1406 → DraftPick, Player; `_draft_strategy_by_team` → 1440 → DraftPick, Player; `_clear_strategy_rows` → 1483 → Metric; `_roster_projection_by_team` → 1526 → DraftPick, Player; `_upsert_or_clear` → 1542 → Metric; `team_edge` → 1578,1586 → Metric (2); `team_components` → 1611 → Metric; `read_all_play` → 1631,1632 → Metric, Team (2); `read_lineup_efficiency` → 1672 → Metric; `read_my_edge` → 1705 → Metric; `read_league_softness` → 1735 → Metric; `read_edge_index` → 1765 → Metric; `team_edge_index` → 1807 → Metric; `read_draft_strategies` → 1841 → Metric (all unmarked entries are one site) |
| [`api/services/momentum.py`](../../api/services/momentum.py) | 3 | `record_metric_snapshots` → 89 → Metric (1); `metric_momentum` → 131 → MetricSnapshot (1); `team_achievements` → 178 → Metric (1) |
| [`api/services/opportunity.py`](../../api/services/opportunity.py) | 6 | `_expected_empty` → 554 → League (1); `_roster_context` → 1025,1031,1036 → CurrentRosterEntry, CurrentRosterSnapshot, Team (3); `_build_opportunity_universe` → 1092 → League (1); `build_player_opportunity` → 1742 → League (1) |
| [`api/services/portfolio.py`](../../api/services/portfolio.py) | 3 | `build_portfolio_rows` → 18,20,21 → Account, League, Team (3) |
| [`api/services/portfolio_filters.py`](../../api/services/portfolio_filters.py) | 1 | `filtered_portfolio_rows` → 45 → League (1) |
| [`api/services/read_models.py`](../../api/services/read_models.py) | 7 | `build_league_out` → 34 → Team (1); `_build_roster_sections` → 99 → CurrentRosterEntry (1); `_team_out` → 150 → Team (1); `_current_matchup` → 165,183 → CurrentRosterEntry, Matchup (2); `build_team_detail` → 212,229 → Account, CurrentRosterSnapshot (2) |
| [`api/services/sync.py`](../../api/services/sync.py) | 11 | `sync_league` → 90,214 → Account, LineupSlot (2); `_reset_team_flags` → 342 → Team; `_upsert_teams` → 357 → Team; `_replace_picks` → 382 → DraftPick; `_stamp_draft_values` → 409 → DraftPick; `_flag_autodrafted_teams` → 433 → Team; `_replace_matchups` → 448 → Matchup; `_replace_current_roster` → 495,501 → CurrentRosterEntry, CurrentRosterSnapshot (2); `_replace_transactions` → 579 → Transaction (all unmarked entries are one site) |
| [`api/verify.py`](../../api/verify.py) | 4 | `_get_or_create_league` → 38 → League (1); `_upsert_account` → 73 → Account (1); `main` → 158,160 → DraftPick, Team (2) |

#### Unowned data-returning endpoints

There are **50 data-returning routes with no authenticated principal and therefore no ownership
predicate**, plus the unowned `DELETE /api/accounts/{account_id}` mutation ([E13](#e13)). Health,
root and image proxy routes may ultimately remain public, but as built they still have no explicit
public-route policy—everything is unauthenticated by default.

| Router | Routes without ownership predicate | Response shape/data exposed |
| --- | --- | --- |
| Accounts (3) | `GET /api/accounts`; `POST /api/accounts`; `POST /api/accounts/{id}/reauth` | `list[AccountOut]` or `AccountOut`: account IDs, labels, status, creation time |
| Leagues (4) | `GET /api/leagues`; `GET /api/leagues/discover/{account_id}`; `POST /api/leagues`; `POST /api/leagues/{id}/sync` | `list[LeagueOut]`, discovered league identities, `LeagueOut`, `SyncSummary` |
| Portfolio and league views (16) | `/api/portfolio`, `/summary`, `/exposure`, `/draft-adp`, `/strategies`; per-league `/overview`, `/teams`, `/teams/{team_id}`, `/draft`, `/matchups`, `/all-play`, `/my-edge`, `/edge-index`, `/league-softness`, `/lineup-efficiency`, `/activity` | Global portfolio rows/aggregates and league/team/draft/matchup/metric/transaction schemas |
| Exports (7) | `/api/exports/portfolio.csv`, `.json`, `.xlsx`, `/exposure.csv`, `/draft-adp.csv`, `/strategies.csv`, `/opportunity.csv` | Whole-portfolio CSV/JSON/XLSX bytes; the JSON response measured 14,351,435 bytes ([E8](#e8)) |
| Opportunity (5) | `GET /api/opportunity/status`; `POST /api/opportunity/refresh`; `GET /api/portfolio/opportunity`; `GET /api/portfolio/opportunity/charts`; `GET /api/portfolio/opportunity/players/{espn_player_id}` | Import status/results, portfolio/player opportunity data and chart datasets |
| AI (11) | `GET /api/ai/status`; GET+POST for draft recaps, league brief, advantage verdict, weekly recap and trade finder | Model configuration plus stored/generated AI report envelopes/lists |
| Player images (2) | Player portrait and allowlisted NFL team-logo proxies | Image bytes and upstream cache metadata |
| Health (1) | `GET /api/health` | Season, absolute DB path and all table row counts |
| Root (1) | `GET /` | App/docs/health links |

#### Existing-league repoint is a present vulnerability

`POST /api/leagues` looks up the globally unique `(espn_league_id, season)` row and, when the
request includes `account_id`, unconditionally assigns that account and sets the league private
([`api/routers/leagues.py:60`](../../api/routers/leagues.py#L60)). With five accounts already in the
DB ([E2](#e2)), this is a present-tense cross-account takeover/data-integrity primitive, not a
future multi-tenancy concern.

**Judgment call: fix before the tenancy phase, in the next production-code change.** A repeat add
may return the existing row only when ownership is unchanged; a differing non-null account must be
rejected (for example, conflict) until an authenticated, authorized transfer operation exists.
The trade-off is losing a convenient implicit “repoint” shortcut. The accepted failure mode is
that a legitimate local reassociation needs a deliberate administrative path. Cost is development
time only and $0/month. This design-only stage does not change the route.

### 0b.4 SQLite → PostgreSQL delta: observed evidence

This is a mechanical type-mapping inventory, not a database architecture decision. Every populated
column was grouped by `PRAGMA table_info` and its actual SQLite `typeof()` values; no populated
column had mixed non-null storage classes ([E14](#e14)).

| Column(s) | Current declared / actual SQLite storage | Mechanical PostgreSQL target | Migration hazard evidenced here |
| --- | --- | --- | --- |
| Surrogate `id` on accounts, leagues, teams, picks, matchups, lineup, roster snapshots/entries, transactions, opportunity weeks, ADP snapshots, metrics/snapshots and AI reports | `INTEGER PRIMARY KEY`, alias of rowid; schema contains no `AUTOINCREMENT` keyword | `BIGINT GENERATED BY DEFAULT AS IDENTITY` | Preserve existing IDs, load parents before children, then set each identity sequence above `MAX(id)`. Delete/reinsert has driven maxima far above row counts—for example 18,459 roster entries have max ID 144,527 and 20,384 transactions have max ID 158,708 ([E15](#e15)). SQLite may reuse a deleted maximum rowid; PostgreSQL sequences do not. |
| `players.espn_player_id`, `nflverse_player_maps.espn_player_id`; all other `espn_*_id`, FK IDs and pseudo-IDs | `INTEGER` / integer | `BIGINT` for external and surrogate references; corresponding FK type must match | Current values fit, but external ID width is not constrained by this sample. `leagues.my_team_id`, draft/lineup/current-roster player IDs and transaction player IDs are integers without declared FKs; PostgreSQL will not make them referentially safe by itself ([E16](#e16)). |
| Seasons, weeks, periods, standings, counts, slots, bids, round fields | `INTEGER` / integer | `INTEGER` | No mixed storage observed. Add only domain checks that reflect real contracts; current schema has none. |
| Points, shares, EPA, ADP, projections and metric values | `FLOAT` / real when populated | `DOUBLE PRECISION` | PostgreSQL rejects malformed text that SQLite affinity might accept, but the current populated DB contains no mixed storage class. NaN handling must follow the existing opportunity validation, not implicit DB coercion. |
| All `VARCHAR` business fields, `raw_cache.key`, `opportunity_imports.id` | `VARCHAR` / text | `TEXT`; keep the latter two as text PKs | No model declares a length, enum or collation. Do not invent `VARCHAR(n)` during a mechanical migration. Case/collation behavior must be selected explicitly. |
| `accounts.created_at`; `leagues.last_synced_at`; roster `synced_at`/`kickoff_at`; transaction `executed_at`; player/map `updated_at`; opportunity import `started_at`/`completed_at`; ADP `pulled_at`; metric `computed_at`/snapshot `recorded_at`; AI `created_at`; cache `fetched_at` | `DATETIME`; every one of 14 populated datetime columns is SQLite text such as `YYYY-MM-DD HH:MM:SS.ffffff`; all 85,125 sampled non-null values deserialize with `tzinfo is None` | `TIMESTAMPTZ` with SQLAlchemy `DateTime(timezone=True)` | Writers call `datetime.now(UTC)`, but SQLite strips the offset. Import must interpret existing naive text **as UTC**, not server local time. Straight `DateTime` would compile to PostgreSQL timestamp without time zone and preserve the bug ([E17](#e17)). |
| `leagues.scoring_json`, `leagues.lineup_slots_json`, `teams.owner_swids_json`, `opportunity_imports.details_json`, `adp_snapshots.payload_json`, `ai_reports.content_json`, `raw_cache.payload_json` | Declared SQLAlchemy/SQLite `JSON`, physically text; all 2,007 non-null values pass `json_valid()` | `JSONB` | Use explicit `USING column::jsonb` and reject/repair invalid values before load (none observed). JSONB normalizes whitespace/key order and changes textual equality/serialization size. Cache payload TOAST behavior will differ. |
| `draft_picks.keeper/autodraft`, `leagues.is_public/last_sync_ok`, `lineup_slots.is_starter`, `matchups.is_playoff`, `teams.is_me/autodrafted` | `BOOLEAN`; populated values are integer 0/1; `lineup_slots` is empty | native `BOOLEAN` | Cast explicitly and validate only 0/1 before load; observed populated rows are valid. Do not rely on truthiness of other integers. |

The **naive/aware timestamp trap is already encoded in tests**. `_now()` creates aware UTC values,
but `test_same_period_syncs_append_but_momentum_uses_latest_event` explicitly expects SQLite to
return `t0.replace(tzinfo=None)` ([`tests/test_momentum.py:42`](../../tests/test_momentum.py#L42)).
Four freshness paths defensively attach/convert UTC before subtraction:
[`api/services/espn.py:82`](../../api/services/espn.py#L82),
[`api/services/ffc_adp.py:213`](../../api/services/ffc_adp.py#L213),
[`api/services/draft_analytics.py:146`](../../api/services/draft_analytics.py#L146), and
[`api/services/opportunity.py:282`](../../api/services/opportunity.py#L282). Other datetime uses
are database ordering or serialization. A PostgreSQL migration that returns aware datetimes will
change the momentum test and any code assuming SQLite's stripped values; a migration to timestamp
without time zone would keep the ambiguity instead of fixing it ([E17](#e17)).

JSONB produces a concrete query opportunity in only one current place: AI weekly/opponent lookups
load all reports and inspect `content_json[key]` in Python
([`api/services/ai.py:317`](../../api/services/ai.py#L317)). A JSONB predicate/index could replace
that. No current SQL query filters the other JSON columns, so JSONB does not make those reads faster
by itself ([E18](#e18)). Code receiving SQLAlchemy dictionaries should continue to work; code that
expects byte-for-byte JSON text would not, but no such application query was found.

#### Keys, unique constraints and indexes

The current schema has **20 declared foreign keys**; `PRAGMA foreign_key_check` returned no rows,
so the current DB has no violation of those declared constraints ([E16](#e16)). PostgreSQL's
always-on enforcement therefore does not expose a current orphan, but it changes operations:
bulk copy must respect dependency order; account deletion remains restricted by `leagues.account_id`;
and the 19 declared cascade edges must be present before delete behavior matches. Undeclared
pseudo-FKs remain unenforced. The per-connection event in
[`api/db.py:31`](../../api/db.py#L31) is also unconditional on dialect; against psycopg it would try
to execute `PRAGMA foreign_keys=ON` and fail at connect time.

The nine named UNIQUE constraints are:

- `uq_league_season(espn_league_id, season)`;
- `uq_team_league_espn(league_id, espn_team_id)`;
- `uq_pick_league_overall(league_id, overall)`;
- `uq_matchup(league_id, week, home_team_id, away_team_id)`;
- `uq_lineup(league_id, week, team_id, espn_player_id, slot)`;
- `uq_current_roster_snapshot_league(league_id)`;
- `uq_current_roster_slot(snapshot_id, team_id, lineup_slot_id, slot_index)`;
- `uq_opportunity_player_game(season, season_type, game_id, gsis_id)`; and
- `uq_metric_snapshot_batch(league_id, batch_id, team_id, key)`
  ([E19](#e19)).

Regular indexes are `ix_metric_snapshot_lookup`, `ix_nflverse_player_maps_gsis_id`,
`ix_opportunity_import_season_time` and `ix_opportunity_player_week`. Metrics additionally depend
on four partial unique indexes for the null/non-null `(team_id, week)` shapes ([E19](#e19)). This is
the most serious measured DDL portability failure: the model supplies only `sqlite_where`. Compiling
the current model through SQLAlchemy's PostgreSQL dialect produced four **unconditional** unique
indexes—no `WHERE` clause at all ([E20](#e20)). That would make valid league, league-week, team and
team-week metrics mutually conflict. PostgreSQL predicates must be declared explicitly; a generated
baseline from the model as it stands is wrong.

#### Hand migration and startup race

`init_db()` first runs `Base.metadata.create_all`, then conditionally issues two hand-written
`ALTER TABLE opportunity_weeks ADD COLUMN` statements for `receiving_tds` and
`team_passing_yards` ([`api/db.py:45`](../../api/db.py#L45)). Both columns already exist in this DB
([E14](#e14)). An Alembic baseline generated from the final model must include them in the baseline;
an existing database is stamped at that baseline only after schema comparison, while a new
PostgreSQL database creates them once. Replaying the SQLite additive statements after that would be
a duplicate-column failure. There is no migration version table today.

`create_all(checkfirst=True)` is not a distributed migration lock. In a measured race on fresh
SQLite files, four simultaneous starters were launched in each of 20 runs with the normal timeout;
**18 of 20 runs had at least one `table ... already exists` startup failure** ([E21](#e21)). Two
instances both inspect absence before either finishes DDL. PostgreSQL transactional DDL changes the
error/rollback behavior, not the absence of coordination. Startup `create_all` is therefore neither
a migration system nor safe simultaneous bootstrap.

Finally, source search found no application `.like()`/`.ilike()`, `LIKE`, `strftime`,
`json_extract`, SQLite `ON CONFLICT`, or implicit string/number comparison. The SQLite-specific
surfaces actually present are the FK PRAGMA, `sqlite_where` predicates, the additive ALTER helper
and opportunity diagnostics that read `sqlite_errorcode` ([E18](#e18)). SQLite's default
case-insensitive ASCII `LIKE` is not a current application dependency. That is evidence from the
current source, not proof that future or third-party SQL is portable.

### 0b.5 Test-suite portability

Pytest collected **235 tests**. Instrumenting SQLAlchemy's `Engine.before_cursor_execute` during a
full passing run showed **139 tests (59.1%) actually execute SQL**, all against the temporary
file-backed SQLite database selected in `tests/conftest.py`; **0 use an in-memory database and 0
execute PostgreSQL** ([E22](#e22), [`tests/conftest.py:16`](../../tests/conftest.py#L16)). The suite
passed in **8.25 seconds wall clock** locally. Twenty-five test functions directly request the four
ESPN JSON fixtures from `conftest.py`; another six `test_real_fixtures.py` tests load fixture files
directly, while module/custom fixtures cause additional tests to consume the same files indirectly
([E23](#e23)).

The SQLite binding is deeper than a URL swap:

- `DB_PATH` is set before any `api` import; `api/db.py` builds a SQLite URL, passes
  `check_same_thread=False`, and installs an unconditional SQLite PRAGMA event
  ([`tests/conftest.py:16`](../../tests/conftest.py#L16),
  [`api/db.py:22`](../../api/db.py#L22)).
- `db_session` and module fixtures repeatedly call `init_db`, `drop_all` and `create_all` rather
  than applying migrations ([`tests/conftest.py:51`](../../tests/conftest.py#L51)).
- The suite depends on SQLite partial indexes, rowid/naive-datetime behavior and
  `sqlite_errorcode`. Offline ESPN fixtures themselves are portable; the database harness is not.

Running a meaningful PostgreSQL lane in CI requires a PostgreSQL service container, a psycopg
driver, dialect-aware engine options/events, a `DATABASE_URL`-style test configuration, and two
distinct test modes: transaction-isolated application tests and a migration test that applies the
Alembic baseline/upgrades from an empty database. Keeping the fast SQLite lane is useful, but it
cannot be the portability oracle.

The **additional PostgreSQL CI minutes cannot be measured yet** because no PostgreSQL lane,
driver or migration exists. Estimating it would violate this stage's rule. As a baseline, the last
10 successful whole GitHub Actions workflows took 56–127 seconds, with p50 **78 seconds**
([E24](#e24)); those workflows run backend and frontend jobs in parallel, so that is elapsed
workflow time, not PostgreSQL job CPU-minutes. The measurement method is to add the service/matrix,
run 10 uncached and 10 cache-warm CI executions, and report the PostgreSQL job's billed duration
minus the existing backend job duration.

Every current test can remain green while a PostgreSQL-only fault exists because PostgreSQL is
never opened. The most dangerously reassuring named tests are:

| Passing test today | PostgreSQL failure it does not detect |
| --- | --- |
| `test_metric_identity_unique_for_every_null_shape` and `test_metric_distinct_shapes_coexist` | They prove the SQLite partial indexes. They do not compile PostgreSQL DDL, which [E20](#e20) shows loses the predicates and makes the four valid shapes conflict. |
| `test_foreign_keys_are_enforced` | It proves the per-connection PRAGMA, not PostgreSQL connection setup, copy order, deferred constraints, or undeclared pseudo-FKs. The unconditional PRAGMA would itself break a psycopg connection. |
| `test_same_period_syncs_append_but_momentum_uses_latest_event` | It explicitly blesses SQLite's tzinfo stripping; it does not exercise aware `TIMESTAMPTZ` results. |
| `test_sync_is_idempotent` | It covers delete/reinsert on one SQLite writer, not PostgreSQL identity-sequence repair, concurrent transactions or lock semantics. |
| `test_league_delete_cascades_history_before_id_reuse` | It manually reuses an old integer ID; it does not prove PostgreSQL sequence state after imported explicit IDs. |
| `test_refresh_is_idempotent_correctable_and_preserves_last_good` | It validates the service transaction on SQLite, not PostgreSQL isolation/locking or the SQLite-specific diagnostic branch. |
| `test_health_returns_season_and_db_path` | It asserts the configured path ends in `.db`, so it tests the local deployment contract rather than database portability. |

No test exercises the two-column additive migration from an old schema or an Alembic baseline;
there is no Alembic code to exercise.

### 0b.6 AI cost and abuse surface

The DB contains **48 reports: 43 draft recaps across four leagues, three league briefs across three
leagues, and two advantage verdicts across two leagues**. Every stored report uses
`claude-sonnet-5`; there are no stored weekly recaps or trade-finder reports ([E25](#e25)).

The application stores `input_hash`, model, output JSON and creation time, but **does not store the
Anthropic response usage object, input tokens, output tokens, request count, cache-hit count or
dollars** ([`api/models.py:411`](../../api/models.py#L411),
[`api/services/ai.py:60`](../../api/services/ai.py#L60)). Exact historical tokens and dollars per
report therefore cannot be measured from this system. I also did not send private league facts to
Anthropic merely to count tokens. Anthropic's token-count endpoint is free but transmits the prompt;
using it requires explicit authorization for that disclosure. The safe implementation method is to
persist the `response.usage` fields and calculated cost at generation time, and use the token-count
endpoint on sanitized fixtures for pre-deployment budgets.

What can be measured locally is the exact current prompt/output byte surface. Current facts were
reconstructed through the same `ai_inputs` functions, and prompts through the same task/system
templates. The system prompt is 348 characters ([E26](#e26)).

| Kind | Model | Samples | Current user-prompt bytes min / p50 / max | Stored output JSON bytes min / mean / max | Exact token/$ measurement |
| --- | --- | ---: | ---: | ---: | --- |
| Draft recap | Sonnet 5 | 43 stored rows | 2,522 / 2,557 / 2,604 | 870 / 1,042.4 / 1,218 | Not recoverable; usage was not stored |
| League brief | Sonnet 5 | 3 stored rows | 3,363 / 3,369 / 3,384 | 1,485 / 1,635.3 / 1,752 | Not recoverable; usage was not stored |
| Advantage verdict | Sonnet 5 | 2 stored rows | 1,706 / 1,714.5 / 1,723 | 1,380 / 1,407.5 / 1,435 | Not recoverable; usage was not stored |
| Weekly recap, representative league/week | Haiku 4.5 | 1 current constructed prompt | 20,221 / 20,221 / 20,221 | No stored output | Requires authorized token-count or real generation |
| Trade finder, representative opponent | Sonnet 5 | 1 current constructed prompt | 7,463 / 7,463 / 7,463 | No stored output | Requires authorized token-count or real generation |

As verified on 2026-08-09, Anthropic's introductory Sonnet 5 price through 2026-08-31 is
**$2/M input tokens and $10/M output tokens**; standard pricing is $3/$15 afterward. Haiku 4.5 is
**$1/M input and $5/M output** ([Anthropic Sonnet 5 pricing](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5),
[Anthropic model overview](https://platform.claude.com/docs/en/about-claude/models/overview)). The
code caps every generation at **2,048 output tokens**
([`api/ai_config.py:22`](../../api/ai_config.py#L22)). Thus the current-price formulas—not measured
bills—are:

- Sonnet report: `$0.000002 × input_tokens + $0.000010 × output_tokens`; maximum output component
  is **$0.02048**.
- Haiku report: `$0.000001 × input_tokens + $0.000005 × output_tokens`; maximum output component
  is **$0.01024**.

All 48 persisted hashes are unique, but hits leave no row and requests are not counted, so the
**observed historical cache-hit rate is unknowable**. Recomputing each report's hash from current
facts found **0 matches out of 48** ([E26](#e26)); this is a current cache-eligibility measurement,
not a historical hit rate. If those report endpoints are invoked now without `force`, all 48
current prompts miss. A second invocation with unchanged facts would hit.

For the requested 1/10/50-user monthly view, the only defensible measured usage pattern is one copy
of the current database's **48 Sonnet generations per user per month** (43 draft + 3 brief + 2
verdict), all misses, no weekly/trade calls ([E25](#e25)). Because token usage is absent, a total
dollar estimate would be invented. The code-derived **maximum output component** is:

| Users with that monthly pattern | Model calls | Maximum output charge at current price | Input charge |
| ---: | ---: | ---: | --- |
| 1 | 48 | $0.98304 | Unknown until input tokens are counted |
| 10 | 480 | $9.83040 | Unknown until input tokens are counted |
| 50 | 2,400 | $49.15200 | Unknown until input tokens are counted |

This deliberately overstates observed output because stored JSON is far below the 2,048-token cap,
while omitting input prevents it from being mislabeled as a total. It is a budget ceiling component,
not a per-tenant spend estimate.

#### Wallet-drain path

There is no application authentication, rate limit, concurrency cap, tenant quota, daily budget or
provider circuit breaker on generation routes. `force=true` bypasses the input-hash cache. One
forced draft-recap HTTP request on a current 10-team league can make **10 sequential Sonnet calls**
([`api/routers/ai.py:106`](../../api/routers/ai.py#L106), [E5](#e5)), for a maximum current-price
output component of **$0.20480 per HTTP request**, plus unknown input.

The unbounded worst case in **dollars per hour has no finite app-level answer**: concurrent forced
requests are not bounded in this repo. Actual hourly spend is eventually bounded by the account's
Anthropic usage tier/rate limits and billing controls, neither of which is stored or observable here.
The measurable formula is:

`forced draft HTTP requests/hour × team count ×
($0.000002 × input tokens + $0.000010 × output tokens)`.

Producing a numeric $/hour would require either reading the account's current limits and combining
them with token-counted sanitized prompts, or a controlled load test that incurs real Anthropic
spend. Neither was performed.

### 0b.7 Baseline operations: the blunt version

**Current backup strategy: none.** Repo/source search found no DB backup, dump, snapshot, copy,
restore, Litestream or replication path. `data/` is gitignored. `tmutil destinationinfo` reports
“No destinations configured,” so this laptop does not have a Time Machine destination either
([E27](#e27)).

**Actual RPO today: there is no recovery point.** It is not “24 hours” or “since last sync”; no
recoverable copy was found. A laptop/disk loss can erase all application-only history since the
oldest stored account/snapshot on 2026-07-30 ([E27](#e27)).

A total disk failure permanently loses at least the only stored copies of:

- **48 AI reports** and their current cached narratives;
- **18,020 metric snapshots** and their trend/momentum history;
- **two opportunity import records**, including exact source version/fingerprint, retry/error and
  match diagnostics; the 5,356 processed opportunity rows may be re-downloadable today, but that
  exact import history is not reconstructable;
- **one FFC ADP snapshot** and the historical market response captured on 2026-08-05;
- five account records and their encrypted session custody/configuration (credentials can be
  re-entered only if the user still has valid sessions);
- the current 576-row raw ESPN cache, sync diagnostics/current-roster snapshots, and any historical
  roster/transaction fact the undocumented upstream no longer returns after the loss
  ([E2](#e2), [E25](#e25), [E27](#e27)).

Players, current league settings/drafts/schedules and nflverse data may be refetched **only while
their respective upstreams still expose compatible data**. AI outputs, metric history, import
diagnostics and the exact FFC snapshot are generated/captured locally and do not live on ESPN.

README's claim that “all source data lives on ESPN” is no longer true. The accurate replacement is:

> Most currently served ESPN league facts can be re-synced while the unofficial upstream still
> exposes them; app-generated reports and metric history, enrichment snapshots/import diagnostics,
> credentials/configuration, and any upstream-retired facts require a separate backup of `edge.db`
> and the secrets needed to use it.

### What a staff engineer would ask about these measurements

1. **“How can a first league cost 6,283,264 bytes while a later full sync adds only 4,096?”** The
   first number removes the league's five multi-megabyte raw payloads and all owned rows from a
   compact clone. The second replay replaces/upserts existing facts, bypasses cache writes and adds
   only 20 snapshot rows. The missing number is live cache-refresh delta; it cannot be obtained
   without an authorized ESPN call or a second response fixture.
2. **“Should I size cloud compute from the 435.312 MiB export peak?”** It is the best current
   measured high-water and cannot be ignored, but it is one M2/macOS/Python 3.14 process and 11 warm
   runs. Before buying compute, repeat the same 14,351,435-byte export under the intended Linux
   container/cgroup and test concurrency. The accepted uncertainty is allocator/runtime variance,
   not whether the export materializes a large object—it demonstrably does.
3. **“Why are there no precise AI dollars if this is a cost review?”** Because the code discarded
   the only authoritative usage fields, cache hits are invisible, and counting current private
   prompts would disclose them to Anthropic. The honest numbers are prompt/output bytes, model
   prices, output ceilings and 0/48 current hash eligibility. Exact spend requires usage capture;
   a character-to-token guess would violate the measurement rule.

**Whiteboard cold for a senior interview:** physical versus logical DB size; first-load versus
marginal growth; benchmark method and environmental validity; latency decomposition; RSS high-water
versus incremental allocation; N+1 query amplification; static query-surface inventory; PostgreSQL
identity/FK/partial-index semantics; naive timestamp versus `TIMESTAMPTZ`; cache observability and
abuse-budget formulas; RPO as a recoverable point rather than a policy claim.

**Implementation detail to look up:** SQLite `dbstat` columns, `.scanstats` interpretation,
SQLAlchemy dialect-specific index syntax, exact psycopg/Alembic fixture setup, GitHub Actions service
container YAML, Anthropic token-count/usage response fields, and platform-specific RSS units.

### Measurement evidence ledger

<a id="e0"></a>**E0 — host/runtime.** `system_profiler SPHardwareDataType`,
`.venv/bin/python --version`, `sqlite3 --version`. Only model/chip/core/memory fields were retained;
serial identifiers were not copied into this document.

<a id="e1"></a>**E1 — file/pages.** `stat -f '%N %z' data/edge.db data/edge.db-wal
data/edge.db-shm` (missing sidecars recorded as absent) and:

```sql
PRAGMA page_size;
PRAGMA page_count;
PRAGMA freelist_count;
SELECT SUM(pgsize) FROM dbstat;
```

<a id="e2"></a>**E2 — rows/table bytes.** For all 18 model tables, `SELECT count(*)`; sizes used:

```sql
SELECT COALESCE(s.tbl_name,d.name) AS table_name, SUM(d.pgsize) AS bytes
FROM dbstat d LEFT JOIN sqlite_schema s ON s.name=d.name
GROUP BY COALESCE(s.tbl_name,d.name);
```

Opportunity source/stored counts were checked with `SELECT input_rows,stored_rows FROM
opportunity_imports`.

<a id="e3"></a>**E3 — cache distribution.** 

```sql
SELECT count(*), SUM(length(CAST(payload_json AS BLOB))),
       MAX(length(CAST(payload_json AS BLOB))),
       AVG(length(CAST(payload_json AS BLOB)))
FROM raw_cache;
-- Median: ordered lengths, average of the two central rows.
```

Filesystem check: `du -sk data/raw_cache` and `find data/raw_cache -type f -maxdepth 2 -exec
stat -f '%z' {} \;`.

<a id="e4"></a>**E4 — seasons.** `SELECT season,count(*) FROM leagues GROUP BY season`.

<a id="e5"></a>**E5 — compact delta/projection.** `VACUUM INTO
'/private/tmp/espn-edge-stage0b-full.db'`; a second clone enabled FKs, deleted internal league 27 and
its five cache keys, ran `PRAGMA foreign_key_check`, then `VACUUM`. `stat -f '%z'` returned
724,570,112 and 718,286,848 bytes. The representative was rank 58 of 115 by per-league cache bytes.
Shared sizes came from the same `dbstat` query; projection is the displayed integer arithmetic.

<a id="e6"></a>**E6 — sync replay/growth.** `.venv/bin/python
/private/tmp/espn_edge_stage0b_profile.py sync`; the profiler wrapped production parse, DB-helper
and recompute functions with `time.perf_counter`, replayed the six persisted payloads, committed,
reported macOS `resource.getrusage(...).ru_maxrss`, then `sqlite3 ... 'VACUUM'` and `stat` measured
the compact delta. No HTTP client was opened.

<a id="e7"></a>**E7 — cache freshness sequence.** `SELECT key,fetched_at,length(CAST(payload_json
AS BLOB)) FROM raw_cache WHERE key LIKE <representative-league-prefix> OR
key='pro_schedule:2026' ORDER BY fetched_at`; TTL from
[`api/config.py:27`](../../api/config.py#L27) and freshness logic from
[`api/services/espn.py:79`](../../api/services/espn.py#L79).

<a id="e8"></a>**E8 — endpoint/recompute timings.** `.venv/bin/python
/private/tmp/espn_edge_stage0b_profile.py recompute` (21 rollback runs) and `... endpoint --name
<portfolio|exposure|draft_adp|strategies|opportunity|opportunity_charts|portfolio_json|portfolio_xlsx>`
(one warm-up plus 11 timed TestClient requests, each endpoint in a fresh process).

<a id="e9"></a>**E9 — query work.** `.venv/bin/python
/private/tmp/espn_edge_stage0b_profile.py scan --name <endpoint>` captured SQLAlchemy SELECTs and
rows, then ran each statement through SQLite CLI `.scanstats on` and summed plan-node `rows=`.

<a id="e10"></a>**E10 — dataframe/library paths.** `rg -n
'pandas|numpy|openpyxl|json.dumps' api pyproject.toml`; only the dependency declarations and
`from openpyxl import Workbook` occur in application request work.

<a id="e11"></a>**E11 — Monte Carlo.** `.venv/bin/python
/private/tmp/espn_edge_stage0b_profile.py montecarlo`; 11 calls to the production
[`simulate_playoff_odds`](../../api/services/playoff_sim.py) using the displayed deterministic
fixture shape.

<a id="e12"></a>**E12 — query call sites.** `.venv/bin/python
/private/tmp/espn_edge_query_surface.py`; Python AST walked `api/**/*.py`, selected calls whose
session method is `get|scalar|scalars|execute|query`, resolved model names in the enclosing
statement, and grouped file/symbol/line/table. Source search separately checked raw SQL.

<a id="e13"></a>**E13 — route inventory.** `rg -n
'^@(router|app)\.(get|post|delete|put|patch)' api/routers api/main.py`, with response models read from
the decorators and [`api/schemas.py`](../../api/schemas.py).

<a id="e14"></a>**E14 — column storage.** `PRAGMA table_info(<each table>)`; for every column:
`SELECT typeof(column),count(*) FROM table WHERE column IS NOT NULL GROUP BY typeof(column)`.
JSON validity used `json_valid(column)` over all seven JSON columns.

<a id="e15"></a>**E15 — identity maxima.** `SELECT count(*),max(id)` on every integer surrogate-PK
table; schema SQL from `SELECT sql FROM sqlite_schema WHERE type='table'` confirmed no
`AUTOINCREMENT`.

<a id="e16"></a>**E16 — foreign keys.** `pragma_foreign_key_list(<each table>)` returned 20 rows;
`PRAGMA foreign_key_check` returned none.

<a id="e17"></a>**E17 — datetimes.** For each `DATETIME` column, SQL counted `typeof`, minimum and
maximum text; SQLAlchemy selected every non-null value and counted `value.tzinfo is None`.
Comparison sites were found with `rg -n 'tzinfo|astimezone|datetime.now|timedelta' api tests`.

<a id="e18"></a>**E18 — dialect-specific source.** `rg -n
'text\(|PRAGMA|sqlite_|ON CONFLICT|INSERT OR|json_extract|strftime|\.like\(|\.ilike\(' api tests`.

<a id="e19"></a>**E19 — unique/index inventory.** `SELECT name,tbl_name,sql FROM sqlite_schema
WHERE type='index' ORDER BY tbl_name,name`, plus each model's `__table_args__` in
[`api/models.py`](../../api/models.py).

<a id="e20"></a>**E20 — PostgreSQL DDL compile.** 

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex
from api.models import Metric
for index in Metric.__table__.indexes:
    print(CreateIndex(index).compile(dialect=postgresql.dialect()))
```

All four outputs were unconditional `CREATE UNIQUE INDEX` statements.

<a id="e21"></a>**E21 — startup race.** `.venv/bin/python
/private/tmp/espn_edge_create_all_race.py`; 20 fresh temp SQLite files, four spawned processes per
file, normal SQLAlchemy SQLite timeout, each calling the production metadata's `create_all`.

<a id="e22"></a>**E22 — tests/SQL touch.** `.venv/bin/pytest --collect-only -q`; full run via a
temporary pytest plugin attached `Engine.before_cursor_execute` and recorded the current node ID:
`.venv/bin/python -c '... pytest.main(["-q","-p","pytest_db_touch_plugin"])'`. Result: 235 passed,
139 node IDs executed SQL. `/usr/bin/time -p .venv/bin/pytest -q` measured local wall time.

<a id="e23"></a>**E23 — fixture consumers.** Python AST counted test function arguments named
`league_fixture|players_fixture|current_roster_fixture|pro_schedule_fixture`; `rg -n
'load_fixture\(|FIXTURES|FIX /' tests` located direct loaders.

<a id="e24"></a>**E24 — existing CI duration.** `gh run list --workflow CI --limit 10 --json
databaseId,conclusion,createdAt,startedAt,updatedAt,event,headBranch`; elapsed seconds are
`updatedAt-startedAt` for the 10 successful rows.

<a id="e25"></a>**E25 — stored AI inventory.** SQL grouped `ai_reports` by kind/model, counted rows,
distinct leagues/hashes and `length(CAST(content_json AS BLOB))`.

<a id="e26"></a>**E26 — current AI prompts/hashes.** A read-only Python command loaded each stored
report's current facts through `api.services.ai_inputs`, constructed the exact `_SYSTEM + _TASK +
json.dumps(facts)` inputs used by `AiService.generate`, counted UTF-8 bytes, and compared
`compute_input_hash` with the stored hash. It also constructed one representative weekly and trade
prompt. No Anthropic client was created.

<a id="e27"></a>**E27 — backup/RPO.** `tmutil destinationinfo` returned “No destinations
configured”; `tmutil latestbackup` found no mountable destination. `find` searched for backup/dump
files and `rg -n 'backup|restore|snapshot|litestream|rclone|Time Machine' README.md Makefile
docker-compose.yml api docs` found no DB backup/restore implementation. Oldest/newest timestamps
came from `MIN/MAX` over accounts, metric snapshots, AI reports, opportunity imports and ADP
snapshots.
