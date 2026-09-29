# ESPN Edge — close-out

Branch `sprint-9/tenancy-capacity-hardening`, six commits on top of
`codex/draft-phase-30-recovery-contract`. 174 files, ~49,000 lines. Suite **861 passed / 0 failed**,
ruff clean. Nothing pushed, nothing merged.

This closes the project at Phase 38. Phases 39–45 are not started, and the reason is below.

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

**The opportunity read path costs 40% less memory.** 396 → 245 MiB at 100k player-game rows on arm64.
Equivalence was checked before the swap: same rows, same key order, identical values, identical
scored output.

**Nothing sensitive is in the repository.** Verified per file before staging, not assumed: no `.env`,
no database, no recovery runtime state. The one alarming filename — `ops/private-recovery/keychain-password`
— is a Keychain retrieval helper containing no literal, checked with the value masked so it could not
reach a transcript either way.

---

## What is NOT true, ranked by how much it matters

1. **There is no authentication.** None. No session cookie, no token, no `current_user`. The seam
   refuses to serve when more than one tenant exists, which makes this single-operator scoping with
   multi-tenant enforcement underneath it. It is not multi-tenant serving, and no amount of the work
   above makes it so. This is Phase 40.

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

8. **The offline suite cannot test isolation.** SQLite has no row-level security. Every isolation
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

39 and 40 are large but doable offline. 41–45 cannot be done at all without AWS access and spend,
which was never authorized. The operator's standing direction was that this is a practice project to
be narrowed and time-boxed, and the architecture lessons it exists to teach have been learned and
recorded. Continuing would be more of the same work, not more of the same learning.

---

## Picking this up again

Run the suite: `APP_MODE=private_operator python -m pytest tests/ -p no:cacheprovider`. The
`test_recovery*` files need `<repo>/.venv/bin/python` to exist — on a machine where that venv is
absent or points elsewhere, 89 of them fail before touching any application code. That is
environmental and predates this branch.

Migrations: `alembic upgrade head` (currently 0005). On SQLite, 0005 does nothing. To exercise the
policies you need a real PostgreSQL, a non-owner role, and
`docs/sprint-9/kernel/attacks_migrated.py`.

Housekeeping this branch leaves behind: the sandbox that produced these commits could not delete
files, so git could not clean up after itself. `.git/objects/**/tmp_obj_*` and `.venv/stale-locks/`
hold that debris. `git gc` clears the first; the second is gitignored and can be deleted.

Standing constraints that were honoured throughout and should stay honoured: no secrets in chat,
logs, evidence, fixtures, commits or handoffs; public hosted mode stays synthetic-only; no provider,
Anthropic or AWS calls; no spend; no pushes or merges.

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

The single most useful habit, across all of it: after something passes, break it on purpose and
check that it fails for the reason you expect. Most of the findings above came from that one move.
