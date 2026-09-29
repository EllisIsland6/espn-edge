# Sprint 9, Stage 4 — migration strategy and cutover runbook

Status: design complete; no migration, infrastructure apply or production-code change is authorized
by this stage. This runbook inherits the public-synthetic custody decision and the Stage 3
operational amendments.

## Outcome

Use a **strangler migration for the application**, with one deliberately boring **offline copy for
the private SQLite data** if local PostgreSQL is adopted. Do not dual-write at the current volume.
Launch the public stack in parallel from an Alembic-built schema and synthetic seed; no current
real ESPN row, raw response, owner identifier, AI report, opportunity/FFC capture or credential
crosses into that account.

This is the first important premise correction: the selected public deployment is not a data
cutover from the laptop. It is a new synthetic product surface. The SQLite-to-PostgreSQL work still
matters because it proves dialect correctness and supports private local mode, but using it to load
the public RDS instance would contradict Stage 1 rather than complete the migration.

“Zero downtime” means only an application-version/cutover technique here. There is no external SLA,
no existing public dynamic service to preserve and no architecture that survives the one host/AZ.
The honest target is a reversible, observable launch; a short drained swap is acceptable if two
slots do not fit.

## 1. What migrates, and where

| Track | Source → target | Rows allowed | Availability technique | Decision |
| --- | --- | --- | --- | --- |
| Public hosted artifact | Empty Alembic PostgreSQL schema → deterministic Synthetic Fixture Factory | Synthetic tenants, synthetic league/member/reference facts, operational/audit/job state generated after launch | Parallel build behind a maintenance page; atomic Caddy/SPA entry switch | **Selected.** No real SQLite import. |
| Private local mode | Existing `data/edge.db` → local PostgreSQL, or retain SQLite during transition | Real operator facts after field disposition; credentials remain in OS keychain; cache excluded | Bounded read-only maintenance window and offline copy | **Supported, not required for public launch.** |
| Private AWS mode | Existing private data → separate AWS account/RDS | Only after a new provider/custody approval | Same offline import at current volume | **Designed, not approved or deployed.** |

Existing real-data `ai_reports` are retained local-only or purged; they are never an import payload.
Existing `raw_cache` is never copied. Existing `teams.owner_swids_json` is used once locally to
derive `is_me`, then dropped. The current real nflverse/opportunity/FFC captures and recorded-derived
test fixtures are also excluded from public hosted seed/CI; the public factory synthesizes their
required shapes.

The A4 purge is an explicit cloud-export gate, not an implied filter: on the isolated export copy,
record `SELECT count(*) FROM ai_reports` (48 at measurement), delete those rows in one transaction,
compact/create the sanitized export, verify the count is zero and record only the deleted count—not
content—in the migration evidence. The export manifest must also show zero `raw_cache` and
`owner_swids_json` fields. No cloud upload begins before those assertions. The operator may retain
the untouched source database locally; “purged from cloud payload” does not pretend old local disk
blocks/backups were cryptographically erased. Recorded-derived fixtures are removed from the hosted
CI/deploy artifact after synthetic coverage, rather than merely excluded at runtime.

## 2. Migration shape: strangler outside, offline cutover inside

### Options

| Approach | Benefit | Failure/cost | Decision |
| --- | --- | --- | --- |
| Big-bang rewrite | One conceptual switch | Simultaneously changes identity, tenancy, SQL dialect, query shapes, jobs, fixtures, deployment and operations; failures are not attributable | Reject. Phase 29 is too much working contract to discard. |
| Strangler by stable seam | Each release preserves route/provider/domain contracts while replacing one boundary | Temporary adapters and compatibility code must be deleted later | **Select for the application.** |
| Permanent parallel local/cloud implementations | Avoids a decisive switch | Two behavioral products drift; a solo developer reviews every feature twice | Reject. Compatibility is transitional, not a product architecture. |

The application sequence is:

1. synthetic provider/fixture seam and PostgreSQL CI;
2. reviewed Alembic baseline and dialect-safe database configuration;
3. tenant/user/session models, transaction context, composite FKs and forced RLS;
4. set-based reads plus streaming exports under the real RLS role;
5. durable job adapter/worker and progress telemetry;
6. identity/same-origin frontend edge;
7. AWS candidate deployment, synthetic seed and observed cutover; then
8. removal of startup DDL and expired compatibility paths after the rollback window.

Each step retains offline parser behavior and existing response contracts unless its phase contract
explicitly says otherwise. This is a strangler around infrastructure seams, not a proxy routing old
and new microservices. The accepted cost is temporary adapters and a longer sequence; the benefit is
that a failed RLS test, SQL plan or worker lease has one plausible change set.

For the one-user, 14.5-MB relational private database, the final data copy is intentionally **not**
continuous. Stop writes, take a consistent SQLite backup, transform/import, verify and switch. That
small bounded big-bang inside the broader strangler is safer than inventing a replication system.

## 3. Non-negotiable release prerequisites

Cutover does not start until all are true:

1. **External health evidence works.** Fault injection has proved the Stage 3 C1 matrix: host stop,
   task crash loop, database unreachable and live worker/not-claiming. Required alarms are `OK`, not
   `INSUFFICIENT_DATA`; self-published alarms treat missing as breaching.
2. **Schema authority is singular.** Alembic builds an empty PostgreSQL database, upgrades the prior
   revision, owns the two opportunity additions, and asserts the four PostgreSQL partial predicates.
   Runtime startup performs no `create_all`, `PRAGMA` or `ALTER TABLE`.
3. **Tenant isolation is attacked, not inferred.** Every R4 named adversarial test passes using the
   deployed non-owner forced-RLS role, including absent context, colliding IDs, pooled-connection
   reuse, composite cross-tenant FKs and direct SQL.
4. **The repoint primitive is gone.** Existing league ownership cannot be changed by repeating
   `POST /api/leagues`; transfer is a separately authorized operation or a conflict.
5. **Performance gates use deployed semantics.** The four profiled reads use at most 25 SELECTs and
   meet the 750-ms warm p95 with RLS enabled. The representative export streams under 256 MiB.
6. **Cutover memory is measured.** ECS agent + Caddy + worker + loaded active web + candidate
   readiness/canary + one active export fit 2 GiB with 25% headroom, or the host is resized before
   cutover. Otherwise the runbook uses a drained swap and makes no blue/green claim.
7. **Synthetic provenance is clean.** The allowlist generator and adversarial scanner find no
   cookies, SWID-shaped values, owner/league names or unapproved capture fields anywhere in hosted
   fixtures/artifacts.
8. **Recovery is already real.** A timed RDS point-in-time restore drill and identity re-link
   exercise have passed. A backup plan without this evidence does not open the gate.
9. **Variable cost is bounded.** EC2 is explicitly Standard; RDS credit/CloudWatch/Budget alarms and
   the $8 month-to-date RDS stop guard have passed a forced trip/re-stop test; the public account plan
   contains no private-stack resources, NAT, endpoint or ALB.
10. **Origin TLS is recoverable.** The served certificate has more than 21 days remaining and the
    manual DNS-01 renewal exercise has passed.

The trade-off is a slower first launch. The failure accepted is delayed portfolio delivery; an
unobservable or isolation-unsafe cutover is not accepted.

## 4. SQLite → PostgreSQL delta and import contract

This table is the transform specification for the private import and the scrubbed migration CI
fixture. It is based on the current 18-table model and Stage 0b measurements, not on SPEC's older
schema.

| Current surface | SQLite as built | PostgreSQL target | Required transform / hazard |
| --- | --- | --- | --- |
| Fourteen populated datetime columns; 85,125 values | `DATETIME` stored as naive text such as `YYYY-MM-DD HH:MM:SS.ffffff`, although writers supplied aware UTC | `TIMESTAMPTZ`; SQLAlchemy `DateTime(timezone=True)` | Parse the naive value as UTC and attach `+00:00`; never use server local time. Replace the momentum test that expects `tzinfo=None`; audit the four defensive freshness conversions for double attachment. |
| `scoring_json`, `lineup_slots_json`, `owner_swids_json`, `details_json`, both `payload_json` columns and `content_json` | SQLAlchemy `JSON`, physically TEXT; 2,007 observed non-null values passed `json_valid()` | `JSONB` where retained | Decode and schema-validate before insert. JSONB changes textual equality/order/size. Drop owner JSON and raw-cache JSON; no general GIN index. AI week/opponent become explicit columns rather than a Python scan. |
| Eight boolean fields | Integer `0/1` under SQLite affinity | native `BOOLEAN` | Reject anything outside `0`, `1`, `NULL`; cast explicitly. Truthiness is not a migration rule. |
| Integer surrogate IDs | `INTEGER PRIMARY KEY`/rowid, no `AUTOINCREMENT`; maxima exceed row counts | `BIGINT GENERATED BY DEFAULT AS IDENTITY` | Preserve IDs in FK order; after load set every identity sequence above `MAX(id)` and prove the next insert. Do not assume row count equals sequence state. |
| ESPN/external IDs and pseudo-FKs | SQLite integer; several are not declared FKs | `BIGINT`, matching declared FK types | Current sample fits but is not a width contract. Do not silently add pseudo-FKs unless lifecycle/delete semantics are defined. Tenant children instead receive explicit composite parent FKs. |
| Counts/seasons/weeks/slots | INTEGER | INTEGER plus only evidenced checks | Reject mixed types during staging. Do not invent enums/ranges that valid historical data may violate. |
| Points/shares/EPA/ADP/metrics | FLOAT/REAL when populated | `DOUBLE PRECISION` | Reject malformed text and non-finite values where the API/JSON contract cannot represent them; do not rely on SQLite affinity. |
| Unbounded `String` columns | `VARCHAR` affinity/text | `TEXT` | Do not invent `VARCHAR(n)` truncation. Select collation/case behavior explicitly. No current application `.like()`/`.ilike()` dependency was found. |
| Twenty declared FKs / 19 cascade edges | Enforcement comes from a per-connection PRAGMA; current `foreign_key_check` is clean | Always enforced FK/composite FK | Load parent-first into constrained target and validate. Remove/dialect-gate the PRAGMA listener. Account deletion remains restricted; cascades must match. |
| Two opportunity additions | Startup inspection then hand-written `ALTER TABLE` for `receiving_tds`, `team_passing_yards` | Present once in reviewed Alembic baseline | Never replay the SQLite helper. Stamp an existing schema only after exact schema comparison; empty PostgreSQL creates them once. |
| Runtime schema creation | `create_all` + additive helper; 18/20 measured four-starter races failed | One Alembic runner under advisory lock | Web/worker readiness fails on revision mismatch. Concurrent app startup performs zero DDL. |
| SQLite-specific diagnostics | `sqlite_errorcode` branch in opportunity import | PostgreSQL SQLSTATE/driver-neutral error code | Normalize bounded diagnostic codes at the adapter; never expose backend exception text. |

### Unique and isolation constraints

The nine current named constraints are not hand-waved as “keep uniques”:

| Current constraint | PostgreSQL baseline | Specific hazard/change |
| --- | --- | --- |
| `uq_league_season(espn_league_id, season)` | `(tenant_id, espn_league_id, season)` | Global identity currently blocks two tenants from owning the same external league. |
| `uq_team_league_espn(league_id, espn_team_id)` | `(tenant_id, league_id, espn_team_id)` | Add direct discriminator and composite tenant/league FK. |
| `uq_pick_league_overall(league_id, overall)` | `(tenant_id, league_id, overall)` | Preserve current NULL-distinct behavior unless `overall` is separately made non-null with evidence. |
| `uq_matchup(league_id, week, home_team_id, away_team_id)` | Add `tenant_id`; composite team FKs | Both team IDs must resolve inside the same tenant, not merely exist globally. |
| `uq_lineup(league_id, week, team_id, espn_player_id, slot)` | Add `tenant_id`; composite league/team FKs | Do not infer tenant solely through the league join. |
| `uq_current_roster_snapshot_league(league_id)` | `(tenant_id, league_id)` | One current snapshot per tenant-league. |
| `uq_current_roster_slot(snapshot_id, team_id, lineup_slot_id, slot_index)` | Add `tenant_id`; composite snapshot/team FKs | Prevent a valid snapshot in tenant A from referencing a valid team in tenant B. |
| `uq_opportunity_player_game(season, season_type, game_id, gsis_id)` | Unchanged pooled reference key | Public stack contains generated reference facts; this key is not proof of tenant ownership. |
| `uq_metric_snapshot_batch(league_id, batch_id, team_id, key)` | Add `tenant_id`; composite league/team FKs | Snapshot batch identity becomes tenant-local. |

The four `metrics` unique indexes also include `tenant_id` and declare explicit PostgreSQL
predicates for exactly `(team_id NULL/non-NULL, week NULL/non-NULL)`. A blind autogenerate from
today's `sqlite_where` model is forbidden. The four ordinary indexes—metric snapshot lookup,
nflverse `gsis_id`, opportunity import season/time and opportunity player/week—are recreated only
after their target column/tenant shape is reviewed; none supplies isolation.

Application and worker roles are non-owner/no-`BYPASSRLS`; every tenant table has `ENABLE` and
`FORCE ROW LEVEL SECURITY`. Migration/restore ownership is the accepted privileged exception.

PostgreSQL does not repair weak ownership modeling by itself. Direct tenant discriminators,
composite FKs, transaction-local context and forced policies are all required; omitting any one
weakens defense in depth.

### Private offline import procedure

This procedure is rehearsed against a scrubbed copy before touching the operator database:

1. **Preflight (20-minute limit).** Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, table
   row counts/min/max IDs, `typeof()` checks, datetime parse checks, `json_valid()` and the nine
   unique/four-shape metric checks. Any orphan, invalid JSON/time, non-0/1 boolean or duplicate aborts
   the import; it is fixed at the source with an auditable script.
2. **Freeze (5 minutes).** Disable writes/sync/AI locally, close app sessions and make a SQLite
   online backup. Record file bytes, SHA-256, table counts and schema SQL. Preserve the untouched
   source until the rollback window closes.
3. **Stage (15 minutes target).** Copy rows into isolated PostgreSQL staging tables as source text,
   not directly into target tables. The staging role cannot serve the app.
4. **Transform/load (20 minutes target).** Create one stable operator tenant ID; load parent-first;
   normalize UTC/booleans/JSON; inject tenant IDs; split pooled reference identity from tenant
   observation; exclude raw cache, credentials, owner identifiers and A4-selected reports;
   insert into fully constrained target tables. Credentials are enrolled separately into the local
   OS keychain, never into staging.
5. **Repair generators (5 minutes).** Set every identity sequence to exceed imported `MAX(id)` and
   execute/rollback a next-insert proof for each.
6. **Verify (30-minute limit).** Compare adjusted counts and deterministic per-table hashes; run FK,
   unique/predicate, timestamp-range and JSON checks; prove no SWID/cookie/cache/report residue; run
   service smoke reads and the full RLS attack suite as the runtime role.
7. **Switch or abort (5 minutes).** Only after all evidence is green, point local configuration to
   PostgreSQL and start one app process. Keep SQLite read-only for seven days. Any mismatch or smoke
   failure points configuration back to SQLite; no target write is merged backward.

Target rows are small enough for one constrained transaction after staging. If a step exceeds its
time box, stop and diagnose rather than relaxing constraints during a migration.

## 5. Dual-write and backfill

**Do not dual-write this database.** One writer and 14.5 MB of relational data do not justify a
distributed consistency problem. A short read-only window is cheaper to reason about, test and
reverse. The accepted product cost is minutes of private local unavailability; the avoided failure
is SQLite and PostgreSQL diverging on a partial write, sequence, JSON, timezone or RLS behavior.

If future volume or continuous write demand made an offline copy unacceptable, do this instead:

1. release an application transaction-outbox into SQLite before backfill, with monotonic change ID,
   aggregate ID/version, operation and idempotency key;
2. take a consistent source snapshot and its high-water mark;
3. backfill staged PostgreSQL in bounded, checksum-verified chunks;
4. relay outbox events after the high-water mark as idempotent PostgreSQL upserts/deletes and retain
   acknowledgements;
5. shadow-read and compare business aggregates plus per-tenant counts;
6. briefly freeze writes, drain the tail, prove zero lag, switch reads/writes, and keep the old store
   read-only through the rollback window.

That is source-transaction-plus-outbox, not naïve synchronous writes to two databases. SQLite is not
an AWS DMS source and has no PostgreSQL-style logical replication stream; pretending otherwise only
hides the hardest failure window.

## 6. Public launch/cutover runbook

### Before the change window

- **T-7 days:** provision the public network/RDS/host/CloudFront behind the S3 maintenance page, then
  run the complete prerequisite suite against that topology or an equivalent isolated rehearsal DB:
  four C1 fault injections, quarterly-style restore, certificate renewal, Linux dual-slot memory and
  20 cold/20 warm PostgreSQL CI runs when that lane exists. Record evidence, not screenshots alone.
- **T-48 hours:** freeze the release candidate by Git SHA, arm64 image digest, SPA manifest hash,
  Terraform plan hash and Alembic head. Lower no DNS TTL: Route 53 already aliases the stable
  CloudFront distribution, which serves an S3 maintenance page.
- **T-24 hours:** confirm the `$150` AWS budget, `$5` AI ledger, EC2 Standard credit setting, RDS
  surplus alarm and $8 stop guard, origin certificate >21 days, PITR `LatestRestorableTime`, deletion
  protection and 14-day retention. Confirm every required alarm is `OK`.

### Change window

| Step / time box | Action | Verification | Rollback trigger and action |
| --- | --- | --- | --- |
| 1. Plan lock — 10m | Re-run read-only Terraform plan against the approved hash. | No NAT, endpoint, ALB, private-stack/KMS/raw-cache resource or unpriced replacement. | Any drift/replacement: abort before mutation. |
| 2. Infrastructure reconcile — 30m | Apply the no-replacement approved delta while CloudFront still serves maintenance. | RDS private/same-AZ, host/task/guard roles, SG routes, backups, alarms and budget match ADRs. | Any replacement, apply error or unexpected resource/cost: keep maintenance and abort; do not improvise a replacement in-window. |
| 3. Schema — 10m | Run the pinned one-off migration task under advisory lock. | `alembic current=head`; expected tables, RLS/FORCE, roles, FKs and four partial predicates. | Any revision/DDL assertion failure: abort. Do not start web/worker or run an automatic downgrade. |
| 4. Synthetic seed — 30m | Load deterministic colliding tenants through an idempotent seed job; take the pre-traffic manual snapshot. | Seed manifest hashes/counts; adversarial identifier scan; second run changes zero rows; snapshot reaches `available`. | Hash/count/provenance difference: truncate synthetic candidate under migration role and reseed; no traffic. Snapshot failure: abort before candidate. |
| 5. Candidate — 20m | Start worker and inactive web slot. Run local-origin canaries over SSM and one synthetic queued job. | Readiness, session, CSRF, cross-tenant 404, RLS SQL, ≤25 queries, streaming memory, job claim/success/audit. | Any isolation failure: stop candidate and disable API behavior immediately. Any other gate failure: keep maintenance and diagnose. |
| 6. Edge path — 15m | Exercise CloudFront `/api` and `/auth` with the final domain while SPA entry remains maintenance/operator-only. | Viewer/origin TLS, host/header restriction, no API cache, 503 fallback, cookies/CSP/CORS and trace correlation. | Auth cache/cookie/origin failure: restore maintenance behavior and prior Caddy slot. |
| 7. Switch — 5m | Atomically switch Caddy to candidate, then publish content-addressed SPA entry/config. | External synthetic login and both colliding tenants; no alarm leaves `OK`. | Restore prior Caddy target and prior S3 entry manifest. If no prior app exists, serve maintenance/503. |
| 8. Observe — 30m minimum | Run bounded reads/export/job canaries; watch errors, latency, connections, credits, memory and alarms. | 5xx <1%, warm p95 <750 ms, queries ≤25, no OOM/restart, canary job <10m, tenant attacks pass. | Isolation leak: immediate fail-closed rollback. 5xx ≥1% for 5m, three health failures, OOM/restart, alarm breach, p95/query breach for 10m or job >10m: rollback slot/entry within 5m. |
| 9. Close — 10m | Mark launch successful; retain prior slot/assets and snapshot. | Evidence bundle contains plan/digests/revision/tests/metrics/timestamps. | Missing evidence keeps window open; no cleanup. |

The maintenance page and old assets stay available throughout. A first launch can therefore be
reversed to a truthful unavailable state without risking current private operation. The laptop
continues to run private mode independently; it is not a public fallback and receives no cloud
traffic/data.

### Rollback semantics after database changes

- Initial schema and seed are additive/new, so the prior state is “no public app,” not an older
  database contract.
- Later releases use expand/contract: release N adds nullable structures, backfills and validates;
  N+1 switches reads/writes; N+2 removes old structures only after the rollback window and snapshot.
- The previous application image must remain compatible with the expanded schema. Rollback switches
  image/entry; it does **not** run Alembic downgrade automatically.
- If a new release corrupts data, stop writes, retain forensic logs/snapshot, restore PITR to a new
  DB instance, validate, update the restored DB's new IAM database resource permission/endpoint and
  switch. A destructive down migration is not a recovery plan.

## 7. Zero downtime: useful term, wrong requirement here

For this first public launch there is no prior public workload, so “zero downtime” is not meaningful.
For later releases, same-host blue/green can preserve in-flight requests only if the C4 dual-slot
memory test passes. It remains one kernel, EIP, Caddy and AZ; host failure still makes the site dark.
If memory does not fit, use the two-minute staged drain and call the interruption what it is.

If a genuine 99.9%/continuous-cutover requirement appeared, use at least two independently placed
web tasks behind an ALB, Multi-AZ RDS, connection draining/health gates and expand-contract schema
migrations. Stage 3's comparable Fargate+ALB path is about **+$42.26/month** over the selected host
and Multi-AZ RDS about **+$13.98/month**, a minimum **+$56.24/month** before NAT, cross-AZ traffic or
extra observability. That remains under the ceiling but pays continuously for a failure target this
artifact does not owe. The failure accepted today is a bounded maintenance/restore event, not a
false zero-downtime claim.

## 8. Disaster recovery and the restore drill

### Backup scope and policy

- RDS retains 14 days of automated backups/PITR and a manual snapshot immediately before schema or
  cutover risk. The relational instance contains the cache-free recovery closure; raw cache and
  generated exports are absent by design.
- Manual/final snapshots are deliberate retained objects; retained automated backups expire with
  the configured period. RDS restores both snapshots and PITR into a **new** DB instance, never over
  the source ([PITR](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html),
  [snapshot restore](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html),
  [retained-backup behavior](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.Retaining.html)).
- Terraform state and deployment/identity inventories use versioned operations S3; static SPA/ECR
  retains deploy artifacts. These reconstruct infrastructure, not user passwords/MFA.
- A daily Cognito user inventory supports diagnosis/re-linking but is not a pool backup. PostgreSQL
  is authoritative for authorization. After DB PITR, a Cognito-only user receives default deny until
  an audited membership repair. After pool loss, users manually re-enroll/MFA and an operator
  re-links verified subjects; the accepted identity-recovery exception is up to two business days.
- Private local data has **$0 AWS backup cost** because it does not enter the public account. Retaining
  private mode responsibly still requires a separately encrypted, off-device local backup and a
  restore test; acquisition/provider cost is not measured or selected here and is outside the public
  RPO. Until that exists, private local recovery remains explicitly weaker than public RDS.

### Quarterly timed restore drill

Budget: four operator hours, approximately **$0.03/month amortized** at the selected RDS size/storage
as already carried in Stage 3.

1. Insert synthetic restore canary A through the application and record its committed UTC time.
   Measure `now - LatestRestorableTime`; the RPO passes only at five minutes or less (the ten-minute
   alarm is an incident threshold, not the objective). Wait until `LatestRestorableTime` passes A;
   insert canary B.
2. Start the timer and restore to a time after A but before B, creating a new isolated `db.t4g.micro`
   with no route from the public application SG. Explicitly select the target subnet group, SG,
   parameter group, encryption and 20-GiB gp3; never accept restore defaults.
3. When available, grant a one-off recovery task access to the restored instance's **new** DB
   resource ID and endpoint. Verify TLS hostname, engine version and Alembic revision; apply only
   reviewed forward migrations needed by the pinned recovery image.
4. Prove A exists and B does not. Compare expected per-table counts/hashes; run FK/unique/partial-
   index checks; inspect RLS/FORCE/role attributes; verify budget ledger, sessions, audit/jobs and
   retained snapshots. Exercise representative reads to remove lazy-load surprise from the RTO.
5. Run absent-context, colliding-tenant, guessed-ID, cross-tenant FK and pooled-connection canaries as
   the restored runtime role. Serve smoke reads through a one-off application task without external
   ESPN/Anthropic calls.
6. Record restore-ready time, verification-complete time, selected recovery point, observed data
   loss, query evidence and operator actions in the operations bucket. Update
   `restore_drill_age_days` only on full pass.
7. Delete the isolated drill DB after evidence review. A drill exceeding four operator hours,
   showing more than five minutes of recovery-point lag, failing any RLS/constraint check or requiring an
   undocumented permission is a failed release gate—not a mostly successful backup test.

### Incident restore path

Declare maintenance and stop writes; preserve a final forensic snapshot if the source is reachable;
choose the latest known-good time; restore a new instance; run steps 3–5 above; update task IAM for
the new RDS resource ID and endpoint; start the inactive web slot; canary; switch Caddy; observe for
30 minutes. Keep the damaged instance isolated until cause/evidence is captured. Human detection
latency is outside the four-hour operator RTO; once the operator starts, the same timed drill is the
evidence that four hours is credible.

## 9. Is there a hybrid story?

There is a legitimate **two-mode** story, but not a hybrid-cloud runtime:

- public AWS is synthetic, multi-tenant and exercises identity/RLS/jobs/recovery;
- private local mode holds the operator's real cookies/data under OS/FileVault boundaries and may
  remain SQLite while the PostgreSQL adapter matures; and
- an optional single-tenant self-host/local-PostgreSQL package can reuse the domain/provider
  interfaces without public Cognito or cloud custody.

There is no tunnel, shared database, cross-account decrypt path, cloud-triggered laptop sync or
upload from private local into public tenants. Calling that “hybrid cloud” would imply a connected
control/data plane that does not exist. “Separate public synthetic and private local deployments
from one tested codebase” is accurate and defensible. The trade-off is duplicated mode testing; the
accepted product loss is no unattended 03:00 cloud sync for real leagues.

## What a staff engineer would ask about this

1. **“Why build a SQLite importer if no real rows go to the public database?”** Because dialect,
   timestamp, sequence, FK and partial-index migration correctness is part of the portfolio evidence
   and enables private local PostgreSQL. But the import is tested with scrubbed/synthetic data;
   shipping the actual 709-MB cache or 14.5-MB real closure to public RDS would violate the selected
   trust boundary. Scope is demonstrated without silently changing custody.
2. **“How do you roll back after the new app has written rows?”** The release before cutover is
   expand-only and the old image remains schema-compatible. Rollback switches Caddy/image/SPA, not
   schema. Destructive contract changes wait for a later release and snapshot. Actual data
   corruption stops writes and invokes new-instance PITR; an automatic down migration would compound
   the incident.
3. **“Does five-minute RPO include Cognito identity?”** No. It covers the cache-free relational
   closure. Cognito authenticates; PostgreSQL authorizes. Pool/DB divergence fails closed, and pool
   loss can require manual re-enrollment/re-linking for up to two business days. Claiming the RDS RPO
   protects MFA/password state would be false.

**Whiteboard cold for a senior interview:** strangler versus big bang; offline cutover versus
outbox/CDC; expand-contract and backward-compatible rollback; SQLite rowid versus PostgreSQL
identity; naive UTC to `TIMESTAMPTZ`; partial unique indexes and NULL shapes; forced RLS/composite
tenant FKs; RPO versus RTO versus availability versus cutover; PITR to a new instance; failure-signal
design where silence is data.

**Implementation detail to look up:** exact Alembic operations/advisory-lock SQL, PostgreSQL catalog
queries, `setval` syntax, `sqlite3_backup` invocation, RDS restore CLI flags/resource-ID IAM ARN,
Caddy admin/reload commands, CloudFront error-response fields and Terraform resource arguments.

## Amendments

These amendments close the four gaps found at the Stage 4 gate. They change release prerequisites
and recovery evidence, not the selected public/private topology.

### D1. Private recovery is a release gate, not a future intention

The accepted runbook inverted recovery effort. The public synthetic database is reproducible; the
private local database contains the only irreplaceable state and currently has no recovery point.
That is not an acceptable residual risk for this sprint.

**Decision (judgment call):** Phase 31, `private-recovery-baseline`, owns a scripted, encrypted,
off-device backup and one successful restore **before** private-mode database migration, destructive
retention, or public launch. Stage 6 may refine the phase title but may not move this work behind the
launch gate. The backup remains outside the public AWS account, so it does not weaken the Stage 1
deployment boundary.

The backup contract is:

1. Take a transactionally consistent SQLite online backup (or a PostgreSQL snapshot/export after
   local migration) to a private scratch directory. Build a cache-free recovery artifact containing
   the relational closure but excluding `raw_cache`, `accounts.swid`, encrypted `espn_s2`, keychain
   material and any other credential field. Preserve non-secret account configuration. The script
   fails closed if an excluded table/column appears in its manifest.
2. Add a schema/Alembic version, per-table row counts, constraint inventory, SHA-256 manifest and a
   committed recovery canary. Back the artifact up with a reviewed open-source encrypted repository
   tool to a physically separate operator-controlled volume. The repository password/private key is
   stored separately from both laptop and backup volume; no plaintext scratch artifact remains
   after a successful run.
3. Run after every clean full-portfolio sync and hourly when protected tables changed. Retain 48
   hourly, 30 daily and 12 monthly recovery points. The normal RPO is **one hour while the laptop is
   awake and the off-device target is attached**. If either is unavailable, the honest RPO is the
   age of the last successful backup, not one hour. Surface that age locally; at 24 hours stale,
   fail closed for new sync, AI generation and opportunity/FFC import writes until a backup succeeds.
4. Record success/failure and manifest metadata locally without secrets or report content. A clean
   sync is not complete operationally until its post-sync backup succeeds or the UI declares the
   recovery-point breach.

At this cadence, ordinary loss is at most one hour of AI reports, import diagnostics, configuration
changes and metric-snapshot batches. A sleeping laptop or disconnected/failed volume loses
everything since the displayed last-success time; the 24-hour write gate bounds that exposure rather
than pretending a scheduler ran. Constraint 13 makes this material: each unprotected clean sync can
add another snapshot batch.

The initial and quarterly private restore drill creates a scratch database and:

- decrypts one selected recovery point using the documented break-glass key path;
- verifies the manifest and canary, expected adjusted row counts and per-table hashes;
- runs SQLite `integrity_check`/`foreign_key_check` or PostgreSQL FK checks, all unique constraints
  and the four explicit partial-index predicate/NULL-shape probes;
- proves every excluded cache/credential field is absent and protected account configuration is
  present; and
- starts the pinned private image against the scratch database and runs the four profiled reads
  offline, with no ESPN or Anthropic call.

Phase 31 is not complete until that restore passes from the off-device copy and the result is timed
and recorded. A failed or stale backup blocks the private migration and any destructive cleanup. It
does not block read-only use of the existing laptop database. Tooling costs **$0/month** and this adds
**$0 AWS/month**; a separate physical volume is a one-time operator purchase, not an AWS line item.
Its actual purchase price must be recorded by Phase 31 rather than invented in this design. If no
separate volume is supplied, the phase and launch recovery prerequisite remain blocked.

This replaces §8's weaker statement that private recovery merely “requires” an unselected backup.

### D2. Cutover positively proves telemetry publication

Alarm state alone is not cutover evidence. `OK` can become `INSUFFICIENT_DATA` after a namespace,
dimension or `cloudwatch:PutMetricData` regression, while the operator watches the wrong signal.
Steps 5, 7 and 8 therefore gain a **telemetry contract self-test**:

- stop the old worker publisher and record the new worker's start timestamp before its telemetry
  self-test; do not add a release/task-ID metric dimension, which would create an unbounded series;
- the new worker reads the authoritative current values and publishes one valid sample for each of
  the ten Stage 3 custom series: `worker_heartbeat`, `sync_due_leagues`,
  `sync_clean_succeeded_leagues`, `sync_oldest_success_age_seconds`,
  `job_oldest_eligible_age_seconds`, `dead_job_count`, `provider_pipeline_failure_count`,
  `ai_authorized_spend_microdollars`, `restore_drill_age_days` and
  `origin_certificate_days_to_expiry`;
- query CloudWatch `GetMetricData` for the stable namespace/dimensions over the interval beginning
  at the new worker timestamp and require a post-start datapoint (`SampleCount >= 1`) for **each**
  series. The worker may source certificate and restore ages from their authoritative checks for
  this one contract publication; their normal independent publishers remain authoritative; and
- preserve the query response, timestamps, namespace and dimensions in the release evidence.

Step 7 verification is now: external synthetic login and both colliding tenants pass; all ten
post-start samples are present; every required alarm is `OK`. Step 8 adds a hard rollback trigger:
**any custom series absent from the new task for ten minutes**. Any required alarm entering
`INSUFFICIENT_DATA` after the task swap is also an immediate rollback signal, not deploy noise to
wait out. This is intentionally stronger than missing-data alarm configuration: it proves the new
release can publish the contract before those alarms are trusted as rollback inputs.

### D3. RDS credit guard behavior during the change window

The `$8` RDS surplus-credit guard remains armed at the same threshold; the change window does **not**
buy temporary headroom by weakening the hard-ceiling control. Instead:

- record RDS `CPUCreditBalance`, month-to-date `CPUSurplusCreditsCharged` and guard state at T-24 and
  immediately before step 1;
- at the T-7 rehearsal, record those values immediately before and after steps 3–8 and the maximum
  five-minute charged-credit increase. The live precondition is that current month-to-date charged
  credits plus **1.25 times** the rehearsal's steps-3–8 draw remains below the 6,400-credit trip
  point. This is the only credit-draw estimate used; no laptop benchmark is substituted for it;
- if the precondition fails, abort before the change window. Wait for the UTC billing reset or
  re-plan/rehearse a different database size/workload. Do not raise the threshold ad hoc; and
- step 9 positively asserts the guard Lambda is enabled and its configured threshold is restored to
  exactly 6,400 credits/$8. It should already match because this runbook does not raise it; the
  close-out check catches drift or manual intervention.

A guard trip during steps 3–8 is **abort-and-diagnose**, not an application rollback trigger. Keep
CloudFront on maintenance, stop new mutation, preserve metrics/logs and determine whether the RDS
stop was the budget guard before changing an image or schema. Rolling back an application while its
database is intentionally stopped confuses two failure signatures and supplies no recovery. Resume
only under a newly approved/rehearsed window; the hard cost ceiling wins over launch availability.

### D4. N−1 compatibility is executable release evidence

The earlier requirement that every application image require exact Alembic-head equality conflicts
with expand-contract rollback. If release N−1 rejects release N's expanded head, switching back to
N−1 is not a rollback plan.

**Decision:** the migration runner still requires the exact release-N head. Web and worker readiness
validate a release-declared **schema compatibility contract**: the current head must be one of the
immutable heads declared compatible with that image, and the database contract version must not be
below the image minimum. For ordinary releases the set is exact; an expand release deliberately
keeps N−1 compatible with N's head until the later contract release. There is no environment
override that skips this check.

CI adds `n-minus-one-schema-compatibility` as a release prerequisite:

1. create PostgreSQL from release N−1, apply release N's migrations and load the deterministic
   colliding-tenant seed;
2. start release N−1's immutable web and worker image digests with the actual non-owner forced-RLS
   roles and the same compatibility manifest that will ship;
3. prove startup and readiness, then run portfolio board, portfolio exposure, portfolio strategies
   and opportunity-charts reads with the R9 query-count assertions; and
4. enqueue one eligible synthetic job, have the N−1 worker claim it, and verify lease/idempotency
   state without ESPN or Anthropic traffic.

The job stores image digests, old/new heads, contract version and test results in release evidence.
If it fails, the release is marked `rollback_safe: false` in its `docs/phase-N-*.md` contract and may
not use the normal blue/green runbook. It requires a separately reviewed maintenance/forward-fix or
restore plan before scheduling. That is a legitimate exceptional release property, but discovering
it during the window is not.

## What a staff engineer would ask about the amendments

1. **“Is an hourly RPO real if the laptop sleeps?”** Only while the machine and separate target are
   available. The displayed last-success age is the actual RPO, and protected writes stop at 24
   hours stale. The design accepts read-only availability rather than inventing a background SLA an
   operator laptop cannot keep.
2. **“How do post-start metric samples prove the new worker published them without a task-ID
   dimension?”** The old worker publisher is stopped first, the start boundary is recorded, and the
   new worker emits the complete contract once. Querying only later timestamps avoids permanent
   release dimensions and their cardinality/cost. That handoff is itself a cutover step and is
   rehearsed at T-7.
3. **“What if N−1 starts on N's schema but writes old semantics into a new column?”** Readiness is
   necessary, not sufficient: the CI job includes one real job claim plus the four read contracts,
   and each expand migration must specify dual-readable/default/write semantics in its phase
   contract. If semantic compatibility cannot be demonstrated, `rollback_safe: false` is the honest
   result and the normal rollback path is unavailable.

**Whiteboard cold for a senior interview:** RPO scope versus backup cadence; client-side encryption
and key separation; positive telemetry versus alarm-state inference; distinguishing budget-control
failure from application failure; expand-contract compatibility and executable N−1 rollback proof.

**Implementation detail to look up:** the selected encrypted repository CLI, CloudWatch
`GetMetricData` query syntax, RDS credit metric units, compatibility-manifest encoding and CI
container orchestration.

**GATE: Stage 4 amendments complete. Proceed to Stage 5 only.**
