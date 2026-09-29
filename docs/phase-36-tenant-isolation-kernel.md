# Phase 36 — Tenant Isolation Kernel (design proven under attack; migration not landed)

Source plan: `docs/sprint-9/06-phases.md`, Phase 36. Predecessors: Phases 33–35.

**Outcome required:** establish stable tenant/user/membership identity and database-enforced isolation
before retrofitting endpoints. *"Any isolation failure blocks all later work."*

**Status: the isolation design is built and holds against all thirteen R4 attacks on a real
PostgreSQL 16, verified by mechanism rather than by outcome.** The startup guard is landed in the
repo. The kernel's DDL is proven as SQL and **not yet an Alembic migration** — see "Not landed".

## The R4 attacks

Run as `edge_app`, a `NOSUPERUSER NOBYPASSRLS` role, against two tenants holding deliberately
colliding data.

| # | Attack | Result |
| ---: | --- | --- |
| 1 | absent tenant context | **0 rows** — fails closed |
| 2 | tenant reads its own scope | sees exactly its 3 leagues |
| 3 | guessed primary keys of another tenant | **0 rows** |
| 4 | colliding external IDs across tenants | 3 shared ESPN ids coexist |
| 5 | direct SQL on a child table, bypassing the league join | own 3, other-tenant **0** |
| 6 | cross-tenant `INSERT` into `leagues` | **refused** |
| 7 | cross-tenant child `INSERT` into `teams` | **refused** |
| 8 | pool reuse — next transaction, same connection | **0 rows**, setting empty |
| 9 | pool reuse — checkin/checkout, same backend | **0 rows** |
| 10 | repointing a league to another tenant | **refused** |
| 11 | owner role with no context | sees all 6 — *by design; the app must never use it* |
| 12 | runtime role privileges | `rolsuper=False rolbypassrls=False` |
| 13 | runtime role disabling RLS | **refused** |

**13/13.**

### Verified by mechanism, not by outcome

Attacks 6 and 7 first reported a bare `ProgrammingError`, which is also what a typo, a missing grant
or a `NOT NULL` produces. A refusal for the wrong reason is a false all-clear on the most important
test in the phase, so the cause was checked:

```
sqlstate: 42501
message : new row violates row-level security policy for table "leagues"
```

That is the `WITH CHECK` clause. And the control that makes it mean something: **the same `INSERT`
into the tenant's own scope is accepted.** Two refusals plus one acceptance is the evidence; two
refusals alone would not have been.

## What the kernel is

**Identity.** `tenants`, `users`, `memberships` (unique on `(tenant_id, user_id)`).

**Discriminator.** `leagues.tenant_id`. Children inherit the tenant through their league, so a
cross-tenant child row is expressible as a constraint rather than a convention.

**Context.** Transaction-local, via `set_config('app.tenant_id', :t, true)`. Policies compare against
`current_tenant()`, which returns NULL when unset — so **no context means no rows**, everywhere, by
construction rather than by a check at each call site.

**Policies.** `ENABLE` + `FORCE ROW LEVEL SECURITY` on `tenants`, `memberships`, `leagues` and ten
child tables, each with `USING` **and** `WITH CHECK`. `USING` alone scopes reads and silently permits
a tenant to write rows into another tenant.

> **Corrected by `docs/phase-36-table-classification-audit.md`:** nine of those ten got a policy, not
> ten. `current_roster_entries` has no `league_id` column — it reaches its league through
> `snapshot_id` — so its `CREATE POLICY` failed, and the error was hidden by a `grep -v` filtering
> routine `psql` noise. It has `FORCE` with no policy, which denies everything, so it fails closed
> rather than leaking. The audit also found **three tenant-scoped tables with no policy at all**:
> `accounts`, `raw_cache` and the spend ledger.

**Roles.** `edge_app` and `edge_worker`, both `NOSUPERUSER NOBYPASSRLS`, with DML but no DDL —
Alembic runs as the owner and the application never shapes the schema.

**Immutable ownership.** A `BEFORE UPDATE` trigger raises if `tenant_id` changes. Phase 36 names an
explicit transfer contract; until one exists, the answer to "can a league move between tenants" is
no, enforced rather than documented.

## Two findings

**P36-1 — the league unique is global, so two tenants could not hold the same ESPN league.**
MEASURED: seeding the colliding-tenant case failed outright on `uq_league_season`, a unique over
`(espn_league_id, season)` with no tenant in it. That is the "guessed/colliding IDs" case the phase
names, and the case `api/services/fixtures.build_colliding_tenants` exists to express — it could not
be inserted at all. **The unique must become `(tenant_id, espn_league_id, season)`**, which is the
"composite parent/child FKs and uniques" item, and it is a schema change Phase 36 must carry.

**P36-2 — `set_config`'s third argument is the entire pool-reuse story.** Phase 33 used
`set_config('app.account_id', '1', false)`. `false` is **session**-scoped: it survives the
transaction, so the next request on a pooled connection inherits the previous request's tenant. `true`
is transaction-local and is what attacks 8 and 9 verify. `SET LOCAL` is the documented spelling but
takes no bind parameters, so `set_config(..., true)` is the form that is both correct and
parameterisable. One boolean is the difference between isolation and a cross-tenant read.

## Landed in the repo

`api/db.py` gains `assert_runtime_role_is_constrained()` — the P33-1 precondition, as a startup check:

```
edge_owner   superuser=True  bypassrls=True  -> REFUSED (correct)
edge_app     superuser=False bypassrls=False -> allowed (correct)
```

It fails closed on an unreadable role too, because *"we could not tell"* and *"it is safe"* are
different answers. PostgreSQL only; a no-op on SQLite. SQLite suite unaffected: **806 passed / 0
failed**, ruff clean.

## Not landed, and why

- **The kernel DDL is SQL, not an Alembic migration.** Proving the design under attack and landing an
  expand-contract migration with a reversible backfill are different jobs; the second is worth doing
  against the proven design rather than alongside it. The SQL is the specification for that migration.
- **The tenant backfill** — assigning existing rows a tenant, reversibly, under expand-contract.
- **External identities.** The phase excludes Cognito, but `external_identities` still belongs to the
  identity kernel and is not built.
- **The explicit transfer contract.** The trigger enforces "never"; the contract that would allow a
  deliberate transfer is unwritten. That is the right order — deny first, then carve.
- **`POST /api/leagues` conflict behaviour.** No route is exposed until Phase 37, and the repeat-post
  acceptance belongs with the endpoint work.
- **The ten child tables above are the tenant-scoped ones I could name.** A complete audit against all
  20 tables — which are tenant-scoped, which are global reference data (`players`, `adp_snapshots`,
  `opportunity_weeks`), which are operational (`raw_cache`, `ai_spend_*`) — is not done, and a table
  wrongly left global is a leak the attacks above would not catch, because they only probe tables the
  policies already cover.

**That last one is the honest gap to carry**: 13/13 proves the mechanism on the tables it was pointed
at. It does not prove the table list is complete.

## Guarantees held

No route exposed, no Cognito dependency, no provider or read-model semantics changed, no ESPN call, no
model call, no AWS call, no spend. The disposable PostgreSQL cluster lived only in an isolated
container; nothing was installed on the operator's machine. The only repository change is the startup
guard in `api/db.py`.

---

# Addendum — the kernel landed as a migration (Phase 36b)

The section above ends by naming the gap: "the kernel DDL is SQL, not an Alembic migration." That is
now closed for the schema half. **`alembic/versions/0003_tenant_kernel_expand.py` is head.**

## What landed

| | |
| --- | --- |
| `tenants`, `users`, `memberships` | created; `memberships` unique on (tenant, user), both FKs `ON DELETE CASCADE` |
| `leagues.tenant_id` | added **nullable**, indexed, FK to `tenants` |
| backfill | every existing league assigned to one tenant, slug `default`; idempotent, runs again in 0004 to catch rows written during the window |
| `uq_league_tenant_season` | added **alongside** `uq_league_season`, not instead of it |
| `api/models.py` | the three model classes, `League.tenant_id`, and **both** unique constraints |

Row-level security is deliberately not in this revision. It is Postgres-only, and a policy over a
column that half the rows leave NULL protects nothing.

## What is parked, and why that is the point

`alembic/pending/0004_tenant_kernel_contract.py` — `tenant_id` NOT NULL, `uq_league_season` dropped.
It is written, and it was run end to end: it contracted the column, dropped the old constraint, and
its downgrade restored both with child rows intact. Its collision guard was exercised against a
database where two tenants held the same ESPN league and refused by name, leaving the database
unchanged at 0004.

It is parked because there are **37 `League(...)` construction sites across 18 files** and 4 raw
INSERTs, and none supplies a tenant. Landing NOT NULL first turns sixteen test files red at once and
invites the fix to be a default value — which is how every league ends up owned by tenant 1 forever.
Holding the window open is what expand-contract is for: at 0003 the schema accepts tenant-aware and
tenant-unaware writers, so Phase 37 converts call sites a few at a time with the suite green.

Three things must move together when it lands: the migration file, `models.py`'s nullability, and
`test_the_leagues_tenant_column_is_still_nullable`. The parity test in `tests/test_spend.py` fails if
only one or two of them do.

## Two defects the migration had, both of which reported success

Neither is specific to this revision. Both are properties of the migration environment, so every
existing and future `batch_alter_table` had them.

**1. The batch rebuild cascade-deleted every child row.** SQLite's `DROP TABLE`, with foreign keys
enforced, performs an implicit `DELETE` that fires `ON DELETE CASCADE`. Alembic's batch mode rebuilds
a table by copy-drop-rename. `leagues` is the parent of nine cascading children. `alembic upgrade
head` emptied all nine and exited zero.

Measured, same database, one line changed:

| | `leagues` | `teams` | `PRAGMA foreign_key_check` |
| --- | --- | --- | --- |
| enforcement OFF during migration | 2 rows, both backfilled | **2 rows** | clean |
| enforcement ON during migration | 2 rows, both backfilled | **0 rows** | clean |

**`foreign_key_check` is clean in both columns.** The rows are gone, not dangling, so the obvious
guard never sees it — and neither does a schema-comparison test, because the schema is right either
way. The only thing that distinguishes the two is rows put in beforehand. An empty-database probe
passes.

SQLite's own documented table-rebuild procedure turns enforcement off first, for this reason. `env.py`
now does, and runs `foreign_key_check` before commit as the other half of the bargain.

**No existing database was ever damaged by this, and that is luck rather than design.** Every batch
operation in 0001 and 0002 is on a table the same revision had just created -- empty, and with no
children. 0003 is the first batch operation in this project's history against a pre-existing table
that parents anything. The defect was latent from the day batch mode was switched on.

**2. The guard's first version made the migrations commit nothing.** `PRAGMA foreign_keys=OFF` issued
through the connection implicitly BEGINs a SQLAlchemy transaction. Alembic's `begin_transaction()`
then returns a no-op context manager and **nothing is ever committed**. `alembic upgrade 0002` logged
`Running upgrade 0001 -> 0002` against a database it left empty; the next command found out by failing
with `table accounts already exists`.

That one was caught by the read-back assertion in `_set_sqlite_fk_enforcement`, which exists because
`PRAGMA foreign_keys` is silently a no-op inside a transaction — a guard that can quietly fail to
apply is worse than no guard, since the migration reports success either way. It fired on the
function's own restore-to-ON call, which is how the transaction was found at all.

Both are instances of the defect class this project has now recorded forty-plus times: **a check green
while establishing something other than what it claims.**

## The evidence

`tests/test_migration_preserves_data.py` — 2 tests. Seeds child rows at 0002, upgrades to head,
asserts they survive. **Control-removal: flip the one argument in `env.py` and it fails with `teams`
empty while every other test in the suite stays green.** A companion test asserts `teams` still
cascades off `leagues`, so the preservation test cannot go green because nothing would have deleted
the rows anyway.

It also asserts `alembic_version` is non-empty when read from a *fresh* connection, which is defect 2.

## Classification

`tests/test_tenant_classification.py` — now 17 tests. `leagues` moved from `NOT YET SCOPED` to
`tenant_id`. `tenants`, `users` and `memberships` classified **global** — by construction, not
oversight: scoping the tenant list to a tenant is circular, and one user may belong to several
tenants, so it is `memberships`, the join, that carries the authorization fact. "Global" here means
"has no tenant_id column", not "public".

Known-unprotected is now `{accounts, raw_cache, ai_spend_months, ai_spend_entries}` — four tables that
reach no tenant by any path. The spend ledger is a decision to make, not a column to add: the
UTC-month ceiling is currently shared, so one tenant's spend consumes every tenant's budget.

**Do not read `"leagues": "tenant_id"` as "leagues are isolated."** The column exists; nothing yet
requires it to be filled, and no RLS policy exists outside the prototype.

## Suite

**835 passed / 0 failed**, ruff clean, excluding `test_recovery*.py`. Those 89 failures are
environmental and predate this work: `api/recovery.py` requires `<repo>/.venv/bin/python`, which in
this repository is a symlink to `/Library/Frameworks/Python.framework/...` — a macOS path that does
not exist inside the Linux device VM. The failure is raised before any database work.
