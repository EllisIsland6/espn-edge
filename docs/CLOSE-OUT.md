# ESPN Edge — close-out

Merged to `main`. Suite **1462 tests, 1373 passing**; the 89 failures are all in three recovery
files and all predate this work — see "The recovery format has drifted" below for the measured
decomposition. ruff clean.

This closes the project at **Phase 41 (narrowed)**. Phases 39, 40 and 41 were done after the first
close-out was written; 42–45 need AWS access and spend, which was never authorized.

| Phase | Status |
| --- | --- |
| 36 tenant isolation kernel | done — 27/27 attacks on PostgreSQL |
| 37 tenant API retrofit + RLS | done |
| 38 opportunity memory | done — 396 → 245 MiB |
| 39 durable job queue | done (narrowed) — queue, leases, retry, poison, fairness, schedules, worker, outbox |
| 40 application sessions | done (narrowed) — cookie sessions, CSRF, headers; **no OIDC callback** (PyJWT uninstallable here, and hand-rolling JWT verification is not something to ship) |
| 41 observability | done (narrowed) — ten series, ten alarms, worker heartbeat, fault harness; CloudWatch half needs AWS |
| 42–45 | not started — **AWS** |

---

## What is true of this system now

Each of these was established by running something, and each names what would break it.

**Tenant isolation is enforced by the database, not by the application.** Migrations 0003–0005 create
the kernel and put a row-level security policy on all 16 tenant tables. 27 of 27 cross-tenant attacks
are denied against the schema `alembic upgrade head` actually produces — not against a design
document — as a `NOSUPERUSER NOBYPASSRLS` role on PostgreSQL 16. Every denial is paired with a
control proving the same operation inside the tenant's own scope is accepted, because a refusal alone
cannot be told apart from a missing grant or a typo'd column name.

**The application binds a tenant in exactly two places, and both are proven.** `db.get_session` for
every router and `db.session_scope` for every service. The binding is transaction-local
(`set_config(..., true)`) and re-applied on every transaction the session begins, because a handler
that commits midway would otherwise run the rest unbound — which under RLS reads nothing and presents
as an empty result rather than an error.

**No route can be added without deciding whether it is tenant-scoped.** All 57 routes must take a
bound session or appear in an explicit list with a reason, and the listed ones are called with a
connection-checkout counter running and must open zero connections. The list is a measurement, not
prose.

**No table can be added without deciding which side of the tenant boundary it is on.** That check
fails the build. It exists because Phase 36's attack suite passed 13/13 while three tenant-scoped
tables had no policy at all — an attack suite only probes what the policies already cover.

**Migrations no longer delete data.** SQLite's `DROP TABLE` with foreign keys enforced performs an
implicit `DELETE` that fires `ON DELETE CASCADE`, and alembic's batch mode rebuilds a table by
copy-drop-rename. `upgrade head` emptied all nine of `leagues`' cascading children and exited zero,
with `PRAGMA foreign_key_check` clean either way. Fixed in `alembic/env.py`, with a test that fails
when the guard is removed.

**Work survives a crash, and nothing runs twice.** The `jobs` table is the queue: a conditional
UPDATE takes an atomic lease, a worker that dies leaves a lease that expires, and the next worker
reclaims it with nothing needing to notice the death. Idempotency is per tenant, so two tenants
syncing the same league are two jobs. Schedules materialise by arithmetic — the idempotency key is
`(schedule, slot)` with no clock reading, no worker identity and no randomness — so any number of
schedulers cover the same window and the database collapses their work into one job per slot. No
leader election, no advisory lock. Side effects go through an outbox written in the same
transaction as the change they describe, delivered at-least-once, marked only after the sink
returns.

**An idle worker is distinguishable from a dead one.** Before Phase 41 it was not: a probe ran the
worker loop against an empty queue and counted every durable row that changed, and the answer was
none. Ten metric series, one alarm each, every alarm treating a missing datapoint as breaching —
because a counter written only when non-zero has no datapoint during an outage, and CloudWatch
reads `INSUFFICIENT_DATA` as not-alarming, so silence reads as health. `worker_heartbeats` carries
two timestamps so that "looping and claiming nothing" is a state the system can express. A local
fault harness proves four faults produce four pairwise-distinct observations, and a healthy
baseline distinct from all four — because a harness where every fault lights every alarm has proved
nothing.

**The opportunity read path costs 40% less memory.** 396 → 245 MiB at 100k player-game rows on arm64.
Equivalence was checked before the swap: same rows, same key order, identical values, identical
scored output.

**The branch stands on its own.** Verified by cloning it to a scratch directory and running the suite
there, not by running it in the worktree. That distinction found three tests that passed on this
machine and would have failed in CI on day one: two subprocess probes were handed a hand-built
environment but also `cwd=ROOT`, so pydantic-settings quietly filled in two required settings from
the operator's gitignored `.env`. One of them has a docstring claiming it runs with "a minimal
environment rather than the operator's whole one". Fixed, and the clean clone now passes 861/0 with
no `.env` present.

**Nothing sensitive is in the repository.** Verified per file before staging, not assumed: no `.env`,
no database, no recovery runtime state. The one alarming filename — `ops/private-recovery/keychain-password`
— is a Keychain retrieval helper containing no literal, checked with the value masked so it could not
reach a transcript either way.

---

## What is NOT true, ranked by how much it matters

1. **There is no way to log in.** Phase 40 built the second half of authentication and not the
   first: sessions are minted, verified, revoked, hashed at rest and compared with
   `hmac.compare_digest`; CSRF is double-submit; the cookie flags are right. What does not exist is
   anything that issues the first session — the OIDC callback is blocked on PyJWT, which cannot be
   installed in this environment, and hand-rolling JWT verification invites `alg:none` and key
   confusion. So the session machinery is real and unreachable. Nobody can authenticate.

   Related: the RLS proved 27/27 in Phase 37b **made login impossible**, because `memberships` and
   `users` were policied on `current_tenant()`, which is NULL before a tenant is known. The design
   was not wrong, it was finished. Fixed with a second GUC (`app.user_id`) and read-only
   "my own rows" policies; the write policies were left alone.

2. **Roles and grants are not created by any migration.** The policies bind nothing if the
   application connects as the schema owner or a `BYPASSRLS` role — Phase 33 measured exactly that,
   with every policy correct and isolation silently off. `assert_runtime_role_is_constrained()`
   refuses to start on such a role, but the roles and grants themselves are an operator step, and a
   table added by a future migration has no grants for the app role until someone issues them.

3. **`raw_cache`'s primary key is still `key` alone.** Two tenants cannot hold the same cache key, so
   the second INSERT fails with `23505` — and a uniqueness error is not something RLS hides. One
   tenant therefore learns another holds that key, and a key carries a league id and a hashed SWID.
   An enumeration oracle, measured, with its fix (a composite key) blocked until the contract step.

4. **The AI spend ceiling is shared across tenants**, so one tenant's spend consumes everyone's
   budget, and a tenant-scoped role can modify another tenant's ledger rows. The operator grant
   should withhold DELETE on both ledger tables. This is a product decision nobody has made.

5. **The contract migration is parked** (`alembic/pending/0006`). `tenant_id` stays nullable on
   `leagues`, `accounts` and `raw_cache` until every writer supplies one; roughly 32 test
   constructions do not. It is proven on both SQLite and PostgreSQL and lands the day the call sites
   are converted — together with the model change and the deletion of the test that pins the window
   open, or the parity test fails.

6. **The opportunity path still materialises one dict per player-game** before aggregating to one row
   per player (99,990 rows in, 4,166 out). Pushing that aggregation into SQL would make the peak a
   function of players rather than player-games. Not done: it moves where an analytics formula is
   evaluated.

7. **No live ESPN read has ever been performed or authorized**, and the provider's network term
   remains unmeasured. Phase 32 retired the measurement-instrument risk and explicitly did not retire
   this one.

8. **The recovery format has drifted and a restore cannot be attempted.** Recovery format v1
   freezes the whole SQLite catalog and pins its digest; Phases 36–41 added eight tables and a
   `tenant_id` to three existing ones, and the format was never re-versioned. So it matches no
   database this application can create, and 70 of the 89 failing tests are that. The control is
   fail-closed and working — it simply has not been updated. The drift has three independent parts
   (unknown tables, unlisted columns, a dead digest pin), all measured, all named in
   `tests/test_recovery_format_drift.py`.

   A further 10 failures are environmental — the missing `<repo>/.venv/bin/python` described below
   — 1 is a tenant seeding collision in the oracle's own fixture, and 8 are downstream. **The
   number 89 also appears below attached to the venv cause alone; that is a coincidence of
   arithmetic, not a shared cause.** Rebuilding the venv leaves 79 red.

9. **The offline suite cannot test isolation.** SQLite has no row-level security. Every isolation
   claim here rests on the PostgreSQL runs, and an offline assertion would be a green tick
   establishing nothing.

---

## Why it stops here

Phases 39–45 are roughly 39 sessions and 930 operator minutes in the plan.

| Phase | Needs |
| --- | --- |
| 39 durable work orchestration | offline-provable (fake clock/provider, "no network in CI") |
| 40 identity + session + frontend | offline-provable (fake OIDC/JWKS + Playwright); live callback deferred to 43 |
| 41 observability + retention | CloudWatch — **AWS** |
| 42 public infrastructure + CI | **AWS**, IaC apply |
| 43 migration/recovery rehearsal | **AWS**, live Cognito |
| 44 public synthetic launch | **AWS**, real spend |
| 45 rollback window contract | **AWS** |

39, 40 and 41 turned out to be substantially doable offline and were done, narrowed. 41's offline
half is larger than the plan suggested: four of its five acceptance clauses are provable on a
laptop, and the CloudWatch half is one sink implementation away. 42–45 cannot be done at all
without AWS access and spend, which was never authorized.

The operator's standing direction was that this is a practice project to be narrowed and
time-boxed. The architecture lessons it exists to teach have been learned and recorded.

---

## Picking this up again

**CI is red, and it is red for the reason in item 8.** `.github/workflows/ci.yml` runs
`ruff check api tests` (clean) and then `python -m pytest -p no:cacheprovider`, which fails on the
89. The first green build after this needs recovery format v2, not a change to the workflow — and
the CI environment has no `<repo>/.venv`, so 10 of the 89 are guaranteed there regardless.

Run the suite: `APP_MODE=private_operator python -m pytest tests/ -p no:cacheprovider`. Expect
**1462 tests and 89 failures, all in `test_recovery*`** — see item 8 above for the measured
decomposition. Ten of the 89 need `<repo>/.venv/bin/python` to exist, because `api/recovery.py`
launches the backup job through the lexical venv path on purpose (so a launchd plist survives a
Python upgrade); on a machine where that venv is absent those ten fail before touching any
application code. The other 79 are the format drift and will not go away when the venv is rebuilt.

The suite takes longer than a single sandbox command window. Split it, or run
`--ignore=tests/test_recovery.py` first (1187 tests, 25 failures, all in the other two recovery
files); the 1155 tests outside all three recovery files pass clean.

Migrations: `alembic upgrade head` (currently **0011**). On SQLite, 0005 and 0007 do nothing — they
are PostgreSQL-only. To exercise the policies you need a real PostgreSQL, a non-owner role, and
`docs/sprint-9/kernel/attacks_migrated.py`. `alembic/pending/0012_tenant_kernel_contract.py` is
still parked: it makes `tenant_id` NOT NULL and drops the old unique, and it lands the day the ~32
test `League(...)` writers supply a tenant — together with the model change and the deletion of
`test_the_leagues_tenant_column_is_still_nullable`, or the parity test fails.

The fault harness runs standalone and is the quickest way to see what Phase 41 built:
`APP_MODE=private_operator python docs/sprint-9/faults/harness.py`. Exit 0 means five scenarios
produced five pairwise-distinct observations.

Housekeeping this branch leaves behind: the sandbox that produced these commits could not delete
files, so git could not clean up after itself. `.git/objects/**/tmp_obj_*` and `.venv/stale-locks/`
hold that debris. `git gc` clears the first; the second is gitignored and can be deleted.

Standing constraints that were honoured throughout and should stay honoured: no secrets in chat,
logs, evidence, fixtures, commits or handoffs; public hosted mode stays synthetic-only; no provider,
Anthropic or AWS calls; no spend. Commits and the merge to `main` were authorized explicitly
mid-session; the push was performed by the operator.

---

## The part that transfers

This was a practice project. These are the findings that are not about fantasy football.

1. **A check can be green while establishing something other than what it claims.** Forty-plus
   recorded instances, and it never stopped happening — the last one was in this session's own
   verification, a shell test whose exit status came from `grep` rather than from `git`.
2. **A passing test is not evidence a control works.** The evidence is that it fails when the control
   is removed. Every control in this branch was removed and the suite re-run.
3. **An instrument that can be emptied by something other than the absence of the defect is not an
   instrument.** A memory test checked SQLAlchemy's identity map and passed with the defect
   restored — the map holds weak references, so the objects were collected before the assertion ran.
4. **A specification cannot settle what only code can show.** Five contract rejections in Phase 32
   were about properties of unwritten code. Every phase after that was run probe-first, and it paid
   every time.
5. **Reading is not measuring.** "These partial indexes will be a no-op on PostgreSQL" was written,
   then run, and the truth was the opposite and worse.
6. **A probe with no data cannot detect data loss.** The migration that emptied nine tables passes
   every schema check and every empty-database run.
7. **Counting a grep is not counting the call sites.** Two phases were sized at 122 and 37 sites; the
   real numbers were 2 and 2. A plan built on a match count plans the wrong phase.
8. **Scoping what can be read is not scoping what can be written.** `USING` without `WITH CHECK`
   reads as an access control and behaves as a read filter.
9. **A right conclusion can rest on the wrong mechanism, and the mechanism is what you act on.**
   Phase 34 was correct that memory had to come down and wrong about where it was spent; acting on
   the stated mechanism would have produced a streaming CSV writer that changed nothing.
10. **Filtering tool output to cut noise can filter out the errors.** A `grep -v` removing routine
    `psql` notices also removed a `CREATE POLICY` failure, and a report was written from what
    survived it.

11. **"The suite passes here" is not the same statement as "the suite passes from the commit."** Only
    the second is what CI checks. Cloning the committed branch and running it there took two minutes
    and found three tests that had been green for months because a gitignored file happened to exist.

12. **A harness in which every fault lights every alarm has proved nothing.** It is
    indistinguishable from an alarm catalog that always fires. The claim worth checking is that the
    faults produce *distinguishable* observations, and checking it found that a stopped host and a
    healthy system looked identical — because the window function took the last N samples instead
    of the last N periods of wall-clock time. Silence occupies periods.

13. **Zero and absent are different observations, and most systems publish neither.** A counter
    written only when it is non-zero goes silent during the outage it exists to reveal, and the
    default reading of that silence is "fine". The fix has to be structural — a reporting call that
    *refuses* an incomplete snapshot — because "remember to publish the zero" is not a mechanism.

14. **A right diagnosis can still be the wrong count.** The 89 failing recovery tests were
    explained as one cause, committed with that explanation, and turned out to be four — and the
    one that mattered had three independent parts, so fixing the obvious one would have left the
    suite just as red. Counting the failure messages took a minute. The total had been read a dozen
    times.

15. **An identical number in two places is not evidence of a shared cause.** The close-out already
    recorded "89 tests fail" for an environmental reason. A later run also failed 89, for mostly
    different reasons. Believing the coincidence would have sent the next person to rebuild a venv
    and find 79 still red.

The single most useful habit, across all of it: after something passes, break it on purpose and
check that it fails for the reason you expect. Most of the findings above came from that one move.

The second most useful: before believing a green suite, run it somewhere that is not where you built
it.
