# ADR-004 — PostgreSQL job queue and durable database schedule

**Status:** accepted for the public baseline; SQS is a documented scale-up seam. **Date:** 2026-08-09.

## Context

The required behavior is durable at-least-once work, idempotency, retry/poison handling,
backpressure, fairness and missed-schedule recovery. Current volume is 115 private leagues/day and
synthetic public jobs; there is one worker and every useful job already needs PostgreSQL state. SQS
request pricing is $0 here, but SQS plus the relational outbox creates two-state delivery ambiguity.

## Options

| Option | Monthly delta | Trade-off |
| --- | ---: | --- |
| PostgreSQL queue + worker wake-up loop | **$0** on selected DB/host | Atomic job/business/schedule state and simple local testing; team owns leases, polling, fairness, poison state and cleanup. |
| EventBridge Scheduler + SQS Standard/fair queue + DLQ | **$0** at expected requests on public egress; private subnet adds `$7.30/AZ` SQS endpoint | Managed delivery/visibility and independent worker scale; requires outbox relay/reconciliation and a second state system. |
| EventBridge + Step Functions Standard | `$0.025/1,000` state transitions; roughly **$0.86/month** at 34.5k transitions before retries | Strong visual orchestration/audit; duplicates job state and is excessive for one linear league transaction. |
| In-process `BackgroundTasks`/memory queue | **$0** | Loses accepted work on crash and has no poison/backpressure contract; fails the requirement. |

SQS and Scheduler rates are current official
[SQS](https://aws.amazon.com/sqs/pricing/) and
[EventBridge](https://aws.amazon.com/eventbridge/pricing/) terms.

## Decision

Use `sync_jobs` as the queue and source of truth. Enqueue and business acceptance commit in one
transaction with a unique idempotency key. A worker claims one due row using
`FOR UPDATE SKIP LOCKED`, writes a lease owner/expiry/attempt, and commits before doing external work.
It heartbeats without holding a long transaction. Completion writes result/checkpoints in a new
tenant-scoped transaction; a crash leaves an expired lease that another attempt reclaims. The
underlying sync run ID makes replay a no-op after commit.

Forced RLS creates an intentional control-plane problem: a worker cannot select the next tenant before
it has tenant context. Solve it with a narrowly granted, `SECURITY DEFINER` `claim_next_job` function
whose search path is pinned and whose owner can access only queue/schedule tables, not league facts,
credentials, reports or memberships. The queue/schedule RLS policies explicitly allow that function
owner to see all queue-control rows; it is neither `BYPASSRLS` nor an owner of business tables. It
atomically applies fairness, claims a row and returns opaque job/tenant IDs. The worker then starts a normal transaction, sets that tenant context and must read
the claimed job under forced RLS before touching business data. Direct table-wide worker access and
`BYPASSRLS` are forbidden. Only the worker DB role can execute the function; web/user roles cannot.
The function, fixed search path, grants and role boundary get adversarial SQL tests.

Select work by eligibility, tenant virtual finish/last-served time, estimated request weight and age
so one portfolio cannot consume the queue. One active job per tenant and global worker/provider
semaphores enforce backpressure. After five attempts, set terminal `dead`, retain 14 days, alarm and
require an audited replay that creates a new job referencing the original. Poison is a state, not a
second unaudited table.

Schedule definitions and their last materialized windows live in PostgreSQL. A bounded wake-up loop
inside the worker periodically invokes a similarly narrow `enqueue_due_jobs(now)` database function;
the unique schedule-window key coalesces duplicates. If the host is down at 03:00, the first loop
after boot materializes missed due windows and lateness is observable. The loop is only a clock; the
database is the durable schedule. Manual and scheduled requests use the same enqueue contract. Public
jobs call the deterministic synthetic provider; private local jobs use the global 1-rps limiter. No
scheduled AI exists.

The queue is behind a repository interface so an SQS implementation can later preserve job IDs,
idempotency and result contracts. Do not implement both now.

## Consequences and cost

Incremental service cost is **$0/month**. The main benefit is atomicity: there is no commit-to-DB /
send-to-SQS failure window or relay. It still demonstrates leasing, at-least-once delivery,
idempotency, fairness, poison isolation and backpressure—the architecture skills, not a product logo.

Accepted failures: when PostgreSQL or the one host is down, jobs neither enqueue nor execute; queue
polling adds bounded database work; a bug in lease/fairness SQL is ours; jobs do not wait durably
outside the database during a database restore. Since every job result needs that same DB and the
99.0% target accepts restore, this is coherent at current scale.

## Revisit when

Move to EventBridge/SQS when workers scale independently across hosts/AZs, queue depth exceeds
10,000/day, polling/locking becomes measurable, accepting work during DB unavailability matters, or
a workload no longer depends on the relational transaction. Step Functions becomes credible when
one workflow has multiple independently retried branches/human waits worth visual orchestration.
