---
name: tenant-postgres-gate
description: Validate ESPN Edge PostgreSQL, Alembic, tenant context, forced RLS, composite foreign keys, partial indexes, timestamps, JSONB, sequences, pooling, query budgets, restore, and N-1 compatibility. Use for any database model, migration, router/service query, job worker, export, restore, or tenant-scoped change.
---

# Tenant PostgreSQL Gate

## Preconditions

Read the accepted phase contract, `docs/sprint-9/00-current-state.md`, requirements R3/R4/R9, the
database/migration ADRs, current models, migrations, roles, and candidate diff. Use PostgreSQL with
the deployed non-owner runtime role; SQLite success is not release evidence.

## Required checks

1. **Migration authority:** Alembic empty upgrade, prior-revision upgrade, one migration runner,
   concurrent app startup with zero DDL, and reviewed catalog diff.
2. **Dialect correctness:** naive SQLite datetimes interpreted as UTC into `TIMESTAMPTZ`; validated
   JSONB/native booleans; identity sequences above imported maxima; FK orphans fail explicitly.
3. **Metric uniqueness:** all four nullable metric shapes coexist and duplicates within each shape
   fail under explicit PostgreSQL partial-index predicates.
4. **Tenant isolation:** missing context, guessed/colliding IDs, direct SQL, cross-tenant composite
   FKs, pooled-session reuse, job context, exports/AI/charts, and owner-versus-runtime-role attacks.
   Require `ENABLE/FORCE RLS`; app/worker roles cannot bypass it.
5. **Performance:** count and plan queries under the forced-RLS runtime role. Apply the active ≤25
   SELECT profiled-read and ≤50 ordinary-route budgets without hiding lazy loads.
6. **Compatibility/recovery:** release N-1 startup, four profiled reads and one job claim against
   release N's expanded schema; restore canary, FK/unique/partial-index integrity, and sequence checks.

## Output

Return pass/fail by gate with exact commands, role identity, catalog/plan evidence, candidate SHA,
and untested cases. Any RLS bypass, cross-tenant result, wrong predicate, failed upgrade, or unproven
rollback compatibility blocks the phase; do not compensate with application `WHERE` clauses alone.

