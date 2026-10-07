# ESPN Edge — close-out

Merged to `main`. Suite **1500 tests, 1481 passing, 19 skipped, 0 failing** — verified from a
clean clone with no `.env` and no `.venv`, not from the worktree. `ruff check api tests` clean, the
fault harness exits 0, `alembic upgrade head` reaches 0011.

Seventeen of the nineteen skips are tests that assert a property of an *installed deployment*: they
need `<repo>/.venv/bin/python`, which points at a macOS framework interpreter and resolves only on
the operator's Mac. Each names that path in its reason, and the skip condition is guarded by a test
that is never skipped itself — see the CI note below.

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

**Every tenant column that an expand window was opened for is now NOT NULL.** `raw_cache` at
revision 0012, `leagues` at 0013, `accounts` at 0014 — the last of the three, and the most
sensitive table in the schema: `swid` plus the Fernet-encrypted `espn_s2`, the Phase 36 audit's
first finding. A credential row with no tenant is invisible to every policy and therefore to
everyone; it fails closed, but silently, and the thing that goes quiet is a stored ESPN credential.
`alembic/pending/` is empty.

`accounts` needed no constraint swapped with it, and that was measured rather than assumed: it
carries no unique constraint at all, only a primary key and the tenant index. So `swid` is not
unique — two rows may hold the same ESPN credential, within a tenant or across them — and whether
that should be constrained is a product decision nobody has made.

Its rebuild had a child, unlike `raw_cache`'s: `leagues.account_id`. That is where Phase 36's
defect lived, so the revision checks at run time that nothing cascades into `accounts` rather than
resting on a sentence, compares the row counts of both tables before and after, and refuses to
finish if either moved. The test seeds a league pointing at the row being rebuilt, so a cascade
would have something to destroy.

**Two tenants can hold the same ESPN league and season, and before revision 0013 they could not.**
That was not an oversight — the old global `uq_league_season` held the line through the whole expand
window, because SQL treats NULLs as distinct and two NULL-tenant rows would otherwise have satisfied
the tenant-scoped constraint. Its cost was the reason it had to go: it made **P36-1, the
colliding-tenant case, impossible to INSERT**, which is exactly the attack Phase 36 existed to test.

`leagues.tenant_id` is NOT NULL as of 0013, so a tenantless league — invisible to every policy and
therefore to everyone, failing closed but silently — is now impossible rather than merely unlikely.
The expand window stayed open for five phases and closed the correct way round: **31 call sites
converted first, with the suite green throughout**, then the schema. `alembic/pending/` is empty for
the first time.

Two things that conversion turned up. Fifteen tests failed with `TenantNotResolved` because their
fixtures build **unbound** sessions — a configuration production never hands out — and those eleven
sites now read the tenant from the database and say so where they do it. And **five raw SQL league
writers**, one in production code, that an AST scan over `League(...)` constructor calls could not
see: counting constructors is not counting the writers, and only the NOT NULL found the rest.

**Two tenants can hold the same cache key, and before revision 0012 they could not.** The primary
key on `raw_cache` was `key` alone, so a second tenant's INSERT failed on it — and a uniqueness
error is not something row-level security hides. That told the second tenant the first one holds
that key, and a key carries a league id and a hashed SWID: a measured enumeration oracle, recorded
in revision 0004's own docstring and left open there because a composite key over a nullable column
is not a key.

Probed before anything was written, and the real behaviour is worse than that sentence. With
`tenant_id` nullable, SQLite accepted **two** rows holding NULL and the same key, because it treats
NULLs as distinct for uniqueness — so the composite would not have been a weaker key, it would have
been no key at all. PostgreSQL refuses a nullable column in a primary key outright, so the two
engines disagree about the same schema. The `NOT NULL` is load-bearing, and a test removes it to
show that.

The read path moved with it. Both cache lookups were `session.get(RawCache, key)` — a primary-key
lookup that returned whichever tenant's row held the key, correct only because a policy elsewhere
was filtering it, and nothing filtered it offline at all. They now name both columns, so a write no
longer overwrites another tenant's payload and a read no longer returns one.

**The recovery format matches a real database again, and a test now checks that it does.** Format v1
froze the whole SQLite catalog and pinned its digest — a good control that nobody updated. Phases
36–41 added eight tables and a `tenant_id` to three existing ones, and the format came to match no
database the application could build: every backup and every restore refused, in 71 tests reporting
one opaque error code. Format v2 lists the tables, admits `leagues` in both its real column orders
(appended in a migrated database, fifth in a `create_all` one — the suite builds one shape and the
operator runs the other), stops fingerprinting alembic's own bookkeeping table, and pins three
digests read off three real databases. A restore now starts from empty, because `create_all` seeds
the default tenant and the bundle's own tenant row collided with it.

What a bundle carries is a named set with a reason per entry rather than four scattered special
cases: credentials, identity, sessions, queue state and heartbeats do not travel; tenants and
schedules do. The test that did not exist for six phases now does — build every shape the
application can produce, hash it, and require the digest to be pinned, and require the reverse, that
no pinned digest is one nothing produces.

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

3. **The AI spend ceiling is shared across tenants**, so one tenant's spend consumes everyone's
   budget, and a tenant-scoped role can modify another tenant's ledger rows. The operator grant
   should withhold DELETE on both ledger tables. This is a product decision nobody has made.

4. **Three of Phase 39's tables are scoped by a nullable tenant column**, and the enforcement
   differs across them — which is three situations, not one gap. Found by a check added while
   closing `accounts`, the moment it existed:

   - `jobs` — the worker **refuses** a tenantless job at run time (`TenantlessJob`,
     non-retryable), so the application enforces what the schema does not. Note that
     `tests/test_worker.py` constructs one deliberately to test that refusal: a NOT NULL would
     leave the guard's own test unable to build its subject. That is a reason to think, not a
     reason not to do it.
   - `schedules` and `outbox` — **nothing refuses one.** A tenantless schedule materialises
     tenantless jobs, which the worker then refuses, so the failure surfaces one layer late and as
     somebody else's error. A tenantless outbox message is delivered with no tenant and the
     receiver copes.

   `tests/test_tenant_classification.py` now holds both halves under test: an unqualified
   `"tenant_id"` entry must have a non-nullable column, and the set of qualified ones must be
   exactly those three.

5. **The opportunity path still materialises one dict per player-game** before aggregating to one row
   per player (99,990 rows in, 4,166 out). Pushing that aggregation into SQL would make the peak a
   function of players rather than player-games. Not done: it moves where an analytics formula is
   evaluated.

6. **No live ESPN read has ever been performed or authorized**, and the provider's network term
   remains unmeasured. Phase 32 retired the measurement-instrument risk and explicitly did not retire
   this one.

7. **The offline suite cannot test isolation.** SQLite has no row-level security. Every isolation
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

**Verified from a clean clone, not from the worktree.** `git clone --no-hardlinks` of the merge
commit into a scratch directory with no `.env` and no `.venv`, every required setting supplied from
the environment: 1155 tests outside the recovery files pass, the three recovery files fail 89, the
fault harness exits 0, `alembic upgrade head` reaches 0011, `ruff check api tests` is clean. The
counts reproduce exactly. The clone also showed that the venv-attributable share of the 89 is **7
with no `.venv` and 10 with a dangling one** — different error paths, same total.

**CI should be green.** `.github/workflows/ci.yml` runs `ruff check api tests` and then
`python -m pytest -p no:cacheprovider`, and both pass from a clean clone with nothing installed
that a runner would not have. Said as "should" rather than "is", because nobody has run it there.

The seventeen tests that need `<repo>/.venv/bin/python` skip when it is absent, with the path named
in the reason — they assert a property of an installed deployment rather than of the code, and a CI
checkout has no `.venv` at all because `pip install -e ".[dev]"` goes into the runner's own
environment.

The skips are guarded rather than taken on trust.
`test_the_lexical_venv_skip_condition_matches_the_production_check` compares the skip predicate
against `_launchd_plist`'s own refusal and is never skipped itself, so a predicate that disagreed
with the code it stands in for fails the build instead of quietly hiding seventeen tests. Measured:
hardcoding the predicate to "usable" fails that guard and makes the seventeen run and fail, which
is the direction that matters.

Run the suite: `APP_MODE=private_operator python -m pytest tests/ -p no:cacheprovider`.

**On the operator's Mac**, with a working `<repo>/.venv`, expect everything to run. Seventeen tests
in `tests/test_recovery.py` assert properties of that installed deployment and will execute rather
than skip.

**Anywhere else** — a container, a CI runner, a fresh clone — expect **19 skips and no failures**:
the seventeen above plus two unrelated ones that predate this work.
The skips name `<repo>/.venv/bin/python` in their reason. `api/recovery.py` launches the backup job
through the *lexical* venv path on purpose: resolving the symlink would select the base framework
interpreter and lose the venv's package search path under launchd. That symlink points at
`/Library/Frameworks/Python.framework/.../python3.14`, so it resolves on the Mac and nowhere else.
Measured rather than assumed: pointing `api.recovery.ROOT` at a tree whose `.venv/bin/python` does
resolve took the file from 18 failures to 3, and two of those three need that venv to carry the
application's dependencies too.

The suite takes longer than a single sandbox command window. Split it, or run
`--ignore=tests/test_recovery.py` first: 1200 tests, 0 failures.

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

16. **A pin nothing produces is a control that reads as working and is not.** The recovery
    format's catalog fingerprint described a schema from six phases earlier, so it matched no
    database the application could build — and nothing noticed, because no test ever compared the
    pin to a real database. The fix that mattered was not re-pinning; it was the test that builds
    every shape the code can produce and requires both that each one is pinned and that no pin is
    one nothing produces.

17. **The suite builds one shape and the operator runs another.** A migrated database and a
    `create_all` database disagreed about one table's column order, and one of them contained a
    table the other did not. A single-order allowlist passes exactly one of them, silently, in
    whichever direction nobody tests — and for an entire sprint the direction nobody tested was the
    one the operator actually runs.

18. **Fixing the obvious part of a problem can leave the symptom unchanged.** The recovery drift
    had three independent parts. Listing the eight missing tables — the part anyone would find
    first — still failed validation, because three older tables had also gained a column. A fix
    that changes nothing visible is indistinguishable from no fix, and the only way to tell them
    apart is to measure which check is refusing.

19. **A hand-built environment is not isolation if the thing it configures reads a file by
    absolute path.** The restore verifier spawned a subprocess with a carefully minimal environment
    that was missing three *required* settings, and it worked for months because `Settings` loads
    `ROOT/".env"` by absolute path — so the minimal environment never isolated anything. On a
    machine without that file the subprocess printed a traceback instead of JSON and the caller
    reported "Restored application verification failed": a missing setting, presented as data loss,
    during a restore. The same mistake had already been recorded a phase earlier in two *tests*;
    this time it was in production code, and the clean clone found it both times.

20. **Running the suite you edited is a narrower habit than running the suite.** A one-word
    filename in the restore verifier tripped a provider-wiring scan three files away. The targeted
    runs were all green; the clone running everything was not.

21. **A race can be unwinnable by its own configuration, not just lost on a slow machine.** A test
    forked a child that slept 1.2 seconds and then asserted the child had not yet exited — while
    setting a timeout of 1.5 seconds that the code had to wait out first. It was not flaky and it
    was not the machine; 1.5 > 1.2. The fix was not a longer sleep but a handshake, because what
    the assertion existed for was a liveness *precondition* — a dead process holds no locks, so
    the lock check after it would have proved nothing.

22. **"The control removal fails the test" is not the same as "the test can report the defect."**
    Removing the `close_fds=True` behind an authority-free claim did fail the test — with the
    marker file *missing* rather than reading "bad", because the extra descriptors broke the
    subprocess before the probe ran. The removal proved the spawn was fragile and left the probe
    unproven. A probe needs its own two-way instrument check, and it is cheap: a dozen lines with
    no production code in them.

23. **A constraint can be present and its name invisible.** Hand-written SQLite DDL wrapped
    `CONSTRAINT fk_raw_cache_tenant_id` and `FOREIGN KEY(...)` onto two lines for readability.
    SQLAlchemy recovers constraint names by matching the stored DDL text, so the key reflected as
    *unnamed* — and the failure surfaced in a **different migration four revisions further down**,
    as `No such constraint`. Formatting changed behaviour, the symptom appeared nowhere near the
    cause, and no amount of reading the new revision would have found it.

24. **Counting constructor calls is not counting the writers.** An AST scan found 31 `League(...)`
    sites and I converted all of them; the NOT NULL then found **five raw SQL inserts** the scan
    could not see, one in production code. Lesson 7 says counting a grep is not counting the call
    sites. This is the same error from the other side, committed by someone who had just written
    lesson 7 down.

25. **A list of known holes goes stale silently, because the test asserts the list.**
    `raw_cache` sat in the "NOT YET SCOPED" set for two revisions after it was scoped, and the test
    guarding that set passed the whole time — it compares the set to itself, so a stale entry is
    invisible to it. The remedy is not a cleverer assertion; it is editing the entry in the same
    change that closes the hole. A second check, comparing each recorded claim against the actual
    column, found three more overclaims the moment it existed.

26. **A fingerprint can hash something that means nothing — and then the fingerprint is the
    defect.** The recovery catalog's SHA-256 included each table's stored DDL text. Two checkouts
    of the *identical commit* produced `leagues` DDL differing only in whether the FK clause came
    before or after the UNIQUE one: same constraints, same semantics, different digest. So the pin
    computed in one tree refused a database built in the other, and the refusal says
    `Database schema is not allowlisted` — a reproducibility problem wearing the costume of data
    loss during a restore. I never pinned the root cause: it is produced inside alembic's batch
    rebuild, it is stable within a tree, it survives six `PYTHONHASHSEED` values, and the stale
    `.pyc` that looked responsible did not survive a three-state experiment. **Not pinning the
    cause did not block the fix**, because the conclusion did not depend on it: a control whose
    verdict changes with something outside the source is not a control, so the fingerprint stops
    hashing the ordering. The honest write-up records the open question rather than quietly
    implying it was solved.

    The second half of that fix matters as much as the first. Sorting the clauses makes reordering
    invisible — and *dropping* them would too. So the tests assert both directions: reordering does
    not change the reading, a changed constraint still does, a changed column order still does, and
    every clause survives the parse, counted. Only the invariance test fails when the control is
    removed; the other three exist so that the canonicaliser cannot pass by throwing information
    away.

27. **The suite had never been run the way it is actually going to run.** Every green reading in
    this project came from one machine with one interpreter -- CPython 3.14.7 -- and from
    `APP_MODE=private_operator` on the command line. CI runs ubuntu-latest, Python 3.12, and a bare
    `python -m pytest` with no `APP_MODE` at all; the container image is `python:3.12-slim`. (The
    `APP_MODE` half turned out to be inert: `tests/conftest.py` **assigns** it, so CI's bare
    invocation is the same run. Measured, after assuming otherwise.) Running CI's own commands on
    CI's own interpreter, in a second environment because the dev machine has only 3.14, turned up
    three failures in ten minutes:

    - `os.kill(pid, 0)` cannot tell a running process from a **zombie**. A process-group kill left
      the descendant dead-but-unreaped for ~1.5s (measured: `/proc` state `Z`, ppid 1), the probe
      read "alive" throughout, and a correct control was reported broken. Why it had been passing
      in the dev environment was never measured -- only that it did. The probe could not have
      established the claim in either place; one of them let it look like it had.
    - Two tests asserted `elapsed < 3` against a path configured to wait 2.6s. 0.4s of headroom is
      less than the cost of cold-starting the subprocesses they spawn: ~2.1s on the dev machine,
      3.12-3.42s on the other across five runs. The bound now comes from the configured timeouts plus named slack,
      with a separate assertion that the configuration is still a prompt-return one.
    - A provenance test drew 25 random UUIDs and required `min(entropy) > 3.0`. The production floor
      is **2.5** -- so the test guarded a number the scanner does not use, with a margin the real
      distribution crosses. Measured over 50,000 draws: 7 (0.014%) below 3.0, **none** below the
      production floor. It was a 1-in-290 red build whose message accused the scanner.

    The lesson is not "test on another OS too" -- and the first version of this lesson said exactly
    that, because its author had the environments wrong (see lesson 29). It is that **the
    environment is part of what a green suite establishes**, and it is the part nobody writes down.
    A suite that has only ever run in one place has been measured in one place, and "the place"
    includes the interpreter.

28. **Four falsified hypotheses are a result, and recording them is what stops the next person
    repeating them.** `test_trusted_prompt_failures_are_stable_hidden_and_single_shot[ctrl-c]`
    is **intermittent** under 3.14 and does not reproduce under 3.12, which is what CI runs. It also
    reproduces on a clean clone of the commit *before* this work, so it is not new.

    The characterisation above was wrong twice before it was right, and both corrections came from
    measuring rather than from thinking harder. First it was recorded as "fails in a whole-file run,
    passes when selected alone" -- **falsified**: at the current commit the file alone passes, and a
    bisect over the file's own 91 predecessors plus the target passes at every prefix. Then the
    trigger was assumed to be an earlier file -- **falsified**: all 32 files that sort before it,
    plus the target, pass too. There is no prefix that reproduces it. Six consecutive full-suite
    runs failed it while the machine was loaded with other work; three later runs on a quiet machine
    did not. Load is a hypothesis consistent with that and is untested.

    One run did something worse than fail: the whole suite **blocked**. `/proc/loadavg` read 0.06
    with 151 threads, which says the process was not computing but waiting -- a measurement, not an
    inference, and the one available when a sandbox hides the process table. A hang in CI burns the
    whole six-hour budget instead of printing a red test, which makes this more than a flaky-test
    nuisance even though 3.12 has never shown it. What is measured rather than guessed:

    - It is **not a timing bound.** Raising the 5s deadline to 30s does not help, and instrumented,
      every prompt child that does terminate takes 0.01-0.12s. Watching a failure survive a sixfold
      deadline is what proves a backstop was never the thing failing.
    - The signal **is** sent and the prompt **is** seen -- `sent=True`, `marker_seen=True`, and a
      transcript that is exactly the 43-byte prompt and nothing after.
    - The child is genuinely alive, not a reaped pid: the failure path's `kill(pid, 9)` and
      `waitpid(pid, 0)` both succeed.
    - **Resending** SIGINT every 0.25s for the full five seconds changes nothing, so it is not one
      signal lost in a race window.
    - No signal-mask leak and no changed disposition after any test in the file (a teardown hook
      over SIGINT/SIGTERM/SIGHUP/SIGQUIT: zero hits in 279 tests).
    - No leaked stdlib patch: `selectors.SelectSelector`, `selectors.DefaultSelector`,
      `termios.tcsetattr` and `termios.tcgetattr` are unmodified after every test, despite several
      tests assigning them **directly** rather than through `monkeypatch`.

    What remains: the child wrote the prompt and then sat in something that neither returns nor
    checks `interrupted_signal`, immune to repeated signals. The production handler only sets a
    flag, and under PEP 475 an interrupted syscall is restarted when the handler does not raise --
    so the flag is seen only when the poll loop next ticks. The loop is sound (it checks the flag
    before and after a 0.1s-capped select), which points at a blocking call *between* the prompt
    write and the loop -- `termios.tcsetattr`, `_capture_independent_tty_state` and the `/dev/tty`
    open all sit in that window. **The next step is not another bisect**: a bisect needs a
    deterministic reproducer and there is none. It is to instrument that window in the child, where
    the transcript already carries anything it writes.

    A procedural note from the bisect that failed: the first harness printed a confident trigger
    (`[nul]`) from a search whose **invariant never held** -- both ends passed, so every step moved
    the same way and the final value was an artefact of the loop, not a measurement. The second
    harness checks both ends first and refuses to conclude. **A search that cannot state its
    precondition will still print an answer.**

29. **The author of lesson 27 had the environment wrong while writing it.** This project's two
    environments were described throughout as "macOS" and "Linux". They are both Linux: the Claude
    desktop workspace is an isolated Linux VM on the Mac (Ubuntu aarch64, CPython 3.14.7, PID 1
    `bwrap`) and the cloud container is Linux with CPython 3.12.3. They differ by **interpreter
    version, architecture and machine speed**, and nothing in that session ever ran on macOS.

    Every fix in lesson 27 stands, because each was proven by removing its control and watching the
    test fail -- a procedure that does not depend on the OS. What was wrong was the *explanation*,
    and one explanation was a guess wearing the clothes of a measurement: "macOS reaps orphans
    promptly" was never measured, it was inferred from the thing it was offered to explain. The
    `[ctrl-c]` finding's reason for not blocking CI changed too, from "it is macOS-only" to "it does
    not reproduce under 3.12, which is what CI runs" -- same conclusion, weaker evidence, and the
    two factors cannot be separated with the interpreters available.

    The general form: **`uname` is a measurement and "the dev machine" is an assumption**, and this
    was committed by someone who had just written down that the environment is the part nobody
    writes down. Check what the shell actually is before attributing anything to it.

30. **A known gap and a decision are different things, and a sentinel that conflates them invites
    the wrong fix.** The AI spend ledger sat in the tenant classification as
    `NOT YET SCOPED - global ceiling is shared across tenants` for a sprint. The operator has now
    decided it should stay shared -- so the entry was never a gap, and `NOT YET SCOPED` was an
    instruction to the next session to close something that should not be closed. It now reads
    `DELIBERATELY GLOBAL`, with a test that pins what the decision costs (one tenant's spend
    consumes everyone's budget; a tenant-scoped role can modify another tenant's ledger rows) and
    asserts the tables really have no tenant column, so the label cannot be true of the comment and
    false of the schema.

    The second sentinel immediately broke two tests that keyed on the first one's spelling: a
    parametrised test read `DELIBERATELY` as a column name and asserted that a column by that name
    exists. `is_column_path()` is now defined once. **A predicate keyed on one spelling is the same
    defect as a test parametrised over its own input** -- and the replacement for it was, on the
    first attempt, a tautology (`A and not A`, which cannot fail); removed rather than reworded.

31. **A constraint is a new responsibility for every writer, including the error path.**
    `UNIQUE (tenant_id, swid)` on `accounts` was asked for as a one-line schema change. What it
    actually touched:

    - **The restore.** The bundle substitutes a placeholder for every account's swid, and that
      placeholder was ONE constant -- so a tenant holding two accounts restored to two identical
      pairs and the constraint would have made disaster recovery the path that refuses. Measured
      before the constraint existed; no test had two accounts in one tenant, which is why the suite
      would not have caught it. The placeholder is per-row distinct now, and recovery format v3
      exists because of it.
    - **The API.** A duplicate `POST /api/accounts` reached `session.commit()` unguarded and raised
      `IntegrityError` out of the handler. That exception's string carries the bound parameters --
      **the swid and the encrypted espn_s2** -- and an unhandled exception in a request handler is
      logged with its traceback. In a module whose second half is tests proving credentials never
      reach a response or a log. Measured: the swid was in the exception text. Now a 409 with fixed
      text, guarded twice, with the pre-check disabled in one test so the constraint has to answer
      on its own.
    - **The existing tests.** Five credential-redaction tests shared one module-level swid canary in
      one tenant, so the second one to post it got the constraint instead of an account. The canary
      is per-test now, which also names the test if one ever leaks.
    - **Existing data.** Nothing prevented duplicates before, so the migration refuses up front and
      reports the tenant and the account ids -- never the swid, which is a credential identifier. A
      migration that prints one into a terminal, a log or a CI transcript has leaked it. Asserted
      both ways: the message identifies the rows, and the swid does not appear in the output.

    The shape to carry: when a constraint is added, the writers are not the only thing to count.
    **The error path is a writer too, and it writes to the log.**

32. **I read a buffered log as a hang, twice in one session.** A background pytest run's output file
    sat unchanged for ten minutes and I concluded the suite was stuck -- both times. It was not:
    pytest's dot-format terminal writer buffers, so with `-q` the file grows in 4KB jumps, and with
    `-vv` every line flushes. The second time I had already been caught by the first. The run that
    "hung" at 17 bytes finished in 161 seconds. **`wc -c` on a log is not a measurement of a
    process**, and lesson 5 said "reading is not measuring" twenty-seven lessons ago.

33. **A recorded gap can overstate itself, and the fix is a gate rather than a guard.** The tenant
    classification said `schedules` and `outbox` have "nothing refusing a tenantless row" -- true of
    the code, and misleading as a statement of exposure, because an AST scan plus a raw-SQL check
    found that **nothing under `api/` writes either table**. Both modules are built, tested and
    measured, and reached only for their two measurement functions.

    Writing the guard anyway would have meant inventing policy for a feature with no caller and no
    requirements: skip and retry forever, disable the row, or raise and stop every tenant's
    scheduler are three different answers and nothing in the repository chooses between them. So the
    uncalled state is pinned instead, and the test fails the moment a production caller appears,
    naming the `jobs` precedent to follow. **The choice becomes unavoidable exactly when it becomes
    answerable**, which is the opposite of deciding it now and being wrong cheaply.

    The scan's first run flagged `snapshot.py`, which imports `outbox` for `undelivered_age` and
    separately imports **observability's** unrelated `emit` -- a bare name attributed to the wrong
    module. A bare name now counts only when bound by `from <module> import <name>`. **A false alarm
    in a build gate is worse than a missed one**, because a gate people override is a gate that is
    not there, and the three negative cases are tested as carefully as the positive ones.

34. **"Unconfirmed, needs the right environment" can sometimes be settled by reading.** The one
    failure in the first-ever run of the Playwright suite was filed as probably-a-browser-artefact,
    because it had run against chromium-1194 rather than the pinned 1228. It was not. Two sibling
    components hide a broken image by different mechanisms -- `PlayerIdentity` with Tailwind's
    `hidden` (`display: none`), `TeamIdentity` with `opacity-0` -- and the failing test asserts
    `display: none`, copied from the portrait test. `TeamIdentity` could never produce it on any
    browser. The test had simply never been executed, which is what a suite nobody has run means.

    The fix was in the component, not the test: three states where there were two, so a FAILED logo
    is hidden and a logo that has not loaded YET is merely transparent. That also restores a
    distinction the DOM had lost -- with one class for both, the test could not have proved the
    error path ran even if it had asserted the right property.

    The habit worth keeping: before accepting "it needs an environment I do not have", check whether
    the claim is about the environment at all. This one was decidable from two files.

35. **A control removal is a claim about the harness as much as the test.** Three of this session's
    harnesses were wrong in ways that produced confident output: a bisect whose invariant never held
    printed a trigger that was an artefact of its own loop; a control removal replaced the first
    occurrence of `npm ci` in a Dockerfile, which was inside a *comment*, and reported the test as
    passing with the control supposedly gone; and a `pkill -f` pattern matched the shell that issued
    it. Each was caught by looking at what the experiment actually did rather than at its verdict.
    After "the control removal passed", the next question is not "which control is unheld" but
    **"did the removal remove anything"**.

36. **Saying what you could not measure, and why, is part of the finding.** A second intermittent
    blocker turned up: three consecutive full-suite runs on the newer interpreter blocked at one
    test, `/proc/loadavg` at 0.00 proving they were waiting rather than computing. The run before
    them finished in 166 seconds and a later one sailed past that test, so it is intermittent; the
    file alone passes; and the interpreter CI uses has completed the suite cleanly five times.

    Two measurements ruled out the obvious cause and one ruled out the obvious instrument. A SQLite
    busy-lock cannot be it: no `timeout` is passed in `connect_args`, so sqlite3's 5-second default
    applies and a conflict raises `database is locked` rather than hanging for minutes. And
    `timeout -s ABRT` produced **no faulthandler dump at all** -- which is a clue rather than a
    failed experiment, because a dump needs the interpreter to run the handler, so the main thread
    is stuck below it in a C-level wait.

    What is left needs a native stack, and this environment cannot take one: every shell call runs
    in its own PID namespace, so a process another call started is unattachable, and a background
    job started with a bare `&` dies when its call's sandbox tears down. So the write-up carries the
    recipe that *would* work -- start the suite, sleep past the hang, and read
    `/proc/<pid>/task/*/{stat,wchan,syscall}` **inside one call** -- which is how the healthy
    reading that proved the later run was fine got taken.

    An investigation that ends without the answer is still worth the write-up if it ends with the
    eliminations, the boundary, and the next command to run. The alternative is the next person
    spending the same hours to learn the same four things.

37. **A silent instrument is a measurement when you have proved the instrument works.** A
    session-wide faulthandler watchdog, armed at 55 seconds and verified minutes earlier by
    dumping twice on a five-second sleeper at a two-second timeout, stayed completely silent while
    the suite sat blocked on one test for over 175 seconds. That is not a failed attempt. A
    faulthandler watchdog is a C thread that does not need the GIL, so its silence says the process
    cannot run *any* thread -- which eliminates every Python-level explanation at once and is worth
    more than the three instruments before it, each of which only eliminated itself.

    The order matters: the same silence, from an instrument nobody had checked, would have meant
    nothing at all. Verify the instrument on a known positive **first**, and a null result becomes
    evidence instead of an absence of evidence.

38. **A comparison that changes two things at once measures neither.** Two problems -- an
    intermittent `[ctrl-c]` failure and six full-suite hangs -- were recorded across two commits as
    "only under Python 3.14, and CI pins 3.12". Wrong, and the error was structural: every 3.14 run
    happened in the repository worktree and every 3.12 run happened in a container-local checkout.
    The interpreter and the filesystem moved together, and the variable that got named was the one
    that was easier to see.

    `mount` ended it in one line. The worktree is **`type fuse`** -- a userspace filesystem bridging
    to the Mac host -- and a stalled FUSE daemon produces precisely what every instrument had been
    reporting: uninterruptible `D` state, no signal delivery, no faulthandler dump however it is
    armed, no CPU consumed, and a block at whatever point the suite next touches a file, which is
    why the hang moved between six different tests. The controlled comparison, same commit and same
    3.14 interpreter, on a local block device: 1517 passed, 0 failing, twice, with `[ctrl-c]`
    passing. Roughly ten VM-local runs that day, zero hangs; six in the mounted worktree.

    The conclusion -- CI is unaffected -- survived, which is the trap. **A right conclusion resting
    on the wrong mechanism is still wrong**, because the mechanism is what the next person acts on:
    "upgrade CI to 3.14 and watch it break" was the advice implied, and it would have wasted a day.
    Lesson 9 said this already. It needed saying again, because this time the confound was the
    machine rather than the code.

39. **An artefact verified as an artefact is not verified against the commands it was told to run.**
    `Dockerfile` installed the project with a bare `pip install .`. `infra/app.py` defined the
    schema task as `command=["alembic", "upgrade", "head"]`. alembic is not a `[project]
    dependency` -- deliberately, because nothing under `api/` imports it. So the image would have
    built, the service would have started, `/api/health` would have returned `ok`, and the migrate
    task would have exited `executable file not found in $PATH`. The schema would never have been
    created and every signal pointing at the image was green.

    Three files had to agree and **none of them imports the others**: `infra/app.py` says what to
    run, `Dockerfile` says what is installed, `pyproject.toml` says what that means. Nothing in a
    test suite that imports `api` can see a disagreement between them, which is why eighteen tests
    about the hosted container passed while this sat underneath.

    Found by reading `pyproject.toml` with the question "what does the install layer actually see",
    then measured without a container at all: a directory holding only `pyproject.toml` and `api/`
    -- exactly that layer's view -- `pip install .` gives no `bin/alembic` and
    `ModuleNotFoundError`; `pip install ".[migrate]"` gives both. **The environment I did not have
    (docker) was not the environment the defect lived in.** Lesson 31 again, from the other side.

    The general form: for every artefact, enumerate the commands *other* files have been told to
    run with it, and check each one against what the artefact contains. `tests/test_image_commands.py`
    does it by resolving each argv[0] to the distribution that provides it as a console script.

40. **Two mechanics that make a run report nothing while looking complete.** Both cost a measurement
    this stretch.

    `addopts = "-q"` is already in `pyproject.toml`, so passing `-q` again makes it `-qq`, and
    double-quiet **suppresses the summary line**. A split suite run finished in 27 seconds, printed
    475 dots and `[100%]`, and said neither how many passed nor that anything had. Dots are not a
    result; the summary line is. Run it without `-q` and read the number.

    And on this device nothing survives the shell call that starts it -- `setsid`, `nohup` and
    `tmux` are all torn down when the call returns. Two full-suite runs were read as progressing
    (load average, a growing log) when the process was already gone, and a third as hung at 57%
    when the output was merely buffered. Either fit the job inside one call or split it; and
    **check liveness by the process, never by the log**, because a block-buffered log looks
    identical whether the writer is slow or dead.

    Related, and now the fourth occurrence: `pgrep -f <pattern>` and `pkill -f <pattern>` match the
    shell that ran them, because the pattern is in that shell's own argv. Two of those killed my own
    session (exit 143, 144); two reported a dead process as alive. The bracket trick (`[p]ytest`)
    fixes `grep` but not when the wrapper's argv quotes the real command elsewhere on the line. Use
    `pkill -x <exact-name>` or a pidfile.

The single most useful habit, across all of it: after something passes, break it on purpose and
check that it fails for the reason you expect. Most of the findings above came from that one move.

The second most useful: before believing a green suite, run it somewhere that is not where you built
it.
