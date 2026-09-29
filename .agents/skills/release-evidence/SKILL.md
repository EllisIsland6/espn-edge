---
name: release-evidence
description: Assemble and verify ESPN Edge preflight, migration, deployment, observability, recovery, cost, compatibility, and rollback evidence for a candidate. Use for Phase 43–45 rehearsals, launch or rollback decisions, risky migrations, restore drills, and any request to declare a release ready.
---

# Release Evidence

## Preconditions

Read the accepted phase contract, immutable candidate SHA/digests, Stage 4 migration runbook, relevant
ADRs, active write lease, and prior release manifest. This skill assembles evidence; it never deploys
or grants approval.

## Manifest

Collect and verify:

- contract/base/candidate SHA, image and SPA digests, Alembic revision, Terraform plan hash, changed
  paths, ownership, cost delta, and rollback-safe declaration;
- all offline CI gates, PostgreSQL/RLS attacks, query/export budgets, fixture provenance, and AI
  ledger concurrency/failure cases;
- N-1 image on N schema: startup, readiness, four profiled reads, and one job claim;
- private cache/credential-free restore and public PITR canaries with row/constraint/index/sequence
  evidence and actual RPO/RTO timing;
- post-candidate datapoints for every custom metric—not alarm state alone—plus external EC2/RDS
  health, missing-as-breaching behavior, worker no-progress, certificate expiry, and backup age;
- EC2/RDS CPU credits, memory/cutover headroom, database connections, 5xx/latency, cost guard, AI
  ledger, Cost Explorer, and every named rollback/abort threshold;
- unmeasured/live items with bounded Phase 43/44 methods and separate human authorization.

## Decision output

Return `ready`, `not ready`, or `rollback safety unproven` by contract clause. Include exact evidence
paths/hashes, timestamps, commands, environment, failures, `INSUFFICIENT_DATA` states, assumptions,
and operator actions still required. Treat absent custom telemetry, isolation failure, unpriced
resources, incompatible N-1, failed restore, or an expired write lease as blocking. Never apply,
cut over, delete, migrate, spend, or convert a model's confidence into approval.

