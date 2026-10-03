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

## A pre-existing breakage, now named

**89 tests fail, in three recovery files, and none of them because of Phase
41** — measured, not assumed: `WorkerHeartbeat` was removed from
`api/models.py`, the files re-run, and the counts were identical both ways.

They are also not one failure wearing eighty-nine hats, which is what my first
pass at this claimed before I counted the causes:

| Failures | Cause |
|---|---|
| 70 | recovery format v1 no longer matches any database this app can build |
| 10 | `<repo>/.venv/bin/python` missing or broken — **environmental** |
| 1 | tenant seeding collision in the oracle's own fixture |
| 8 | assorted, downstream of the two above |

Outside those three files: **1155 tests, 0 failures.** Suite total **1462**, of which 307 are in the three recovery files.

Only the first group is a code problem, and it has **three independent
parts**, each measured by calling `validate_catalog` on a database built from
the current models and watching which of its four checks refused:

1. **Eight tables the allowlist has never heard of** — `tenants`, `users`,
   `memberships`, `app_sessions`, `jobs`, `schedules`, `outbox`,
   `worker_heartbeats`.
2. **Three allowlisted tables gained an unlisted column** — `accounts`,
   `leagues` and `raw_cache` each carry a `tenant_id` from migrations
   0003/0004. Removing the eight unknown tables is *not* enough to pass, and
   this is the part my first pass missed.
3. **The pinned catalog digest matches nothing buildable.** Both documented
   recipes — `create_all` over the current models, and an older
   `opportunity_weeks` brought forward by the additive ALTERs — were run and
   neither reproduces `_FORMAT_V1_CATALOG_SHA256` even with every Phase 36–41
   table excluded.

The control is fail-closed and working. It has simply not been updated.

The ten environmental failures are the ones `docs/CLOSE-OUT.md` already
describes: `api/recovery.py` launches the backup job through
`ROOT / ".venv/bin/python"` — deliberately the *lexical* venv path, so a
launchd plist survives a Python upgrade — and in a sandbox where that venv was
never created the symlink dangles. Those tests pass on a machine with a real
repo venv.

**The number 89 appears in `CLOSE-OUT.md` attached to the environmental cause
alone. That is a coincidence of arithmetic, not a shared cause** — worth
stating plainly, because conflating them would send the next person to rebuild
a venv and find 79 tests still red.

`tests/test_recovery_format_drift.py` turns 89 opaque errors into seven
explicit assertions: one per part of the drift, plus the environmental cause
stated structurally so its result does not depend on the machine it runs on.
Each fails in both directions — a new table or column added without a thought
about recovery fails it, and so does removing one because the format was
re-versioned, at which point the file should be deleted in that change.
Control removals: emptying the column-drift list fails part two; dropping one
table from the table list fails part one.

### Why it is not fixed here

Re-versioning means extending the allowlist with eight tables and three
columns, deciding bundled-or-excluded for each, re-pinning two digests from
real databases, and re-reading what a restore would then do to tenant
isolation. The comment above `_FORMAT_V1_CATALOG_SHA256` requires a "reviewed
recovery-format version" for exactly this, and no real restore is currently
authorised.

**Attempted and backed out:** adding `worker_heartbeats` to
`EXPECTED_TABLE_COLUMNS` to clear the error. It did not clear it — parts two
and three were still there — and it would have asserted membership in a frozen
format nobody reviewed. Making one table's claim true while ten other things
stay false is not progress, it is a quieter failure.

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
| 1155 tests outside the three recovery files | **0 failures** |
| `tests/test_recovery.py` | 275 tests, 64 failures |
| `tests/test_recovery_integration.py` + `_oracle.py` | 32 tests, 25 failures |
| **Total** | **1462 tests, 89 failures** — identical to the worktree |
| `docs/sprint-9/faults/harness.py` standalone | exit 0, 10 pairs distinct |
| `alembic upgrade head` | 0011 |
| `ruff check api tests` | clean |

One nuance the clone exposed: the venv-attributable failures are **7 here and
10 in the worktree**, because the worktree has a *dangling*
`.venv/bin/python` symlink while the clone has no `.venv` at all, and those
two take slightly different error paths. The total is 64 either way. Worth
recording because "10 of the 89" is only true of one of the two conditions.

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
