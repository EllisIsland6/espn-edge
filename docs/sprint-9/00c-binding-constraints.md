# Sprint 9, Stage 0c — binding constraints

Status: bridge from accepted Stage 0b evidence into later decisions. This adds no measurement and
makes no architecture choice. Derived arithmetic is shown so Stage 3 can reuse it without silently
turning assumptions into facts.

## Constraints the architecture must carry forward

1. **Relational storage is 14,503,936 bytes today; raw cache is a separate capacity domain.**
   `dbstat_total - raw_cache - sqlite_schema = 723,742,720 - 709,222,400 - 16,384 =
   14,503,936` bytes across the other 17 tables ([evidence](00-current-state.md#e2)). After removing
   1,220,608 bytes of shared reference tables and the 4,096-byte accounts table, the observed
   allocation implies about **115,472 bytes per league (112.8 KiB), not 109 KB**. Holding that
   shape constant, 11,500 leagues is about **1.329 GB (1.238 GiB)** including today's shared/fixed
   component. PostgreSQL/index overhead and history growth are not measured, so “the RDS minimum
   covers it” must be verified against the selected service in Stage 3, not asserted here.

   The earlier 67.297 GiB projection is a **cache-inclusive sensitivity**, not relational-data
   demand. It must not size a cache-free relational option; it remains relevant only to the ADR
   option that keeps raw payloads in PostgreSQL. Cross-check: subtracting the derived 115,472-byte
   relational share from the measured 6,283,264-byte representative-league delta leaves about
   6.168 MB across five cache entries, or 1,233,558 bytes each—within 0.5% of the independently
   measured 1,228,402.8-byte global cache mean ([evidence](00-current-state.md#e3),
   [delta method](00-current-state.md#e5)).

2. **Raw-cache placement is a top-level ADR.** `raw_cache` is 709,222,400 bytes: 97.44% of the
   physical file and 97.994% of live `dbstat` bytes. Every entry was beyond the six-hour TTL at the
   measurement instant ([evidence](00-current-state.md#e3), [freshness](00-current-state.md#e7)).
   The options—PostgreSQL, S3 plus lifecycle, a TTL store, or no persistence—change the measured
   storage/backup/restore surface by **48.9×**, nearly but not fully two orders of magnitude. “All
   rows were stale” is a one-time observation, not proof that caching has no value; the ADR must
   state what reuse window it buys and require hit/miss telemetry that does not exist today.

3. **Per-request database round trips are a binding latency variable.** The measured paths issue
   697–929 SELECTs per request ([evidence](00-current-state.md#e9)). They were not proven CPU-bound
   versus wait-bound locally; SQLite is in-process. Any remote-database option that preserves those
   serialized queries must budget `query count × effective per-query RTT`, in addition to query
   execution. Under the explicitly assumed—not measured—0.5 ms same-AZ RTT, that term alone is
   348.5–464.5 ms. An option whose latency budget cannot absorb its measured multiple must reduce
   the N+1 rooted in `services/portfolio.py` before cutover. Compute placement, persistent pooling
   and any proxy must carry their actual latency, availability and dollar effects; a proxy or
   cross-AZ path gets no unmeasured latency credit.

4. **The export path sets the measured memory floor.** Portfolio JSON produced a 14,351,435-byte
   response while the process reached 435.312 MiB RSS; recompute peaked at 68.766 MiB
   ([evidence](00-current-state.md#e8)). Compute/task/function sizing must begin above the export
   high-water unless the response is made genuinely streaming. Concurrency memory has not been
   measured and must not be represented as either free or a simple `435 MiB × requests` fact.

5. **The metric uniqueness model is not PostgreSQL-safe.** The four `sqlite_where` partial unique
   indexes compile as four unconditional PostgreSQL unique indexes, causing the four valid
   `(team_id, week)` null-shapes to collide ([evidence](00-current-state.md#e20)). Explicit
   PostgreSQL predicates are mandatory in the Alembic baseline. The existing identity/coexistence
   tests prove SQLite behavior only; they cannot validate generated PostgreSQL DDL.

6. **Schema migration must be serialized and versioned.** Fresh-database runs with four simultaneous
   `create_all` starters had at least one failure in 18 of 20 races
   ([evidence](00-current-state.md#e21)). Alembic
   plus one migration runner is a prerequisite to more than one application instance. The two
   hand-added opportunity columns must exist once in the baseline, followed by stamping verified
   existing schemas; startup `create_all`/ALTER is not a production migration path.

7. **Datetime migration has an explicit UTC interpretation rule.** All 85,125 populated values
   across 14 datetime columns were naive SQLite text after code supplied UTC instants
   ([evidence](00-current-state.md#e17)). Import must interpret the existing text as UTC and target
   `TIMESTAMPTZ`/timezone-aware SQLAlchemy columns. The momentum test that asserts stripped
   `tzinfo` must change; retaining timestamp-without-time-zone would preserve the ambiguity.

8. **Tenant retrofit is a measured broad change, not a router wrapper.** The surface is 122
   tenant-sensitive query sites across 19 files, 50 unowned data-returning routes, one unowned
   account-delete mutation, and a live league-account repoint primitive in `POST /api/leagues`
   ([query inventory](00-current-state.md#e12), [route inventory](00-current-state.md#e13)). Any
   isolation proposal must explain enforcement across that whole surface and close the repoint path
   before accepting more than one custodian.

9. **The current RPO is nonexistent.** No recoverable database copy or Time Machine destination was
   found. The only copies of 48 AI reports, 18,020 metric snapshots, two opportunity import records,
   one FFC snapshot and upstream-retirable facts are in this file
   ([evidence](00-current-state.md#e27)). Backup cost, restore time and a restore drill are baseline
   requirements, not optional reliability polish; raw-cache placement determines whether backups
   include 709 MB of replaceable payloads.

10. **AI cache invalidation is coupled to fact change, not mechanically to every recompute.** Zero
    of 48 stored hashes matched current facts, but Stage 0b cannot attribute that result to sync,
    recompute, model/schema changes, or their combination ([evidence](00-current-state.md#e26)). An
    idempotent recompute whose hashed facts remain equal retains the cache; a changed fact,
    model/schema version, or `force=true` causes spend. Sync cadence and AI usage are therefore
    interacting knobs, but “every recompute invalidates” is false. Schedule/cost modelling must
    instrument attempts, hits, misses, invalidation cause and usage tokens before assigning a hit
    rate.

11. **Offline sync timing is not worker-capacity timing.** The representative replay processed six
    persisted provider payloads totalling 6,271,487 bytes in 216.464 ms and peaked at 114.422 MiB,
    but provider lookup time was 0.013 ms because no HTTP occurred
    ([evidence](00-current-state.md#e6)). ESPN latency, live hit rate and concurrent behavior remain
    unknown; the future shared 1 rps budget means Stage 3 must not derive worker throughput or
    timeout settings from the offline wall clock.

12. **Database and compute portability remain unproved.** Measurements used one macOS/M2/Python
    3.14 process; concurrency and the target Linux runtime were not measured. Although 139 of 235
    tests execute SQLite SQL, zero execute PostgreSQL ([evidence](00-current-state.md#e22)). Compute
    prices require headroom for this uncertainty, and migration confidence requires a PostgreSQL
    CI/migration lane rather than extrapolation from the passing SQLite suite.

13. **The unbounded relational growth term is clean-sync history, not the static league snapshot.**
    `metric_snapshots` already holds 18,020 rows in 4,030,464 bytes, or 223.67 allocated bytes per
    row including its index ([evidence](00-current-state.md#e2)). Verification across the current DB
    found 901 distinct `(league_id, batch_id)` batches across all 115 leagues; every batch—not just
    the profiled league—contains exactly 20 rows. The code appends a batch only when metrics succeed
    and the sync has no errors ([`sync.py`](../../api/services/sync.py#L293),
    [`gamification.md`](../gamification.md#metric-snapshots)).

    At the stated nightly cadence, the current shape is `115 × 20 = 2,300` rows per day,
    839,500 rows per 365-day year, and about 187.8 MB (179.1 MiB) of allocated growth per year—12.95×
    today's entire relational allocation. The user's rounded 840,000 rows / 188 MB / 13× is valid.
    The correction is dimensional: growth is proportional to **league count × successful-sync
    cadence × retention time**, so league count still multiplies it. There is no retention, rollup
    or partition policy. Stage 3 must either state the retained history contract and its
    aggregation/deletion behavior or carry this compounding term in storage/backup projections;
    partitioning is not automatically justified at this volume.

    Verification query: `SELECT league_id,batch_id,count(*) FROM metric_snapshots GROUP BY
    league_id,batch_id`; grouping that result by count returned only `20 → 901 batches`.

14. **The irreplaceable payload is about 4.17 MB, but the restorable dependency closure is the
    cache-free relational database.** The proposed sum is exact:
    `73,728 + 4,030,464 + 12,288 + 49,152 + 4,096 = 4,169,728` bytes for AI reports, metric
    snapshots, opportunity-import diagnostics, the FFC snapshot and accounts
    ([evidence](00-current-state.md#e2)). It is a useful lower bound on uniquely generated content,
    and Constraint 13 makes metric history its fastest-growing member.

    It is **not by itself a valid restore set**. AI reports and metric snapshots reference leagues
    and teams; account configuration is useful only with league/account associations; and some
    unofficial ESPN facts may disappear upstream. Restoring only those five table allocations can
    therefore produce FK failures or semantically incomplete state. The defensible RPO scope is the
    approximately 14.5 MB cache-free relational dependency closure, unless a tested logical backup
    proves a smaller closed set. Cache placement still changes the physical backup from roughly
    15 MB to 727 MB. Capacity cost for an aggressive RPO over 15 MB should be negligible, but
    scheduling, consistency, retention and restore drills still have engineering and service cost;
    Stage 3 must price them rather than call the RPO free.

These constraints narrow later options; they do not select custody, tenancy, compute, database,
cache or availability architecture. Stage 1 remains gated on credential custody.
