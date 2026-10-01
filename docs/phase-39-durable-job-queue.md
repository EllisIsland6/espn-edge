# Phase 39 (narrowed) — the durable job queue core

Source plan: Phase 39 `durable-work-orchestration`, 7 sessions / 135 operator minutes. Acceptance is
explicitly offline — fake clock, fake provider, **"No network in CI."**

**Built:** the job table, idempotent enqueue, atomic leases, crash recovery, retry with cooldown,
poison quarantine, non-retryable failures, and queue depth. **Deferred and named:** schedules, the
outbox, weighted tenant fairness, one-active-job-per-tenant, the global provider permit, snapshot
retention. Those are policy on top of a queue; this is the queue.

## Why it exists

APScheduler holds its queue in memory. A restart loses whatever had not run, and a second process
runs everything twice. Phase 34's capacity arithmetic already assumes a worker process that does not
exist yet. The table is the queue instead: a crash loses at most one attempt's work, and the row is
the only thing that decides what happens next.

## The one idea

**Every transition is a conditional UPDATE whose row count is the answer.** Read-then-write loses
races silently — two workers both see `queued`, both write `leased`, and the job runs twice, which
for a sync means two provider calls against a rate limit that allows one.

Time is injected. A queue's hard cases are all about time, and tests that sleep are slow and flaky
while tests that cannot control time do not reach those cases at all.

## What is proven

`tests/test_jobs.py` — 18 tests, every one with the clock under its control.

Idempotency per tenant (the same key in another tenant is a different job — a global key would
collapse two tenants syncing the same ESPN league, the mistake `uq_league_season` made). A future job
is not claimable until due. Claiming leases and counts the attempt. **A crashed worker's job comes
back when its lease expires**, with nothing detecting the crash. A retryable failure returns with a
cooldown; a non-retryable one does not come back and does not consume the retry budget; an exhausted
budget quarantines rather than looping. Long errors are truncated, because `last_error` is a database
column and the thing that fails is usually a provider response.

## Three defects found, each by a test that should have failed

**1. The lease-expiry path raised `TypeError` inside SQLAlchemy.** Not in code I wrote. SQLAlchemy
2.0 defaults ORM-enabled UPDATEs to `synchronize_session="auto"`, which tries the `evaluate` strategy:
it re-checks the UPDATE's WHERE criteria **in Python** against objects already in the identity map.
Those carry the timestamp as SQLite returned it — naive, because SQLite has no type that carries an
offset — while the bound `now` is aware. So `lease_expires_at <= now` raised, on the lease-expiry path
only, and only once an object happened to be loaded. `synchronize_session="fetch"` selects the
affected primary keys and compares nothing in Python. **Same Phase 35 trap, third surface, and this
one is inside a library.**

**2. `complete` read the lease owner off the job object.** The ORM had already refreshed that object
to whichever worker reclaimed the job, so a stalled worker's write matched its own guard and
succeeded. `complete` and `fail` now take the owner explicitly and re-read the attempt count inside
the transaction. **A worker knows its own identity; anything it reads back from a shared row is a
fact about the world, not about itself.**

**3. The test for the claim race passed with the guard deleted.** This is the important one. Removing
the state guard from the claim UPDATE left all 17 tests green — because two *sequential* calls are
separated by the candidate SELECT, which already filters on state, so the second call finds nothing
and returns before the UPDATE runs. The guard was unobservable.

Fixed by extracting the candidate lookup so a test can interpose in the one window the guard exists
to close: worker 1 picks a row, worker 2 takes it while worker 1 is still deciding, worker 1's UPDATE
matches zero rows. That test asserts the interleaving actually happened, because a race test that
silently fails to race is worse than no test. Both cases are kept, with the sequential one labelled
as proving less than it looks.

## Scope

`jobs` is classified in `tests/test_tenant_classification.py` as tenant-scoped **with no RLS policy,
deliberately**: one worker process serves every tenant, so a policy on the claim would have to be
bypassed to work at all. Enforcement for jobs is the tenant the worker binds before it *runs* one.
Recorded so "no policy" reads as a decision.

Migration `0008`, reversible. Its downgrade discards queued work, which is the honest meaning of
reversing "add a queue" and is stated rather than guarded.

## Suite

**993 passed / 0 failed**, ruff clean.

---

# Addendum — claim policy, and a control-removal method that needed fixing

Three rules now decide which runnable job is offered next. Each exists because
of a specific failure, and each is enforced **twice**: once when the candidate
list is built, once in the claim UPDATE's WHERE.

**One active job per tenant.** Without it a tenant with two hundred queued
leagues occupies every worker and everyone else waits. Cruder than a weighted
share, and it cannot be gamed by enqueueing more.

**One provider-touching job globally.** Phase 39's guarantee is that
"increasing workers never raises provider rps". The provider rate-limits the
deployment, not each process, so the queue must too. At most one job whose
kind is in `PROVIDER_KINDS` holds a lease at a time. Non-provider work is
unaffected, which is the point — scaling out still buys something. The permit
is released by completion *or* by lease expiry, so a worker that dies holding
it does not block the provider forever.

**Least-recently-served tenant first.** Fairness, not correctness. A tenant
that enqueues constantly would otherwise sit permanently at the head of the
due-time order. A tenant nobody has served has no served rows at all and so
sorts first, which is what a newcomer should get.

Ordering is computed in Python deliberately: it reads clearly, the queue is
small, and **it is not where safety lives**. The UPDATE re-checks every
condition, so a candidate that goes stale between the list and the claim is
rejected by the database rather than by the list having been right.

## The method failure, which is the interesting part

Removing each rule's candidate-list filter left **all 25 tests green**. So did
removing the SQL guard. Neither removal failed anything.

Not because the tests are weak — because each rule has two independent
implementations, and removing either one leaves the other doing the job. That
is defence in depth working exactly as designed, and it makes a single-layer
removal unfalsifiable.

**So the control-removal has to remove the *control*, not one of its
implementations.** Taking out both layers at once:

| removed | failures |
| --- | ---: |
| per-tenant filter only | 0 |
| per-tenant SQL guard only | 0 |
| **both layers of the per-tenant rule** | **2** |
| provider filter only | 0 |
| **both layers of the provider permit** | **2** |
| fairness ordering (only one implementation) | **1** |

Fairness has a single implementation, so one removal is enough — which is why
it was the only control that failed on the first pass, and the signal that the
other two needed a different approach rather than better tests.

Recorded as a method note: when a control is implemented redundantly, "remove
the control and watch it fail" means removing every layer. A single-layer
removal that leaves the suite green proves redundancy, not absence of
coverage — and reading it as the latter would have been the mistake.

## A cleanup worth admitting

The first attempt at this wrote a dead `if False else` expression into the
claim guard while patching, and ruff caught it. Restored from the committed
state and written out cleanly rather than patched over. A half-edited guard
that still parses is the worst possible state for this particular function.

## Suite

**1000 passed / 0 failed**, ruff clean.

---

# Addendum 2 — durable schedules, and leaderlessness by arithmetic

Migration `0009` adds `schedules`; `api/services/schedules.py` materialises
them into `jobs`.

**Intervals, not cron.** A cron parser is a dependency and a parsing surface,
and the only recurrence this application needs is "every N minutes from an
anchor" — which is also the only shape that yields deterministic slot
boundaries without a timezone library. Narrowed deliberately; a real cron spec
would be an additive column, not a rewrite.

## The one idea

A job's identity is computed from **(schedule, slot)** — never from the moment
somebody noticed it was due. `slot_key` contains no clock reading, no worker
identity and no randomness. So any number of schedulers covering the same
window compute the same keys, and `jobs`' per-tenant uniqueness collapses them
into one job per slot.

**No leader election, no advisory lock, no primary scheduler.** The usual
answer — designate one scheduler, or take a lock — adds a failure mode (the
leader dies holding it) to prevent a problem arithmetic prevents outright.

Phase 39's acceptance names this as "three schedulers materialize one window".
It is proven directly, and so is the realistic version: a scheduler whose
cursor is an hour stale does redundant arithmetic and gets the existing jobs
back, because the keys are identical. Redundant effort, not duplicated work —
which is why `next_run_at` can be a cursor rather than the truth.

**A missed window catches up slot by slot**, not as one collapsed run. Each
slot is a separate idempotency key and therefore a separate piece of work
somebody expected to happen; collapsing an hour's gap on a 15-minute schedule
would silently drop three of four syncs.

## Backpressure measures age, not depth

`oldest_due_age` reports how long the oldest *due* job has waited. Depth says
nothing about health — a thousand jobs moving quickly is fine, one job stuck
for an hour is not — and counting jobs still on a retry cooldown would make
every retry look like an incident.

## Evidence

`tests/test_schedules.py` — 15 tests. Three schedulers, one job. Disagreeing
cursors, one job. Catch-up walks every missed slot with no duplicate keys. The
grid is absolute rather than relative to process start, so two schedulers that
booted hours apart agree. A manual "sync now" coalesces with the scheduled run
for the same slot. Jobs materialised inside the horizon are not claimable
early. Two tenants on the same cadence get separate jobs.

**Four controls removed, four failures:** put a clock reading in the slot key
(3 fail), make the grid relative (1), collapse the catch-up into one run (3),
let queue age count jobs that are not yet due (1).

One of those was my own test being wrong first: comparing an aware `now` to a
stored `available_at` by calling `.replace(tzinfo=None)` on the aware side
raised anyway, because the attribute is aware for rows still live in the
identity map and naive for rows reloaded after a flush. Normalised through the
same helper the service uses. **Fifth place in this codebase that has had to
say so.**

## Suite

**1016 passed / 0 failed**, ruff clean.
