# Phase 37b — Row-level security, landed as a migration and proven

Everything before this made a tenant *knowable*. This is what makes it *enforced*. Until 0005 runs,
`leagues.tenant_id` is a column the application fills in and nothing checks.

**27 of 27 cross-tenant attacks pass against the schema `alembic upgrade head` actually produces** —
not against hand-written SQL — run as `edge_app`, a LOGIN role that is `NOSUPERUSER NOBYPASSRLS`.
PostgreSQL 16.13, disposable cluster in an isolated cloud container.

## What landed

| Revision | Engines | What |
| --- | --- | --- |
| `0004_tenant_columns_for_credentials_and_cache` | all | `tenant_id` on `accounts` and `raw_cache`, nullable, indexed, FK'd, backfilled |
| `0005_row_level_security` | **PostgreSQL only** | `current_tenant()`, ENABLE + FORCE RLS and a policy on all 16 tenant tables |
| `0006_tenant_kernel_contract` | all | still parked in `alembic/pending/` — but now proven on PostgreSQL too (renumbered from 0004) |

`accounts` and `raw_cache` were the Phase 36 audit's first two findings: the most sensitive table in
the schema (swid + Fernet-encrypted `espn_s2`) and the raw payloads of private leagues, both with no
tenant column and therefore no possible policy. `accounts` reaches no league, so there was nothing to
scope it *by* — it needed a column of its own.

On SQLite 0005 does nothing, and says so, because SQLite has no row-level security. A SQLite run must
never be mistaken for a protected database.

## Three corrections to the design Phase 36 proved

The prototype in `docs/sprint-9/kernel/rls.sql` denied 13 of 13 attacks and is still wrong in three
places. All three were found by reading it against the table list — the audit, not the attack suite —
and all three are now measured rather than argued.

**1. `current_roster_entries` has no `league_id`.** The prototype policy filtered on one. Phase 36's
run reported that `CREATE POLICY` failing and the error was swallowed by a `grep -v` filtering
routine `psql` notices. The table ended up with FORCE and no policy — denying everything, broken
closed rather than leaky, and invisible because the attack suite probed `teams`. It now reaches its
league through `snapshot_id`, and attack A11 reads exactly one row.

**2. `tenants` and `memberships` had `USING` and no `WITH CHECK`.** `USING` scopes reads; without
`WITH CHECK`, writes are unconstrained. On `memberships` that is not a data leak, it is **privilege
escalation** — one tenant may INSERT a membership granting itself another tenant's data. A10 now
gets `42501`, and A10c proves a membership inside its own tenant is still accepted.

**3. `users` had no policy at all.** Global by construction — one person may belong to several
tenants — but "no tenant column" is not "safe to read": under a tenant-scoped role every email in the
system was selectable. A user is now reachable only through a membership in the current tenant.

## The evidence

`docs/sprint-9/kernel/attacks_migrated.py`. Every denial is paired with a control proving the same
operation inside the tenant's own scope is **accepted** — a refusal alone cannot distinguish a
working policy from a missing grant or a typo'd column name, which is exactly how correction 1
hid for a week.

Beyond the denials, three results are measurements rather than passes:

- **A13 / A13b — the `is_local` boolean, measured.** With `set_config(..., true)` the binding dies at
  commit and the same connection then reads **0 rows**. With `false` it **survives the commit** and
  the connection still sees the tenant's rows with nothing bound in the current transaction. That is
  the pool-reuse leak, demonstrated side by side, and it is the one-character difference in
  `api/tenancy._apply_guc`.
- **A14 — a superuser ignores FORCE RLS entirely**, reading every row with no tenant set. This is
  P33-1, and it is why `api/db.assert_runtime_role_is_constrained()` refuses to start on a superuser
  or `BYPASSRLS` role. Policies can be perfect and isolation silently off.
- **A8b — a hole, measured rather than asserted.** `raw_cache`'s primary key is still `key` alone, so
  two tenants cannot hold the same cache key: the second INSERT fails with `23505`, **not** `42501`.
  A uniqueness error is not something RLS hides, so tenant B learns that tenant A holds that key —
  and since a key carries a league id and a hashed SWID, that is an enumeration oracle. The fix is a
  composite `(tenant_id, key)` primary key and it cannot land while half the rows share a NULL
  tenant, so it belongs with the contract step.

## P36-1, reproduced by accident

The first attack run could not seed its own fixture: inserting the same ESPN league for two tenants
failed on `uq_league_season`. That is P36-1 exactly — **at head, the colliding-tenant attack cannot
be staged**, because the constraint the contract step drops is still there. So the suite was run
twice: at 0005 with distinct ids (27/27), and again after applying 0006 with the collision actually
present (27/27). The second run also verified 0006 on PostgreSQL, which had only been proven on
SQLite: `uq_league_season` gone, `uq_league_tenant_season` present, `tenant_id` NOT NULL.

## Deliberately left alone, with the consequence stated

**The AI spend ledger** gets no policies. The UTC-month ceiling it enforces is *shared across
tenants*; a per-tenant policy would give each tenant its own ceiling, which is a different and larger
product decision. The consequence is not hidden: one tenant's spend consumes every tenant's budget,
and a tenant-scoped role can read and modify another tenant's ledger rows. The operator grant should
withhold DELETE on both tables.

**Global reference data** — `players`, `nflverse_player_maps`, `adp_snapshots`, `opportunity_weeks`,
`opportunity_imports` — is the same rows for every tenant. RLS there would cost plans and protect
nothing. The seven unpolicied tables in the database are exactly these five plus the two ledger
tables; nothing else was missed.

**Roles and grants are not created by the migration.** Roles are cluster-level, a migration running
as the schema owner may hold no CREATEROLE, and a role invented by a migration is a role no
deployment knows about. Two consequences to carry: **a table added by a later migration has no grants
for the app role until an operator issues them**, and **none of this binds if the application
connects as the owner or a BYPASSRLS role.**

## Still not done

- No authentication. The seam refuses to serve when more than one tenant exists, so this is
  single-operator scoping with multi-tenant enforcement underneath it, not multi-tenant serving.
- `leagues.tenant_id`, `accounts.tenant_id` and `raw_cache.tenant_id` remain nullable; the contract
  step is parked until the ~32 test `League(...)` constructions supply a tenant.
- `raw_cache`'s composite primary key (A8b above).
- The offline suite still cannot test any of this. SQLite has no RLS, and an offline isolation
  assertion would be a green tick establishing nothing.

## Scope and guarantees

No ESPN call, no model call, no AWS call, no spend, no commits. The PostgreSQL cluster was disposable
and lived only in an isolated cloud container; nothing was installed on the operator's machine.
Device suite unchanged at **858 passed / 0 failed**, ruff clean.
