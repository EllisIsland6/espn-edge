# Phase 37 — Tenant API Retrofit (narrowed; offline evidence)

Source plan: `docs/sprint-9/06-phases.md`, Phase 37 — "disposition the measured **122 sensitive query
call sites across 19 files** and **50 unowned data-returning routes** so every read/write derives
ownership from the authenticated tenant transaction." Sized at 7 sessions / 155 operator minutes.

**Status: the seam is built and proven; the identity half is not built and is not pretended.**

## The measurement that changed the shape of the phase

Before writing anything, I counted where the application actually obtains a database session.

| | Count |
| --- | ---: |
| `SessionLocal()` constructed in **application** code | **2** — and both are the seams themselves |
| `SessionLocal()` constructed in **tests** | 28 |
| `League(...)` constructed in **application** code | **2** (`routers/leagues.py`, `verify.py`) |
| Routes discovered | **57**, of which 46 take a session |

Every router obtains its session from `db.get_session`. Every service and script goes through
`db.session_scope`. So the retrofit is **two functions, not a hundred and twenty-two edits** — and
that is not a shortcut. Adding `.where(tenant_id == ...)` at 122 call sites is 122 chances to forget
one, and the one forgotten is the leak. Phase 36's own contract already says which control is
load-bearing: **row-level security remains the safety boundary; explicit predicates aid plans and
readability but are not the isolation control.** The application's job is to bind the tenant
reliably. The database's job is to refuse everything when it is not bound.

The "37 `League(...)` sites across 18 files" figure I carried out of Phase 36 was wrong in the way
that matters: 35 of them are in tests or are different classes (`DiscoveredLeague`, `EspnApiLeague`).
Two are real. Counting a grep is not counting the call sites.

## What was built

**`api/tenancy.py`** — the seam.

- `resolve_tenant_id` reads **two** rows, not one. `LIMIT 1` would silently serve the lowest id the
  day a second tenant exists, which is the exact failure this phase is about, and it would do it
  without a symptom. Zero tenants and two tenants both raise `TenantNotResolved`.
- `bind_session` sets the tenant **and keeps it set**. A single `set_config(..., true)` is not
  enough: transaction-local means the binding dies with the transaction, so a handler that commits
  half way through runs the rest of its statements with no tenant. Under RLS that second half reads
  nothing — a mysteriously empty result, not an error. The binding is re-applied on every
  transaction the session begins, through a listener attached to that session instance.
- `_apply_guc` issues `SELECT set_config(:name, :value, true)`. The third argument is `is_local` and
  it is the whole control: `false` scopes the value to the connection's session, so under a pool the
  next request to borrow that connection inherits the previous request's tenant — the pool-reuse
  attack Phase 36 names. It would pass any test that checks the tenant is set, because it **is** set.
  `SET LOCAL app.tenant_id = :t` is not usable: it takes no bind parameter, so the tenant would have
  to be interpolated into SQL text.
- `ensure_default_tenant`, plus an `after_create` hook on the `tenants` table. This is the
  `create_all` path's equivalent of alembic 0003's backfill. A DDL hook that inserts a row is
  unusual and worth being uneasy about; it is scoped to one row, one table, at creation only, and
  `resolve_tenant_id` still refuses outright if a second tenant ever appears. Without it the
  guarantee that holds in production (a migrated database always has its tenant) does not hold in a
  suite that resets the schema inside dozens of individual fixtures — and a guarantee that has to be
  remembered at each of them is not a guarantee.

**`api/db.py`** — both seams bind before yielding. `session_scope` included: background work is a
named attack in Phase 36's contract, because a job outside a request has no ambient tenant and the
historical answer (run as owner, filter by hand) is how a scheduled export reads every tenant.

**The write path** — `POST /api/leagues` and `verify.py` now set `tenant_id` from the session's bound
tenant. Never from the request body: a client-supplied tenant id is a client-chosen owner.

## Evidence, and what breaks when each control is removed

22 new tests. A passing test is not evidence a control works; the evidence is that it fails when the
control is removed. Each was removed and the suite re-run.

| Control removed | What fails |
| --- | --- |
| the bind in `_bound_session` | both seam tests — `TenantNotResolved: this session never went through the tenant seam` |
| `set_config` third argument `true` → `false` | the transaction-local test, on the statement text |
| the `after_begin` re-bind listener | the after-commit test — the binding is never applied |
| a declared-sessionless route opens a session | `GET /api/health is declared sessionless but checked out 1 database connection(s)` |
| `tenant_id=current_tenant_id(session)` on league creation | the ownership test — "created without a tenant" |

**`tests/test_route_tenant_inventory.py`** is the structural answer to the "50 unowned routes"
acceptance item. Every route must either take a tenant-bound session or appear in
`SESSIONLESS_ROUTES` with a reason — so a new route fails the build until someone decides. The
eleven sessionless routes are FastAPI's own four, the app root, health, the AI feature-flag read
(`AiService(session=None)`), two static image passthroughs, and two operator control-plane routes.

That list would be prose anyone could add to, so the safe ones are **called** with a connection-pool
checkout counter running and asserted to open zero connections. A handler that opens its own session
three helpers down is caught whatever the dict says about it.

**A vacuous pass, caught while writing the file.** This FastAPI version (0.141.1) keeps included
routers as nested `_IncludedRouter` objects rather than flattening them into `app.routes`, so the
obvious walk finds **five** routes — all of them FastAPI's own — and every assertion passes against a
set containing no API route at all. `test_the_route_table_is_actually_populated` now guards it. Same
defect as Phase 36's empty `Base.metadata`, one week apart, found the same way: by printing what the
collection actually contained instead of trusting that it was populated.

## What is NOT done, and must not be read as done

- **There is no authentication.** No session cookie, no bearer token, no `current_user`. The seam
  refuses to guess when more than one tenant exists, which is the correct behaviour for a
  single-operator deployment and is *not* multi-tenant serving. Real tenant identity needs the
  Cognito work Phase 36 explicitly excluded.
- **The RLS policies are still prototype-only.** They live in `docs/sprint-9/kernel/rls.sql`, not in
  a migration. Binding a tenant that no policy reads changes nothing by itself. The seam is the half
  that had to exist first; the policies are the half that enforces.
- **`accounts`, `raw_cache` and the spend ledger still have no policies at all** — the classification
  audit's finding, unchanged by this phase.
- **`leagues.tenant_id` is still nullable**, because `alembic/pending/0004` is parked until every
  writer supplies one. Both application writers now do; the column contracts when the rest of Phase
  37's read-path work is done.
- **The acceptance item "all 50 routes return 404/default-deny across colliding tenants" is not
  demonstrated.** It needs PostgreSQL, the policies applied, and two tenants holding colliding data.
  Offline, on SQLite, an isolation assertion would be a green tick establishing nothing.
- **28 raw `SessionLocal()` constructions in tests are unbound.** Recorded rather than closed:
  application code does it in exactly two places, which are the seams. `current_tenant_id` raises on
  such a session so it is never silent, and on PostgreSQL such a session reads nothing.

## Scope and guarantees

No ESPN call, no model call, no AWS call, no spend, no commits, no route removed, no response shape
changed. Suite **858 passed / 0 failed**, ruff clean. `test_recovery*` remains at its pre-existing 89
environmental failures — `api/recovery.py` requires `<repo>/.venv/bin/python`, a symlink to a macOS
framework path absent in the Linux device VM — unchanged by this work, verified by running those
files separately before and after.
