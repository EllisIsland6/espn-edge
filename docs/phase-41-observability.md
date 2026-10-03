# Phase 41 (narrowed) — observability: making silence legible

**Scope.** Phase 41's change surface names eleven things; four of its five
acceptance clauses are provable offline and the rest need an AWS account.
This phase did the offline four and names the rest as deferred. Nothing here
called AWS, Anthropic, ESPN or the network.

---

## The probe that set the scope

Before writing anything I ran the worker loop against an empty queue and
counted every durable row that changed.

```
drain() returned: []
durable rows changed by an idle tick: NOTHING
tables that could hold worker liveness: NONE
```

So a worker that was up and idle and a worker that had died an hour earlier
produced **byte-identical database state**. No alarm could have told them
apart, because there was nothing to tell apart.

That one measurement decided the phase. Everything below is downstream of it.

---

## The rule

A counter written only when it is non-zero has no datapoint during an outage.
An alarm on it sits in `INSUFFICIENT_DATA`, and CloudWatch's default reading of
`INSUFFICIENT_DATA` is *not alarming*. So **silence reads as health**, which is
exactly backwards: silence is the thing being detected.

Two mechanisms make the omission impossible rather than discouraged:

- `observability.report()` takes a whole snapshot and **requires every one of
  the ten series**. A missing key raises `IncompleteReport`. "Nothing happened
  so I published nothing" is a crash here, not a gap in a graph.
- `emit()` publishes `0` like any other number. There is no falsy check in the
  module, and removing that property is one of the control removals below.

---

## What was built

| File | What it is |
|---|---|
| `api/services/observability.py` | The ten series, the sink protocol, the dimension policy |
| `api/services/alarms.py` | Ten alarm specs as data, and `evaluate()` |
| `api/services/heartbeat.py` | `record_tick` / `staleness` / `idle_age` / `take_progress` |
| `api/services/snapshot.py` | Collects the ten; `report_degraded` for a dead database |
| `api/models.py` | `WorkerHeartbeat` |
| `alembic/versions/0011_worker_heartbeats.py` | One table, one index |
| `docs/sprint-9/faults/harness.py` | Five scenarios, ten pairwise comparisons |

Tests: `test_observability.py` (35), `test_alarms.py` (21),
`test_heartbeat.py` (20), `test_worker_heartbeat.py` (6),
`test_snapshot.py` (18), `test_recovery_format_drift.py` (4) — 104 new, and
436 across every file this phase touched, all passing.

The migration was probed up and down against a throwaway database before
anything depended on it: fresh to `head`, the table present with `owner` as
the primary key and `last_claimed_at` nullable, `downgrade -1` removing it
cleanly, and `upgrade head` again.

### The two-timestamp design

`worker_heartbeats` carries `last_seen_at` *and* `last_claimed_at`. The first
advances on every tick including the empty ones; the second only when work was
picked up. A fresh first beside a stale second is the live-but-not-claiming
case — a process looping and achieving nothing — and **no single timestamp can
express it**. Removing the `if claimed:` condition is the one change that
destroys the signal outright, so both the unit suite and the fault harness
require it.

### What "we could not measure" publishes

When the database is unreachable, nine of the ten series cannot be measured.
Three options, two wrong:

- **Zeroes for the nine.** The worst: `queue_depth=0` and
  `queue_oldest_age_seconds=0` are the *healthiest* readings those series take,
  so a total outage would publish a clean bill of health. The defect this phase
  exists to remove, moved one layer down where no alarm setting could catch it.
- **A large sentinel for the nine.** Pages for nine conditions nobody observed,
  with nine false runbook lines. An operator chasing `outbox-undelivered`
  during a database outage is being actively misled.
- **`db_reachable=0` and leave the nine absent.** Correct, and correct *only
  because* every alarm treats missing as breaching — which is what buys the
  right to say "unknown" and still get paged.

A published `0` and a gap are therefore different observations, and that
distinction is what separates a stopped host from a stopped database: both
light all ten alarms, and only one of them had something alive to report the
failure.

### The dimension policy

`env` and `kind` only. `tenant_id` is **named and refused** — not because it is
useless but because a per-tenant dimension multiplies the series count by the
tenant count, and the bill and the cardinality limit both arrive without
warning. `job_id`, `user_id`, `league_id`, `owner`, `trace_id` and `session_id`
are refused with their reasons. Dimension *values* must match a short closed
token, because free text in a dimension is how an error message becomes part of
a metric name — and a metric name is not redactable after the fact.

Structurally: `Sample` has five fields and there is nowhere in it to put a
payload, an error string or an identifier. Asserted over the dataclass's own
fields, so a field added later without thought fails the test.

---

## Control removals

A passing test is not evidence a control works. The evidence is that it fails
when the control is removed.

### `observability.py`

| Control removed | Result |
|---|---|
| `emit` skips a zero | **2 failures** |
| `report` does not check for missing series | **1 failure** |
| Dimensions default-allow instead of default-deny | **13 failures** |
| A bool counts as a number | **1 failure** |
| `report` publishes what it has before raising | **1 failure** |

### `alarms.py`

| Control removed | Result |
|---|---|
| One alarm flipped to `notBreaching` | **3 failures** |
| `evaluate` reads a gap as healthy | **5 failures** |
| `evaluate` returns `INSUFFICIENT_DATA` | **4 failures** |
| The rendered API call drops `TreatMissingData` | **1 failure** |
| `treat_missing_data` gets a default | **collection error** |
| Statistic keyed on unit again | **2 failures** |
| One series loses its alarm | **1 failure** |

The default-value removal is the strongest guard in the set: adding a default
puts a non-defaulted field after a defaulted one, so **Python itself refuses to
define the class**. The catalog test cannot be made unfalsifiable without the
module failing to import.

### `heartbeat.py` / `worker.py`

| Control removed | Result |
|---|---|
| `last_claimed_at` advances on every tick | **4 failures** + harness fails |
| `staleness` returns `timedelta(0)` for an empty table | **1 failure** |
| `take_progress` does not advance the watermark | **4 failures** |
| The watermark guard dropped | **1 failure** (after the test was fixed — below) |
| The worker ticks only when it claimed something | **4 failures** |
| The heartbeat shares the job's transaction | **1 failure** |

### Against the fault harness

| Control removed | Harness |
|---|---|
| `emit` skips a zero | **FAILED** — `db_reachable` read `absent`, expected `0.0` |
| `evaluate` reads a gap as healthy | **FAILED** — 5 claims |
| `last_claimed_at` always advances | **FAILED** — 2 claims |

---

## The fault harness

`docs/sprint-9/faults/harness.py`. Five scenarios, and a claim stronger than
"the alarms fire":

> The four faults produce four observations that are pairwise distinct, and a
> healthy baseline is distinct from all four.

A harness where every fault lights every alarm has proved nothing — it is
indistinguishable from a catalog that always fires. So all ten pairs are
compared and any pair with the same signature is a failure.

```
healthy             0 alarms   db_reachable=1.0    1 owner    ever_claimed=True
host_stop          10 alarms   db_reachable=absent 1 owner    ever_claimed=True
crash_loop          2 alarms   db_reachable=1.0   10 owners   ever_claimed=False
db_unreachable     10 alarms   db_reachable=0.0    0 owners   ever_claimed=False
live_not_claiming   2 alarms   db_reachable=1.0    1 owner    ever_claimed=False

PASSED -- 5 scenarios, 10 pairs all distinct
```

`host_stop` and `db_unreachable` both light all ten. The field that separates
them is the one the zero-versus-absent rule produces: `absent` means nothing
was alive to report, `0.0` means something ran and could not reach the
database. `crash_loop` and `live_not_claiming` both light two; the
short-lived-owner count separates them — a restarting container takes a new
task identity each time, so the table fills with rows that ticked once.

**Not proven here:** that CloudWatch computes what `alarms.evaluate` computes.
That is an AWS claim, needs an account, and is Phase 43. `evaluate` is written
to AWS's documented rule for `treatMissingData=breaching`. Said plainly rather
than left implied.

---

## Eight defects found, seven of them mine

1. **`oldest_due_age` lived in `schedules.py` while querying the `jobs`
   table** — its own docstring said "the oldest job that is due". So there was
   no measure of schedule lateness anywhere, and
   `schedule_overdue_age_seconds` was about to be wired to the job queue age:
   two series publishing one number under names claiming different things, with
   every test green. Moved to `jobs.py`; wrote the `oldest_overdue_age` that
   was missing; added
   `test_the_two_age_series_measure_different_things`, which builds a world
   where the correct answers are unequal and requires that they are.

2. **The alarm statistic was keyed on `Unit`, not `Kind`** — so every `COUNT`
   series got `Sum`, including `queue_depth` and `jobs_poisoned`, which are
   gauges. A depth of 40 sampled five times in a period would have evaluated as
   200 and alarmed on nothing at all. **My test asserted the same wrong rule
   and passed.** Rewritten to assert the property, and to require both branches
   are reachable from the catalog.

3. **`publish` threaded the clock into the measurement but not the
   timestamp** — `report` fell through to `datetime.now(UTC)`, so every sample
   was stamped with wall-clock time while describing state read at clock time.
   Fifteen snapshot tests passed, because not one looked at `Sample.at`. The
   harness found it on its first run. It matters outside tests: CloudWatch
   buckets by timestamp.

4. **The harness's own window function took the last N *samples* instead of
   the last N *periods of time*.** A host that published twice and then died
   produced `[None, 1.0, 1.0]` — one gap among two healthy samples — and every
   alarm read OK. **The harness reported a stopped host as healthy.** Fixed to
   evaluate a wall-clock grid, which is what CloudWatch does: silence occupies
   periods.

5. **The harness passed while the distinction it rests on was not exercised.**
   `scenario_db_unreachable` had no clock, so its samples landed outside the
   grid and read `absent` — the same as `host_stop`. The two were separated by
   a coincidence in their alarm counts, not by the zero-versus-absent reading
   the docstring credits. Found by reading the output rather than the verdict.
   Now asserted per scenario.

6. **`test_two_reporters_cannot_report_the_same_ticks_twice` passed with the
   guard deleted.** The two reporters ran sequentially, so the second read the
   already-advanced watermark and got the right answer from fresh state — the
   guard was never exercised. Same shape as the Phase 39 claim-race test. Both
   reads are now forced to happen before either write, and the assertion is on
   the sum: two ticks may be reported twice in total, not four times.

7. **One control removal left the harness passing** — `last_claimed_at`
   advancing on every tick. The unit suite caught it (4 failures) but the
   harness is the artefact that claims to prove the live-but-not-claiming
   signal *path*, and the path runs through that column. Added `ever_claimed`
   to the signature and asserted it per scenario; the removal now fails the
   harness too.

8. **My own first explanation of the recovery failures was wrong**, and I had
   already committed it. The drift test's docstring said all 89 failures were
   one schema-drift cause. Counting them showed four causes, and the schema
   drift itself had three independent parts — removing the eight unknown
   tables still fails, because three older tables gained a `tenant_id` the
   allowlist does not list. Found by reading the failure messages instead of
   the total. Corrected in the same session, with a control removal on each
   part.

Two comments also overclaimed what their code did and were corrected rather
than left to talk a reviewer out of reading: `evaluate`'s padding comment
(a short history is padded with breaches, but that does **not** force an
alarm — three required datapoints and one healthy sample is OK), and
`take_progress`'s guard comment (the guard stops a second *reporter*;
conservation across windows comes from writing back the total that was read,
which is a different mechanism and survives different edits).

---

## A pre-existing breakage, found, counted, and then fixed

**89 tests failed in three recovery files when Phase 41's work landed, and
none of them because of it** — measured by removing `WorkerHeartbeat` from the
models and re-running: identical both ways.

They were not one failure wearing eighty-nine hats, which is what my first
pass claimed before I counted the causes:

| Failures | Cause |
|---|---|
| 70 | recovery format v1 no longer matched any database this app can build |
| 10 | `<repo>/.venv/bin/python` missing or unresolvable — **environmental** |
| 1 | tenant seeding collision in the oracle's own fixture |
| 8 | assorted, downstream of the two above |

### The format drift had three independent parts

Each measured by calling `validate_catalog` on a database built from the
current models and watching which of its four checks refused:

1. **Eight tables the allowlist had never heard of** — `tenants`, `users`,
   `memberships`, `app_sessions`, `jobs`, `schedules`, `outbox`,
   `worker_heartbeats`.
2. **Three allowlisted tables had gained an unlisted column** — `accounts`,
   `leagues` and `raw_cache` each carry a `tenant_id` from migrations
   0003/0004. **Fixing part one alone still failed**, which is the part my
   first explanation missed: a fix that changes nothing visible is
   indistinguishable from no fix.
3. **The pinned catalog digest matched nothing buildable.** Both documented
   recipes were run and neither reproduced `_FORMAT_V1_CATALOG_SHA256` even
   with every Phase 36–41 table excluded.

And a fourth thing, found only by building the schema the *documented* way:
`catalog_spec` read every table out of `sqlite_schema`, which in a **migrated**
database includes alembic's own `alembic_version`. The allowlist never listed
it, so `alembic upgrade head` could not validate under v1 at all. It went
unnoticed because the suite builds with `create_all`, where that table does
not exist — the suite exercised one provenance and the operator ran the other.

The same hazard bit `leagues`: `tenant_id` is **appended** in a migrated
database and **fifth** in a `create_all` one, because alembic 0003 added it
with a batch rebuild while the model declares it after `account_id`. A
single-order allowlist passes exactly one of the two, silently, in whichever
direction nobody tests.

### Format v2

- Eight tables listed, three column tuples corrected, `leagues` admitted in
  both real orders, `alembic_version` out of the fingerprint.
- Three digests re-pinned from three real databases.
- `FORMAT_VERSION`, `BUNDLE_FILENAME` and `RECOVERY_CANARY` bumped to 2 —
  leaving them at 1 would make the number in every bundle a false statement.
  `RECOVERY_TAG` and the two scratch sentinels stay at `-v1` on purpose:
  changing the Restic tag orphans every existing snapshot. A test names those
  three exclusions, because the first version of it said "no v1 marker
  survives anywhere" and failed on all three.
- **What a bundle carries is now a named set with a reason per entry**, not
  four scattered `raw_cache` special cases. Out: `raw_cache` (private
  payloads), `users` (`email` is unique, so the single-sentinel substitution
  `accounts` uses for credentials would violate the constraint on the second
  row, and a per-row stand-in would be fabricating identity), `memberships`
  (every row points at a `users` row that is not in the bundle),
  `app_sessions` (restoring them re-admits whoever was signed in hours ago),
  `jobs` (a restored lease names a worker that does not exist), `outbox` (a
  notification cannot be un-sent), `worker_heartbeats`. In: `tenants` (three
  bundled tables reference it) and `schedules` (configuration the operator
  created; dropping it means nothing ever syncs again, with nothing saying so,
  and `materialize_due` keys on `(schedule, slot)` so a restored schedule
  cannot double-execute).
- **The restore starts from empty.** `create_all` seeds the default tenant, so
  the bundle's own tenant row collided on the primary key — which is what 24
  integration tests and the oracle reported once the allowlist let them get
  that far. Rows are deleted in reverse dependency order so no cascade has
  anything to reach, every table is asserted empty, and a target holding
  anything else is refused up front, so the clearing step can only remove rows
  the schema seeded.

`tests/test_recovery_format.py` (20 tests) replaces the drift file, which had
said to delete it in the change that re-versions the format and which failed
three of its seven assertions once this landed — the test telling me it was
done. The central new test is the one that did not exist for six phases: build
every shape the application can produce, hash it, require the digest to be
pinned — and require the reverse, that no pinned digest is one nothing
produces, which is exactly what v1 became.

**Control removals, 8:** a table dropped from the allowlist (2 failures); one
pinned digest altered by a single character (3); the `leagues` dual-order
accommodation removed (1); `alembic_version` back in the catalog (4);
`memberships` bundled so its foreign key dangles (2); the not-empty refusal
removed (1); the clearing step removed (11 across two files); `FORMAT_VERSION`
left at 1 (1).

One of those initially left my own test green, for a worse reason than usual:
it passed `b"{}"` as the bundle and asserted "either error code", so execution
never reached the line under test — the bundle is parsed before the target is
looked at. **A test that cannot reach its subject is not a weak test, it is a
different test.** It now builds a valid bundle and asserts the exact code.

### What is left: nineteen skips, no failures

Seventeen tests need `<repo>/.venv/bin/python`. `api/recovery.py` launches the
backup job through the *lexical* venv path deliberately — resolving the symlink
selects the base framework interpreter and loses the venv's package search path
under launchd — and that symlink points at
`/Library/Frameworks/Python.framework/.../python3.14`, which resolves on the
operator's Mac and nowhere else. The exact set was found by measurement, not
reading: `api.recovery.ROOT` was pointed at a tree whose `.venv/bin/python`
does resolve and the two runs were diffed — **15 cleared, 2 cleared only with
the app's dependencies in that venv, 1 did not clear at all**. Three
conditions, three treatments, rather than one marker over all of them.

The skip is guarded.
`test_the_lexical_venv_skip_condition_matches_the_production_check` compares
the predicate against `_launchd_plist`'s own refusal and is never skipped
itself, so a predicate that drifted from the code it stands in for fails the
build instead of hiding seventeen tests. Hardcoding it to "usable" fails that
guard *and* makes the seventeen run and fail — the direction that matters,
since that is where a skip would conceal something real.

### The eighteenth: a race that could not be won

`test_escaped_credential_descendant_is_authority_free_and_does_not_hold_lock`
forked a descendant that slept 1.2s, then asserted further down that it had not
yet exited. **The race was unwinnable by construction, not lost on a slow
machine:** the test sets the broker timeout to 1.5s, so `run_backup` cannot
return in less than that, and 1.5 > 1.2. Measured at 2.085s against the 1.2s
sleep, with the child gone 0.885s before the assertion ran.

What that assertion exists *for* is the liveness precondition of the next line
— a dead process holds no locks, so taking the recovery lock after the orphan
had exited would prove nothing. So the ordering is controlled now instead of
hoped for: the descendant waits on a release file, the test asserts liveness
directly with `os.kill(pid, 0)`, takes the lock while the orphan is provably
running, then releases it and requires a prompt exit — which a zombie could not
answer, making the handshake the definitive proof. The child's 30s backstop
writes a *different* word, so an exit on the deadline fails loudly rather than
passing as a timely one.

And the scan it depends on had no instrument check, which turned out to matter.
Removing the `close_fds=True` that holds the authority-free property does fail
the test — but with the marker file **missing** rather than reading `"bad"`,
because the extra descriptors break the broker before the credential command
runs. So the removal proved the spawn is fragile and left the probe unproven. A
probe that cannot report the defect is not a probe, so
`test_the_descriptor_identity_scan_can_actually_report_authority` proves it both
ways with no recovery code involved: an inherited descriptor is detected, a
closed one is not.

Control removals, 5: the child exits immediately (fails); the child ignores the
release (fails); `close_fds=False` at the credential broker spawn (fails); the
scan narrowed to fd 0..2 (fails); `keep_open` ignored (fails). Recorded as
well: `close_fds=False` at the *other* spawn site passes, correctly — it is a
different subprocess.

---

## Verified from a clean clone

Not from the worktree. The close-out's second most useful habit is "before
believing a green suite, run it somewhere that is not where you built it" —
last time that habit found three tests that had been green for months because
a gitignored `.env` happened to exist.

`git clone --no-hardlinks` of `e83be9d` into a scratch directory with no
`.env` and no `.venv`, every required setting supplied from the environment:

| Check | Result |
|---|---|
| 1203 tests outside `tests/test_recovery.py` | **0 failures**, 2 skipped |
| `tests/test_recovery.py` | 278 tests, 261 passed, 17 skipped, **0 failed** |
| **Total** | **1481 tests, 0 failures, 19 skipped** |
| `docs/sprint-9/faults/harness.py` standalone | exit 0, 10 pairs distinct |
| `alembic upgrade head` | 0011 |
| `ruff check api tests` | clean |

**The clone earned its keep twice.** First it found nine integration tests that
pass in the worktree and fail in a clone, because
`_run_operational_restore_verifier` spawned a subprocess with a hand-built
environment missing three *required* `Settings` fields — and `Settings` loads
`ROOT/".env"` by **absolute path**, so the hand-built environment never
isolated anything. Off the operator's Mac the subprocess printed a traceback
instead of JSON and the caller reported "Restored application verification
failed": a missing setting, presented as data loss, during a restore. The same
mistake was recorded a phase earlier in two *tests*; this time it was in
production code.

Then it found a one-word filename — `verifier-telemetry.md` — tripping the
provider-wiring scan three files away. Every targeted run was green. Running
the suite you edited is a narrower habit than running the suite.

---

## Deferred, with reasons

| Acceptance clause | Status |
|---|---|
| Local fault harness: four signal paths | **done** |
| Every self-published alarm treats missing as breaching | **done** |
| Zero-valued progress distinct from no sample | **done** |
| Ten-metric telemetry self-test contract | **done** (the catalog and its tests) |
| Certificate expiry / credit-guard forced trip | **deferred** — AWS; Phase 43 |
| OpenTelemetry traces | **deferred** — a collector endpoint is infrastructure |
| EC2/RDS/ECS events and metrics | **deferred** — AWS |
| Audit/snapshot/report retention | **deferred** — S3 lifecycle; AWS |
| Recurring cost inside the $0.50 reserve | **partial** — no alarm uses a
  high-resolution period, which is the offline half; the bill is Phase 43 |

Also unchanged from the close-out: the OIDC callback is blocked on PyJWT, which
cannot be installed in this environment, and hand-rolling JWT verification
(`alg:none`, key confusion) is not something to ship.
