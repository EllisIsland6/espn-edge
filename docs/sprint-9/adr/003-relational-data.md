# ADR-003 — Single-AZ RDS PostgreSQL without Proxy

**Status:** accepted for the public synthetic baseline. **Date:** 2026-08-09.

## Context

PostgreSQL is required for forced RLS and migration evidence. Current cache-free relational data is
14.5 MB; even the 100× static-league projection is about 1.3 GB. Ninety-day snapshot retention, not
today's static rows, is the larger future term. The database needs five-minute RPO, 14-day PITR, a
restore drill and same-AZ low latency; 99.0% explicitly accepts restoration rather than a hot standby.

## Options

| Option | Monthly cost | Trade-off |
| --- | ---: | --- |
| RDS PostgreSQL `db.t4g.micro`, Single-AZ, 20 GiB gp3 | `$0.016 × 730 + 20 × $0.115` = **$13.98** | Managed PITR/patching and native PostgreSQL/RLS; 1 GiB burstable instance and no automatic standby. |
| Same RDS size, Multi-AZ instance deployment | Approximately **$27.96** for duplicate compute/storage | Automatic standby/failover; roughly doubles the data line for an availability target not selected. |
| Aurora Serverless v2 Standard | Warm floor `0.5 ACU × $0.12 × 730` = **$43.80**, plus storage/I/O | Distributed storage and scaling. Zero-ACU pause can remove compute cost but a persistent pool/queue prevents pause; resume is typically ~15s. |
| Neon Launch / Scale | Always-on 0.25 CU is about **$19.35 Launch** or **$40.52 Scale**, plus storage/history | Scale-to-zero and external management. Launch offers at most 7-day restore; Scale supplies 30 days but adds internet RTT/control-plane dependency. |
| PostgreSQL on the application EC2 host | **$0 incremental compute**, plus EBS/S3 backup work | Lowest bill and RTT, but one disk/host failure takes both service and database; achieving five-minute RPO and drills becomes solo-operated WAL infrastructure. |

Rates were checked against [RDS PostgreSQL](https://aws.amazon.com/rds/postgresql/pricing/),
[Aurora](https://aws.amazon.com/rds/aurora/pricing/) and [Neon](https://neon.com/pricing/). Aurora
zero-ACU resume behavior is documented by
[AWS](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html).

## Decision

Use current supported PostgreSQL on an encrypted Single-AZ `db.t4g.micro` with 20 GiB gp3 in the
same AZ as compute. Configure 14-day automated backups/PITR, deletion protection in production, a
pre-migration manual snapshot and a quarterly restore into an isolated DB. Keep the database private
and require TLS hostname verification.

Application/worker roles use distinct PostgreSQL users with IAM database authentication and forced
RLS; the migration role owns schema and is unavailable to runtime. RDS-managed master credentials
remain in Secrets Manager for one-off migration/recovery. Connections recycle before IAM tokens
expire. Pool size starts at web 5 + overflow 2 and worker 2, then follows measured connection wait
and the micro instance's memory—not framework defaults.

Do not deploy RDS Proxy. At the current $0.015/vCPU-hour and two-vCPU minimum shape it is about
**$21.90/month**, more than the database instance, and adds a hop before the N+1 is fixed. R9's
RLS-enabled benchmark is the only way to reopen it.

The reviewed Alembic baseline, `TIMESTAMPTZ` import, explicit partial predicates, sequences,
composite tenant FKs and RLS role tests remain release gates. PostgreSQL autovacuum and bounded
retention deletes are monitored; partitioning is deferred until measured delete/index cost requires
it.

`db.t4g.micro` is not configurable to Standard mode: RDS T4g PostgreSQL runs in Unlimited mode and
charges **$0.075 per surplus vCPU-hour** when average use over the rolling 24-hour window exceeds
baseline. Monitor AWS-published `CPUCreditBalance`, `CPUSurplusCreditBalance`,
`CPUSurplusCreditsCharged` and `CPUUtilization`; the database-health alarm fires on the first charged
surplus credit. At a calendar-month projection of $5 the operator profiles/staggers the nightly
work.

AWS Budgets is delayed and is not a hard spend cap. A separate non-VPC cost-guard Lambda runs every
five minutes, sums `CPUSurplusCreditsCharged` from the UTC month boundary, and at 6,400 charged
credits (106.67 vCPU-hours = **$8**) calls `StopDBInstance` on this one tagged/ARN-bound database and
notifies the operator. Its role can read this metric and stop this DB only; neither web nor worker
can stop RDS. The guard re-stops an automatic seven-day restart while the month remains tripped.
This deliberately trades public availability for the hard budget. About 8,640 invocations/month is
inside Lambda's 1M-request/400k-GB-second free tier; EventBridge schedule volume is also inside its
allowance. Resize/resume is a reviewed bill change
([Lambda pricing](https://aws.amazon.com/lambda/pricing/),
[RDS stop/restart behavior](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_StopDBInstance.html)).

The expected RDS line remains $13.98 because no charged surplus usage has been measured. Reserve
**$12/month** for credits: the $8 trip plus a conservative $3.60 full-CPU day if a rolling-24-hour
settlement lands immediately after the last poll, rounded up. Without this guard, the only baseline-
independent 31-day bound is `2 vCPU × 744h × $0.075 = $111.60`; adding it to the current baseline and
alarm reserve can exceed $150. The previously tempting $100.44 calculation imports EC2's published
10%-baseline table into RDS without an RDS-specific earn-rate source, so it is not used as the hard
ceiling. An applied second private stack would duplicate this variable exposure and must be repriced
and receive its own guard before approval.

The $12 reserve exceeds the $10 line-item review threshold. It is justified as the bounded failure
allowance for a managed service mode that cannot be changed to Standard—not as expected spend. A
non-burstable RDS class would replace a rarely used reserve with a much larger recurring compute
floor; an unguarded T4g would violate the ceiling.

Sources: [RDS T4g is Unlimited](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.Types.html),
[RDS PostgreSQL CPU-credit price](https://aws.amazon.com/rds/postgresql/pricing/),
[RDS credit metrics](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-metrics.html).

## Consequences and cost

Selected data cost is **$13.98/month**, above $10 and justified by managed continuous backups,
point-in-time recovery, patching and the exact PostgreSQL isolation semantics under review. Backup
storage up to the provisioned DB storage is expected to remain included; a conservative quarterly
four-hour restore drill adds **$0.03/month amortized** at the selected instance size. Cross-AZ data
transfer is $0 in the normal path.

Accepted failures: the DB/AZ can be unavailable for hours; restore needs operator action; 1 GiB RAM
can pressure bad queries and forced-Unlimited CPU can add up to the bounded surplus-credit charge;
the cost guard deliberately stops the public database/site at its trip; a major-version upgrade is
planned work. A five-minute RPO
does not imply a five-minute RTO. The 20-GiB allocation covers current and 100× static data, but the
retention metric—not allocation folklore—triggers growth review.

## Revisit when

Choose Multi-AZ when calendar RTO/99.9% becomes real, not when a diagram looks sparse. Resize when
FreeableMemory, swap, credit balance, connections or query p95 breach thresholds. Reconsider Aurora
when variable high peaks make scaling worth a >$29.82 warm-floor delta or a cold 15-second resume is
acceptable. Reconsider external PostgreSQL only with a measured RTT and a restore contract meeting
R7.
