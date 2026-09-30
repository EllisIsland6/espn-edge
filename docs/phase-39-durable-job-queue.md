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
