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
    this project came from macOS, Python 3.14, `APP_MODE=private_operator`, and the machine the code
    was written on. CI runs ubuntu-latest, Python 3.12, and a bare `python -m pytest` with no
    `APP_MODE` at all; the container image is `python:3.12-slim`. Running CI's own commands on CI's
    own interpreter -- in a cloud container, because the dev machine has only 3.14 -- turned up three
    failures in ten minutes, and **none of them was a 3.12-vs-3.14 difference**. All three were
    platform or wall-clock assumptions that macOS happened to satisfy:

    - `os.kill(pid, 0)` cannot tell a running process from a **zombie**. A process-group kill left
      the descendant dead-but-unreaped for ~1.5s (measured: `/proc` state `Z`, ppid 1), the probe
      read "alive" throughout, and a correct control was reported broken. macOS reaps orphans
      promptly, so the test passed there on an accident of timing rather than on a sound reading.
    - Two tests asserted `elapsed < 3` against a path configured to wait 2.6s. 0.4s of headroom is
      less than the cost of cold-starting the subprocesses they spawn: ~2.1s on macOS, 3.12-3.42s on
      Linux across five runs. The bound now comes from the configured timeouts plus named slack,
      with a separate assertion that the configuration is still a prompt-return one.
    - A provenance test drew 25 random UUIDs and required `min(entropy) > 3.0`. The production floor
      is **2.5** -- so the test guarded a number the scanner does not use, with a margin the real
      distribution crosses. Measured over 50,000 draws: 7 (0.014%) below 3.0, **none** below the
      production floor. It was a 1-in-290 red build whose message accused the scanner.

    The lesson is not "test on Linux too". It is that **the environment is part of what a green
    suite establishes**, and it is the part nobody writes down. A suite that has only ever run in one
    place has been measured in one place.

28. **A bound that is only a backstop should not be sized like a claim.** Separately from the above,
    `test_trusted_prompt_failures_are_stable_hidden_and_single_shot[ctrl-c]` fails on macOS in a
    whole-file run and passes when selected alone -- and **raising its 5s deadline to 30s does not
    help**, which is how we know it is not a timing bound at all. Instrumented, every prompt child
    terminates in 0.01-0.12s. It is order-dependent state: the prompt child is a `pty.fork()` of the
    test process, so it inherits the process's signal mask, and the production code blocks signals
    with `pthread_sigmask` around a critical section -- a SIGINT blocked in the child is pending
    forever rather than fatal. A teardown hook asserting an empty mask after every test came back
    clean on Linux, where the failure does not reproduce; it has not been run on macOS, where it
    would name the leaker. Recorded as open with that lead rather than closed with a widened
    deadline.

The single most useful habit, across all of it: after something passes, break it on purpose and
check that it fails for the reason you expect. Most of the findings above came from that one move.

The second most useful: before believing a green suite, run it somewhere that is not where you built
it.
