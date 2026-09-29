# Phase 30 — Private Recovery Baseline

Status: **draft — implementation prohibited until independent review and operator acceptance of
the immutable SHA-256 digest**

Source plan: [`docs/sprint-9/06-phases.md`](sprint-9/06-phases.md), Phase 30

## Outcome and risk retired

Create the first recoverable copy of the private operator database before any later phase changes
its schema, retention, tenancy, or deployment. The phase produces a transactionally consistent,
cache-free and credential-value-free logical SQLite artifact, places it in an encrypted repository
on a physically separate operator-controlled volume, and proves a scratch restore from that
off-device repository.

This retires the present-tense single-copy failure documented in
[`00-current-state.md`](sprint-9/00-current-state.md#0b7-baseline-operations-the-blunt-version): a
laptop or disk loss currently destroys the only copies of AI reports, metric-snapshot history,
import diagnostics, the FFC snapshot, account configuration, and upstream-retired facts. It does
not make the private laptop highly available and does not create an SLA.

The selected private-local target is:

- normal RPO: **at most one hour while the laptop is awake and the configured off-device volume
  and automation credential are available**;
- truthful RPO while either dependency is unavailable: the displayed age of the last successful
  recovery point;
- protected-write cutoff: **24 hours** since the last successful recovery point;
- recovery target: one operator-executed, offline scratch restore. No RTO is claimed until the
  drill produces a measured duration.

The 24-hour cutoff accepts stale local analytics and blocks the four protected ingestion/generation
families rather than silently extending their loss window. It does **not** make the application
read-only: account and league administration/deletion remain available and can be lost after the
last recovery point. This is an operational safety control, not an adversarial authorization
boundary; a user who controls the laptop can disable local software.

## Current behavior and dependencies

Current behavior is code, not the target documents:

- `api/db.py:init_db` creates the SQLite schema at startup and has no backup or restore path.
- `raw_cache` is a table in the same file, not a filesystem cache; it accounts for 709,222,400
  measured bytes while the rest of the relational file accounts for 14,503,936 bytes
  ([binding constraints](sprint-9/00c-binding-constraints.md#constraints-the-architecture-must-carry-forward)).
- `accounts.swid` is plaintext and `accounts.espn_s2_encrypted` is Fernet ciphertext
  ([`api/models.py`](../api/models.py)); restoring either would restore replayable custody.
- `teams.owner_swids_json` persists third-party identifiers that the accepted target no longer
  needs after `is_me` has been derived.
- there is no backend portfolio scheduler. `SYNC_CRON` and APScheduler are inert; the current
  full-portfolio operation is a browser sequence of per-league sync requests.
- `restic`, `age`, and `borg` were not found on the 2026-08-12 development host. Installing or
  downloading a repository tool is not authorized by this draft.

Dependencies:

1. Phase 29 is the current application baseline. Phase 31 and every later data mutation wait for
   this phase to close.
2. The operator must supply a mounted volume whose **physical storage parent** differs from the
   source database's physical storage parent, the repository location on it, and its one-time
   acquisition cost (or state that an already-owned volume has $0 incremental acquisition cost).
   A second APFS volume/partition or disk image on the laptop disk does not qualify.
3. The operator must approve acquisition and verification of a pinned `restic` release, then
   separately approve initialization of the named empty repository. Backup never auto-initializes a
   repository. Phase implementation and tests may use a deterministic fake command; the phase
   cannot close on the fake.
4. The operator must provision two copies of the repository secret: an automation copy held by an
   OS credential command and a break-glass copy stored on neither the laptop nor backup volume.
   The initial and quarterly drills make the automation copy unavailable and use the break-glass
   path. The secret value and recovery-report contents never enter this repository or agent context.
5. Native macOS is the sole Phase 30 recovery-capable topology. Docker must report recovery as
   unsupported/unconfigured and receives no repository mount or credential. Docker recovery parity
   requires a later contract; Phase 30 does not place `restic` or private-media access in the image.
6. The actual drill requires verified at-rest encryption for its plaintext scratch location—either
   FileVault on the host volume or a separately encrypted scratch volume. If neither can be proven,
   the real restore remains blocked rather than assuming deleted SSD blocks are unrecoverable.

## The recovery boundary

### Question

What is the smallest recoverable artifact that preserves the cache-free relational dependency
closure without turning a backup into a second cookie vault?

### Lens

Minimize irreversible loss and credential blast radius while keeping the design local, boring,
offline, testable, and $0/month in AWS.

### Selection

Use a **logical SQLite recovery bundle**, not a byte copy of `edge.db`. A byte copy would preserve
the 709 MB raw cache and both credential values. A hand-selected five-table dump would violate
foreign keys and omit upstream-retired dependencies. A versioned logical bundle can retain every
safe current table/column, reconstruct the normal schema during restore, and fail closed when the
source schema changes.

The encrypted repository adapter is `restic` with a local filesystem backend on the separately
mounted volume. The application does not implement cryptography, retention storage, or repository
format itself. The trade-off is a new pinned local binary and operator key/volume setup. The
accepted failure is that backups stop while the laptop, volume, or credential command is
unavailable; the status and write gate expose that fact.

### Synthesis

The versioned allowlist contains all current relational tables except `raw_cache`, with these
column-level exclusions:

| Source | Artifact disposition | Restore disposition |
| --- | --- | --- |
| `raw_cache` | Entire table omitted from the logical bundle. | Normal application table is recreated empty. |
| `accounts.swid` | Value omitted. | Fixed non-secret reauthentication sentinel; never a recovered credential. |
| `accounts.espn_s2_encrypted` | Value omitted. | Fixed non-Fernet sentinel; account is unusable until explicit reauthentication. |
| `accounts.status` | Original non-secret value retained as manifest metadata only. | Forced to `needs_reauth`. |
| `teams.owner_swids_json` | Value omitted. | `NULL`; retained `is_me` is authoritative for the restored historical view. |
| Every other current table/column | Retained inside the encrypted bundle, including private report content and league facts. | Restored with original primary/foreign keys and safe values. |

The artifact schema is a recovery format, not an application migration. `api/models.py`, the source
SQLite catalog, and existing rows remain unchanged. An unknown source table or column makes bundle
creation fail until the allowlist and phase contract are reviewed; it is never silently skipped.

The bundle contains an artifact-only recovery canary, format version, full source-catalog
fingerprint (tables, ordered columns, affinities, nullability, defaults, primary/foreign keys,
indexes, predicates and SQL), per-table adjusted row counts, constraint inventory, and canonical
per-table content hashes. These values are encrypted with the bundle. The local unencrypted status
file may contain only coverage/snapshot timestamps, result/error codes, aggregate byte counts,
format/schema versions, a random evidence-run ID, and a redacted physical-store class; it contains
no path, repository snapshot ID, device serial, row/table count or hash, credential, name, report
content, or private payload.

## Implementation contract

### Owned paths and sole writer

After operator acceptance, `production_sre` is the only implementation writer and receives one
lease for exactly:

- new `api/recovery.py`, `api/services/recovery.py`, and `api/routers/recovery.py`;
- `api/config.py`, `api/main.py`, `api/schemas.py`;
- admission/trigger wiring in `api/routers/leagues.py`, `api/routers/ai.py`, and
  `api/routers/opportunity.py`; the authoritative service boundaries in `api/services/sync.py`,
  `api/services/ai.py`, `api/services/opportunity.py`, and `api/services/ffc_adp.py`; restored
  account-status enforcement only in `api/services/espn.py`; and the direct CLI entry points
  `api/verify.py` and `api/opportunity.py`;
- new `ops/private-recovery/**` launchd template and local operator runbook;
- recovery/status client changes in `web/src/api.ts`, `web/src/pages/Status.tsx`,
  `web/src/pages/Manage.tsx`, and `web/src/pages/PortfolioBoard.tsx`;
- new or phase-scoped tests under `tests/test_recovery*.py` and the minimum existing affected test
  files; phase-scoped Playwright additions under `web/e2e/**`;
- `.env.example`, `Makefile`, `README.md`, and `pyproject.toml` only where the accepted
  implementation requires recovery configuration or commands. `api/Dockerfile` and
  `docker-compose.yml` remain unchanged; recovery is unsupported inside Docker in this phase.

`api/models.py`, `api/db.py`, Alembic/migration paths, analytics formulas, provider adapters,
parsers, raw-cache implementation, existing fixtures, Terraform/AWS files, and every unrelated
dirty path are forbidden. The writer must preserve all pre-existing user changes and return a
deterministic candidate digest rather than commit.

### Backup and state machine

1. `python -m api.recovery backup --reason hourly|post-clean-portfolio-sync|manual` obtains an OS
   inter-process lock keyed to the canonical source/repository pair before temp, state or repository
   work. The lock is crash-released. API, CLI and launchd processes contend on the same lock.
2. It opens a read-only SQLite connection, begins one read transaction, and directly emits the
   allowlisted logical bundle from that consistent snapshot into a bounded in-memory/pipe stream.
   A full physical copy containing raw cache or credentials is prohibited at every stage. Tests
   include committed and uncheckpointed WAL rows plus a concurrent writer.
3. It canonicalizes tables/columns in catalog order and rows by primary key. The recovery-format
   version defines tagged encodings for `NULL`, integer, IEEE-754 float, UTF-8 text, bytes, JSON
   with sorted keys, and the database's stored datetime text. An independent QA oracle, not the
   production serializer, calculates expected adjusted counts/hashes on a synthetic closure with
   representative null/JSON/float/datetime shapes. The fixture uses a distinct type-valid sentinel
   for every retained column where its domain permits: high-entropy values for text/blob/JSON,
   randomized legal integer/float/datetime values, both boolean values, legal NULL/non-NULL cases,
   and row-level high-entropy correlation IDs for constrained/boolean columns. Foreign keys remain
   valid, and the oracle rejects a value whose SQLite storage class does not match the column's
   recovery-format type.
4. Immediately before and after repository invocation, it resolves canonical, non-symlink paths
   through the macOS mount/APFS topology to distinct physical storage parents. It rejects the same
   parent disk, APFS siblings, disk images, network filesystems, symlink traversal and mount swaps.
   Evidence records pass/fail and media class, never device serials or paths.
5. `restic` executes by absolute operator-owned path with no shell, a fixed argv schema, constant
   tags/filenames, bounded sanitized output and a minimal allowlisted environment. The tool verifies
   exact version plus reviewed release digest/signature; clears inherited `RESTIC_*`, cloud and
   proxy variables; and rejects remote repository schemes and repository-path replacement.
6. It creates one encrypted snapshot from the stream, verifies repository success, and atomically
   writes the secret-free `0600` state. A failed run records only a stable error code and keeps the
   prior recovery-coverage timestamp authoritative.
7. `--if-changed` compares the current safe-content digest with the prior encrypted-manifest digest.
   When content is identical, it verifies repository reachability, prior snapshot existence and
   digest agreement, creates no snapshot, advances `last_coverage_at`, and retains
   `last_snapshot_at`. Raw-cache-only changes therefore renew coverage without consuming a point;
   repository absence/corruption never advances coverage.
8. The reviewed retention command selects 48 hourly, 30 daily and 12 monthly snapshots. Before any
   real deletion, a secret-free `forget --dry-run` equivalent records the candidate survivor set,
   proves that the selected drill point and at least one verified current point survive, and rejects
   a wrong repository/tag or zero-survivor result. The operator then separately approves the exact
   destructive argv and canonical repository identity. Only that approval may enable the real
   `forget`/`prune` and its recurring schedule. An interrupted or failed application preserves both
   coverage timestamps, leaves `retention_enforced=false`, and fails phase closure. Status
   distinguishes `retention_configured` from `retention_enforced`; the actual break-glass restore
   runs **after** retention application, and Phase 30 cannot close without that post-retention proof.

The hourly runner is a generated native-macOS `launchd` user-agent template, not APScheduler.
Loading that agent is a local system mutation and requires explicit operator approval. It invokes
only the recovery CLI and never ESPN, FFC, nflverse, Anthropic, or AWS. Native pre-run/startup
scavenging examines only a fixed tool-owned `0700` restore-scratch root, rejects symlinks and removes
or quarantines stale tool-owned scratch after bounded-age and ownership checks. Tests inject SIGKILL
after every restore stage. The residual failure accepted is that SSD secure deletion cannot be
proved; at-rest encryption, minimum lifetime and bounded scavenging reduce that exposure.

### Status and write admission

Add DB-free `GET /api/recovery/status` and local-only synchronous
`POST /api/recovery/backup?reason=post-clean-portfolio-sync|manual`. The mutating route requires a
strict loopback Host allowlist, exact allowed Origin, a non-simple operator-action header and a
persisted API-trigger rate; missing/foreign/rebinding requests fail before lock or repository
access. The production default and minimum are one accepted API-trigger start per 60 seconds across
all API processes, keyed to the canonical source/repository pair. The atomic `0600` state records
an aware-UTC `last_api_trigger_at` before repository work, so failure or restart cannot erase the
bound; clock rollback/future/corrupt state fails closed. A too-early trigger returns a stable 429
with a bounded `Retry-After`. CLI/launchd scheduling is not subject to this browser-abuse limit but
still shares the inter-process single-flight lock.
Responses contain only:

```text
required, supported_topology, configured, target_available, state,
last_coverage_at, last_snapshot_at, age_seconds, stale_after_seconds=86400,
last_result_code, artifact_bytes, format_version, schema_fingerprint_short,
retention_configured, retention_enforced
```

They never contain source/repository paths, commands, keys, row hashes, table contents, names, or
credential state. Concurrent backup returns a stable conflict without starting another process.

One central recovery-admission function is called at the authoritative service boundary and
protects only operations named by the accepted runbook:

- `SyncService.sync_league` before constructing/decrypting an ESPN client or starting provider/DB
  mutation; router, verify CLI and any direct service call share it;
- the uncached branch of `AiService.generate` before any report insert or Anthropic construction/
  call; cached read paths remain readable;
- `refresh_opportunity` before any nflverse construction/fetch or import write, including
  `python -m api.opportunity refresh`; and
- `refresh_ffc_adp` before any fetch, snapshot insert or player update.

When recovery is unsupported/unconfigured, corrupt, `last_coverage_at` is absent, retention is not
enforced after its approval gate, or age reaches 24 hours, these operations fail closed with a
stable `recovery_point_stale` result and coverage age when known. Account reauthentication,
account/league administration, deletes, and all GET/read/export paths remain outside this gate;
later tenancy/deletion phases govern them. Therefore the cutoff bounds the four high-volume write
families, **not every configuration mutation**—account/league admin changes after the last recovery
point remain accepted loss exposure. Tests use injected temporary state/clock, not a production
guard-disable flag.

Restored accounts use fixed non-secret sentinel values and `status=needs_reauth`, but sentinel text
is not the safety boundary. `cookies_for_account` and every discovery/sync/direct-service path check
status first, return a stable reauthentication-required result, make zero decrypt/provider calls,
and never retry an account-linked private league as public. Explicit reauthentication atomically
replaces both sentinels before a fake-provider sync can proceed.

The two existing browser full-portfolio loops request one backup only after every attempted league
returns a clean sync and no league is skipped. A backup failure is presented as “sync data updated,
recovery point failed” rather than a clean operational completion. Individual league syncs are
covered by the hourly runner because the current backend has no full-portfolio transaction or job.
Phase 30 does not invent one.

The Status page adds a Recovery card with configured/available/age/state and a manual-backup action.
It uses absolute time plus a relative age, announces state changes, and leaves existing local reads
usable in stale state. No recovery math beyond display formatting is implemented in React.

## Explicit deltas and guarantees

| Area | Phase 30 delta | Evidence / accepted failure |
| --- | --- | --- |
| Source DB schema | **None.** No model, startup DDL, table, column, index, constraint, or source row migration. | Catalog SQL and per-table counts before/after backup and restore operations are identical. Artifact/scratch schemas are not source migrations. |
| API | Add the bounded recovery status and trigger routes; protected writes may return stable 503/409 recovery errors. Existing successful response shapes are unchanged. | Contract tests cover every admitted and blocked route; all ordinary GETs remain readable. |
| Provider behavior | No endpoint, host, view, filter, parser, retry, cache, or one-rps rule changes. The new gate runs before provider construction. | Provider spies prove zero calls when blocked and byte digests prove provider/parse modules unchanged. |
| Analytics | No formula, metric key, recompute, chart, export, or AI-grounding change. | Existing tests plus source digests for formula modules. |
| Credentials/private data | Raw cache, account credential values, owner SWIDs and key material are absent from artifacts, logs, APIs, evidence and handoffs. Other private facts exist only inside the encrypted operator-controlled repository and encrypted-at-rest ephemeral restore scratch. | Per-column synthetic canaries, recursive manifest allowlist checks, repository scan as supporting evidence, and independent source→bundle→restore assertions. |
| Configuration | Add typed local recovery repository/credential-command/enforcement settings. Network repository schemes and unsafe/missing production settings fail closed. | Configuration matrix and `doctor --json`; no secret or command output. |
| Scheduling | Add one native-macOS hourly launchd template and post-clean-browser trigger. APScheduler remains inert; Docker recovery is unsupported. | Fake-clock tests plus an operator-approved loaded-agent observation. Sleeping laptop/absent volume yields stale age rather than a false success. |
| Cost | **$0 AWS/month, $0 external recurring service, $0 software license line.** A physically separate volume is a one-time operator-supplied cost and must be recorded, not estimated. | Receipt/operator inventory evidence; if already owned, record $0 incremental rather than claiming the device was free. |

No live ESPN, FFC, nflverse, Anthropic, Cognito, AWS, or other network call is authorized. No raw
response, real fixture, real report content, credential, key, or private row may enter a test
fixture, committed evidence, agent handoff, stdout, or log. No AWS resource, public/private
deployment, database migration, retention deletion, merge, or commit is authorized.

## Restore drill contract

The phase-closing drill must restore one selected recovery point **from the off-device encrypted
repository**, never from the source database or a second laptop-local copy:

1. Make the OS automation credential unavailable. The operator supplies the independent
   break-glass secret through an interactive mechanism that never uses argv, environment, file,
   stdout, log, evidence or agent context. Query the repository, select the recovery point, start a
   monotonic timer, and restore its bundle into the fixed encrypted-at-rest private scratch root.
2. Verify repository integrity, encrypted manifest, format/full-catalog version, content hashes,
   adjusted row counts, and artifact canary before constructing the operational scratch database.
   Compare the independently calculated source oracle with decrypted bundle and restored database;
   backup and restore agreeing with each other is insufficient.
3. Create the normal application schema in a new scratch database and load safe rows in dependency
   order. Recreate account rows with fixed sentinels and `needs_reauth`; recreate an empty
   `raw_cache`; restore `teams.owner_swids_json` as `NULL`.
4. Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, exact named unique/index inventory, and
   negative duplicate probes. Exercise the four `metrics` partial-index NULL shapes—league,
   league/week, team, and team/week—in an isolated transaction and prove duplicate rejection.
5. Prove every restored account is `needs_reauth`; discovery, router sync, verify CLI and direct
   service sync return the stable safe result with zero decrypt/provider calls. Reauthenticate a
   synthetic restored account and prove a FakeEspn sync can proceed. Then start the pinned local
   application against the scratch database without background or network
   access. Execute the four Stage 0b reads: portfolio board, portfolio exposure, portfolio
   strategies, and 2025 WR opportunity charts. Compare stable response semantics and adjusted
   counts; do not write response bodies to evidence.
6. Prove the original account credential canaries, every `raw_cache` payload, owner SWIDs, and
   recovery secret are absent. Prove non-secret account configuration, AI-report count,
   metric-snapshot count, opportunity-import diagnostics, and FFC snapshot are present.
7. Stop the scratch app, delete the plaintext scratch directory, and record restore-ready and
   verification-complete durations. Repeat crash injection offline at each scratch stage and prove
   the next run safely scavenges only the owned root. The drill passes only if cleanup succeeds.

A failed manifest, canary, FK/unique/index probe, profiled read, exclusion check, source/off-device
identity check, or plaintext cleanup fails the phase. Do not repair the only source database during
a drill.

## Acceptance criteria and negative cases

All criteria are pass/fail against the frozen candidate:

1. A source table/column/type/nullability/default/primary key/foreign key/index/predicate/catalog-SQL
   shape not present in the versioned allowlist aborts before repository invocation; no partial
   snapshot is recorded successful.
2. Injected SWID, `espn_s2`, Fernet ciphertext, owner-ID, raw-payload, report-content, path, and key
   canaries appear in none of stdout, stderr, logs, state, manifest metadata, API responses, or
   committed evidence. Intended private report content exists only inside the encrypted bundle.
3. Database mutation during bundle construction, including uncheckpointed WAL rows, yields one
   transactionally consistent before-or-after view, never torn parent/child counts.
4. Missing/unmounted/same-device/read-only/full repository, unavailable/malformed credential
   command, unavailable repository binary, lock contention, repository corruption, interrupted
   backup, corrupted state, and failed plaintext cleanup all return stable safe errors and retain
   the prior `last_coverage_at` and `last_snapshot_at` values.
5. Fake-clock boundary tests cover 23:59:59, 24:00:00, clock rollback, naive/corrupt timestamps,
   no prior success, and recovery after a new success. At and beyond 24 hours, every router, CLI and
   direct-service entry to all four protected families is blocked before external construction or
   DB mutation; DB hashes remain unchanged and reads remain available.
6. A raw-cache-only change creates no changed-content snapshot. A protected-row change does.
7. Two independent CLI processes and an API-versus-CLI race create at most one repository
   invocation and one valid success record; kill-restart releases the inter-process lock safely.
8. The launchd command, canonical repository, credential-command handling, subprocess environment,
   and process output contain no secret. Malicious PATH precedence, metacharacters, inherited
   remote/proxy/cloud variables, symlink/mount swaps and secret-emitting fake binaries fail safely.
9. After the approved real retention application, the surviving actual off-device snapshot passes
   every break-glass restore-drill step and produces measured artifact bytes, backup duration,
   restore-ready duration, verification duration, and observed RPO age. Pre-retention restore
   evidence cannot close the phase.
10. Cross-origin form/simple POST, missing/foreign Origin, rebinding Host, forwarded-host spoof and
    missing action header return 403 before lock/repository work; the legitimate local UI succeeds.
    Fake-clock tests at 59.999/60.000 seconds plus concurrent and process-restart cases prove the
    shared rate bound; rollback/future/corrupt state fails closed and an early request returns the
    stable 429/`Retry-After` contract without repository invocation.
11. No-change/raw-cache-only runs verify the prior point, advance coverage without creating a
    snapshot, and never advance it when the repository is unavailable. Fake retention selection
    proves the exact survivor set. Wrong-tag, wrong-repository, zero-survivor and malicious-binary
    cases fail before deletion. Interrupted/failed real retention preserves both coverage
    timestamps, leaves enforcement false and blocks closure; the real repository reports
    enforcement only after approved enablement and the post-retention restore passes.
12. Operator review time is recorded against the 105-minute budget: 35 contract acceptance, 20 handoff
    verification, 35 finding adjudication, and 15 merge/close. Variance and review yield are
    reported; the threshold is capacity evidence, not a reason to weaken acceptance. Volume/tool/
    key setup, runner load, real backup, retention enablement and restore-drill minutes are measured
    separately and are not hidden inside the 105-minute review budget.

## Required commands and evidence

Offline candidate gates:

```bash
.venv/bin/python -m pytest tests/test_recovery.py tests/test_recovery_api.py
.venv/bin/python -m pytest tests/test_sync.py tests/test_ai.py tests/test_opportunity.py tests/test_phase23_draft_analytics.py
make test
make lint
cd web && npm run lint
cd web && npm run build
cd web && npm run e2e
```

Operator-approved local-device gates, run only after the destination and key custody are supplied.
`init`, runner installation and retention enablement are distinct mutations and may not be smuggled
inside `backup`:

```bash
.venv/bin/python -m api.recovery doctor --json
.venv/bin/python -m api.recovery init --confirm-empty-repository
.venv/bin/python -m api.recovery runner install --load
.venv/bin/python -m api.recovery backup --reason manual
.venv/bin/python -m api.recovery retention --dry-run --json
.venv/bin/python -m api.recovery retention --enable --apply
.venv/bin/python -m api.recovery restore-drill --snapshot latest --json
```

Evidence is recorded in `docs/evidence/phase-30-private-recovery.md` as secret-free E-ledger entries:

- `E30-01`: host/runtime, pinned repository-tool version, source/repository distinct-physical-parent
  proof,
  configuration safety, and actual one-time volume cost;
- `E30-02`: source catalog equality and independent allowlist/exclusion construction pass/fail;
- `E30-03`: actual artifact/repository bytes, safe-content digest result, backup duration and
  observed backup age;
- `E30-04`: restore-ready and verification duration plus pass/fail for independent adjusted counts/
  hashes, integrity/FK/unique/partial-index/read/reauth results, and scratch cleanup;
- `E30-05`: fake-clock and loaded-runner age/write-gate evidence, including unavailable-volume
  behavior; and
- `E30-06`: operator review minutes by category, separate setup/drill minutes, reviewer material
  findings/false positives, and findings per operator hour.

Each numeric result is labelled measured, derived arithmetic, assumption, externally verified, or
unmeasurable. Commands, UTC time, OS/architecture/runtime, source row counts, tool version, sample
count, validity domain, and limitations are mandatory. Exact artifact/content/table hashes, precise
private row counts, repository IDs, device serials and paths remain inside the encrypted manifest or
operator-private `0600` evidence outside the repository. Committed evidence contains only algorithm/
format versions, random run ID, aggregate bytes/timing and pass/fail; no private output is committed.

## Rollback and failure mode accepted

Expected `rollback_safe`: **yes for the source database and API schema; conditional operationally**.
The phase performs no source schema/data migration. The prior image can start and read the unchanged
database. Reverting the application removes the 24-hour guard and recovery status, so an operator
must explicitly accept that renewed loss exposure; encrypted recovery snapshots are never removed
as part of application rollback.

The accepted steady-state failures are missed hourly backups while the laptop sleeps or the volume/
credential is unavailable, blocking only the four protected write families after 24 hours while
administrative writes remain exposed, manual quarterly restore work, and loss of ESPN session
usability after restore until reauthentication. The phase does not accept a successful-looking
backup without an off-device restore, an unbounded plaintext scratch artifact, or a backup
containing credential values/raw cache.

## Non-goals

- No PostgreSQL, Alembic, RLS, tenancy, Cognito, AWS, S3, KMS, cloud backup, public deployment, or
  private cloud sync.
- No full-portfolio backend scheduler/queue, sync cadence change, provider instrumentation, cache
  relocation, metric-snapshot retention, or source-database/table/row deletion. Repository snapshot
  retention is Phase 30 scope only after the separate destructive-action approval above.
- No credential restoration, credential rotation, account ownership redesign, team-name
  pseudonymization migration, or removal of current owner identifiers from the source database.
- No disaster-recovery automation, automatic source failover, availability/SLA claim, or proof of
  recovery on a second machine.
- No real retention pruning without a later explicit destructive-action approval.

## Review, acceptance, and lease gate

Required pre-code reviewers are `cybersecurity` and `qa_test`, read-only and concurrent. Agent 1
reconciles their findings; neither reviewer nor Agent 1 accepts the result. Contract identity is:

```bash
shasum -a 256 docs/phase-30-private-recovery-baseline.md
```

The operator must accept that exact digest and resolve the volume/tool/key-custody prerequisites in
one batched gate. Any material edit after acceptance invalidates the digest and repeats contract
review. Only then may Agent 1 activate the exact `production_sre` write lease. No implementation,
tool installation, volume initialization, launchd load, backup, restore, network call, pruning,
commit, or merge is permitted by this draft.

## What a staff engineer would ask about this

1. **“Why not copy the SQLite file and let restic deduplicate it?”** Because the byte copy carries
   709 MB of raw private responses and replayable credential material. The logical bundle costs
   implementation complexity but makes cache and credential exclusion construction-time behavior,
   not a promise about downstream handling.
2. **“Can unattended encryption and off-laptop key custody both be true?”** Only with two deliberate
   custody paths: an OS-protected automation copy and an independent break-glass recovery copy.
   This protects disk-loss recoverability but not a compromised unlocked laptop with the backup
   volume attached. That residual runtime boundary is an explicit judgment call, not a cipher claim.
3. **“Does a 24-hour state-file check prove a backup exists?”** Not against a malicious local
   operator. It is an operational interlock. Repository verification, the encrypted manifest,
   distinct-physical-parent check, canary, and recurring restore drill provide the evidence; the
   local state only makes absence/staleness visible and gates the four named write families.

**Whiteboard cold:** RPO versus backup cadence; consistency versus file copying; logical versus
physical backup; recovery closure and dependency order; credential exclusion versus encryption;
automation-key versus break-glass custody; restore drills and canaries; fail-closed operational
admission; rollback that is data-safe but operationally regressive.

**Implementation detail to look up:** exact pinned `restic` version/flags, macOS launchd plist
syntax, APFS physical-store resolution calls, SQLite read-transaction/serialization APIs,
subprocess hardening, and temporary-directory permission mechanics.

## Amendment — trusted break-glass prompt broker

Status: **draft; independently reviewed acceptance required before implementation**.

This amendment supersedes only the interactive credential-transport mechanism in Restore Drill
step 1 and its corresponding runbook/tests. Every other accepted Phase 30 boundary and threshold
remains unchanged. The previously authorized manual restore was not started and is not authority to
implement or exercise this amendment.

### The Question

How can the operator supply the independent repository credential through the visible terminal
while Restic output stays captured, bounded and absent from evidence?

### The Lens

The binding constraints are credential custody, prompt authenticity, secret-free output,
subprocess termination and solo-operator usability. Candidate 23 inherits terminal input but pipes
and suppresses both Restic output streams. Offline reproduction retained the synthetic prompt only
in the captured stderr buffer. The current manual path is therefore unusable and must not be run.

### The Selection

1. **Expose Restic stderr through the terminal — rejected.** Smallest diff, but the child controls
   all displayed text and terminal control sequences; repository paths, identifiers, private
   diagnostics or unbounded output could bypass the existing capture/redaction boundary.
2. **Application-owned hidden prompt plus anonymous descriptors — selected.** The trusted parent
   displays one fixed prompt on the controlling TTY, disables echo, reads one bounded credential,
   then supplies it to each bounded non-interactive Restic child through a fresh one-use anonymous
   pipe referenced as `/dev/fd/N`.
3. **PTY proxy and output filter — rejected.** It could preserve Restic's native prompt, but adds
   prompt parsing, terminal-mode, signal, resize, escape-sequence and child-lifecycle failure
   surfaces without buying a required product capability.

### The Synthesis

Implement one trusted break-glass broker inside the private local recovery boundary:

- require an actual controlling TTY; missing/non-TTY input fails with
  `recovery_repository_error / Recovery credential is unavailable.` and never falls back to echoed
  stdin;
- emit exactly `ESPN Edge break-glass repository password: ` through the controlling TTY and read
  once with echo disabled under a **300-second monotonic timeout**. The bound deliberately accepts
  holding the recovery lock/scratch admission for at most five operator minutes; timeout, EOF,
  empty/invalid input and `KeyboardInterrupt` clean up and return the same stable secret-free
  `recovery_repository_error` rather than a traceback;
- accept only a non-empty value whose exact UTF-8 encoding is at most **1,024 bytes** and contains no
  NUL, CR or LF. The terminal line terminator is excluded by the hidden-input reader. Restore
  terminal state on success, timeout, EOF, invalid input, interruption or exception;
- hold the value only in a short-lived mutable in-process buffer, never in an argument value,
  environment variable, regular file, stdout/stderr, log, error envelope, evidence, Keychain or
  agent context; best-effort overwrite the buffer immediately after the final child or failure;
- for each of `check`, `snapshots` and `dump`, create a fresh anonymous pipe; write the complete exact
  UTF-8 credential bytes with **no added newline**; close the credential write end before `Popen` so
  the child is guaranteed EOF; and pass the credential read end alongside the existing pinned
  repository and recovery-lock authority descriptors—never the credential write end. Use Restic's
  `--password-file /dev/fd/N`, close the credential read end after bounded child termination, and
  create no regular credential file. `/dev/fd/N` is an ephemeral descriptor reference, **not**
  permission to create a credential file. This is the amendment's explicit interpretation of the
  existing no-file rule;
- run those children non-interactively with stdin closed and stdout/stderr still captured, bounded
  and never forwarded to the terminal. The operator enters the credential once, not three times;
- immediately before each descriptor becomes readable by a child, revalidate the pinned binary's
  device/inode/size/digest authority. The accepted same-UID race after final validation remains the
  existing local-compromise residual risk; eliminating it would require a root-owned installation
  or different custody design;
- on prompt timeout/EOF/invalid input or `KeyboardInterrupt`, restore terminal state, close every
  descriptor, clean owned scratch, release the recovery lock and raise the stable credential
  `RecoveryError` above. On child timeout, output overflow, drain failure, parent interruption or
  any other `BaseException`, kill the child's process group, boundedly drain/wait, close every
  descriptor, restore terminal state, clean owned scratch and release the recovery lock; expected
  operator interruption becomes the stable credential error, while an unexpected `BaseException`
  may be cleanup-then-reraised;
- never invoke the automation credential command or Keychain on this path.

**Judgment call:** transient secret residence in the Python process and best-effort zeroization are
accepted for this local FileVault-protected operator mode. Python cannot prove memory erasure. This
does not change the standing conclusion that same-UID malware or a compromised unlocked laptop is
outside Phase 30's protection boundary.

Cost, schema, API, provider, analytics and hosted-mode deltas remain **none / `$0/month`**. The
accepted failure mode is that no TTY, invalid input or any uncertain child lifecycle aborts the
drill; recovery availability is sacrificed before credential or private-output safety.

### Owned and forbidden paths

The sole `production_sre` writer may change only:

- `api/services/recovery.py`;
- the module documentation in `api/recovery.py` **only** to replace the now-false claim that Restic
  owns the prompt; no executable change in that file;
- directly corresponding cases in `tests/test_recovery.py` and
  `tests/test_recovery_integration.py`;
- the interactive instructions in `ops/private-recovery/README.md`; and
- append-only `docs/evidence/phase-30-private-recovery.md`.

`api/models.py`, `api/db.py`, source schema/data, provider/sync/AI code, `.env`, Keychain, both
external repositories, launchd state and every unrelated path are forbidden. No real restore,
credential entry, repository mutation, provider/network call, commit or merge is authorized during
implementation and offline review.

### Acceptance and negative cases

The frozen candidate must prove offline:

1. A PTY integration test displays the exact fixed trusted prompt once, accepts a synthetic hidden
   value, never echoes it, supplies three fresh one-use descriptors, and completes the synthetic
   check/snapshots/dump sequence with valid final JSON.
2. The synthetic secret is absent from child argument **values**, environment, regular files,
   stdout/stderr, errors, logs and evidence; no `--password-command` or automation credential
   invocation occurs. The constant argument `/dev/fd/N` may disclose only the inherited descriptor
   number.
3. Missing TTY, EOF, empty input, invalid UTF-8/NUL/CR/LF, more than 1,024 encoded bytes,
   terminal-state failure, the 300-second monotonic prompt timeout and Ctrl-C return exactly
   `recovery_repository_error / Recovery credential is unavailable.`, start no child where
   applicable, restore echo, release the lock and leave scratch immediately retryable. Fake-clock
   tests do not wait 300 wall-clock seconds.
4. A wrong credential yields one generic failure, no automatic reprompt and no later snapshots or
   dump call.
5. Child timeout, output overflow, long-lived descendant and parent interruption kill/drain the
   complete non-interactive process group within the accepted bound and close credential FDs.
6. Malicious fake-child stderr/stdout containing synthetic credential, path, identifier and ANSI
   canaries never reaches the terminal or stable error envelope.
7. Binary replacement before the prompt and between each Restic call fails before the next secret
   descriptor is delivered.
8. Existing backup, doctor, launchd, retention, topology, source-consistency, provider-boundary and
   scratch-cleanup tests remain green; their behavior does not become interactive.

Required focused commands are:

```bash
.venv/bin/python -m pytest tests/test_recovery.py tests/test_recovery_integration.py -q
.venv/bin/python -m pytest tests/test_recovery_api.py tests/test_hardening.py -q
.venv/bin/ruff check api tests
```

After the candidate freezes, QA and Cybersecurity independently review the exact candidate digest.
Only a reviewed offline pass allows a **new** operator authorization for one manual
`make recovery-restore`; the prior unused authorization does not automatically carry across the
mechanism change.

Expected `rollback_safe`: **yes**. Reverting restores Candidate 23's hidden-prompt defect but does
not change source data, repository data, schema, API or configuration. A rollback therefore blocks
restore closure rather than corrupting data.

## Amendment — bounded POSIX signal-handoff residual

Status: **draft; independently reviewed digest acceptance required before the real restore drill**.

This amendment changes no implementation, test, schema, API, configuration, provider behavior,
repository state or monthly cost. It records the operator's selected disposition of the exact
Candidate 29 residual found during independent security review. The selection is not authoritative
until the operator accepts this amended contract's immutable SHA-256 digest. It does not authorize
a real restore, credential entry, repository/device mutation, provider/network call, commit, merge
or spend.

### The Question

What failure remains when a guarded POSIX signal arrives after the final empty pending-signal check
but before Python restores the calling thread's prior signal mask?

### The Lens

Evaluate the residual against the private-local, single-operator, FileVault-protected practice
topology; credential and private-data containment; deterministic cleanup; implementation
complexity; and the marginal evidence another custom signal cycle could produce.

### The Selection

1. **Accept the bounded residual for Phase 30 — selected judgment call.** Candidate 29 blocks the
   complete guarded signal set while restoring every original handler and resetting guard context,
   then consumes signals observed as pending before restoring the prior mask. A signal may still
   arrive in the final check-to-unmask interval and be delivered to the restored original/default
   handler. At that point the recovery child/process group is gone, password descriptors are
   closed, the mutable credential buffer has been best-effort zeroized, terminal and signal
   handlers/context are restored, and the recovery lock remains held by the outer restore-drill
   context. Ordinary exception unwinding closes it; default process termination causes the OS to
   release it after the already-terminated recovery children can no longer retain a copy. Default
   termination can still bypass the stable error result and outer scratch cleanup. FileVault limits
   confidentiality exposure from scratch retained by that abrupt termination; the existing bounded
   native scavenger is the recovery path on the next run.
2. **Add another Python signal-mask cycle — rejected.** A user-space pending check followed by mask
   restoration retains a check-to-unmask interval. Moving the check or adding another handler swap
   moves the race and adds lifecycle complexity; it does not prove elimination.
3. **Redesign restore execution around a separate supervisor/process boundary — deferred.** This
   could reduce dependence on Python's handler/mask handoff, but it materially changes the accepted
   mechanism, requires a new implementation contract and full review, and is disproportionate to a
   local practice-project restore path whose sensitive child and credential cleanup already finish
   before the residual window.

### The Synthesis

Phase 30 accepts the following narrowly bounded failure mode: a first guarded
`SIGHUP`/`SIGQUIT`/`SIGTERM`/`SIGTSTP` delivered in the final pending-check-to-prior-mask-restore
window may invoke the caller's restored original/default handler instead of returning the stable
credential-unavailable result. This may terminate the CLI before final result serialization and
outer scratch cleanup. It may not leave a live recovery child/process group, open password
descriptor, un-zeroized mutable credential buffer, modified terminal, installed recovery handler,
or held recovery lock; any evidence of those effects reopens implementation and fails the phase.

This acceptance is valid only for the local private operator mode with FileVault verified on. It
does not apply to hosted/public mode, multi-user custody, an unencrypted scratch volume, a daemon
service, or any future cloud restore path. A topology change, evidence that cleanup has not
completed before mask restoration, a retained plaintext canary after the next-run scavenger, or a
standard-library/runtime mechanism that removes the handoff window requires revisiting the
decision.

The accepted consequence is availability and result-determinism loss under a precisely timed local
signal, not credential disclosure by design. Cost, schema, HTTP API, provider, analytics and hosted
mode deltas remain **none / `$0/month`**. Candidate 29's frozen manifest and previously executed
offline evidence remain the implementation evidence; this contract-only amendment authorizes no
Candidate 30.

After one independent reviewer verifies that this text matches the frozen Candidate 29 evidence
and does not broaden the accepted boundary, Agent 1 freezes the amended contract digest for human
acceptance. Only that acceptance permits Agent 1 to request a fresh, separately bounded operator
authorization for one real `make recovery-restore` drill.
