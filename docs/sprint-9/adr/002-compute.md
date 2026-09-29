# ADR-002 — one Graviton ECS-on-EC2 host for web and worker

**Status:** accepted for the public synthetic baseline. **Date:** 2026-08-09.

## Context

The current buffered export peaks at 435 MiB; R9 requires streaming and ≤256 MiB for the
representative export before cloud cutover. Read handlers include pandas/numpy, chart assembly and
Monte Carlo paths, and the database contract favors warm persistent connections. The artifact has
one operator, 1–50 synthetic users and no zero-downtime SLA. Compute must support an HTTP stream and
durable worker without a mandatory load balancer or NAT.

## Options

| Option | Comparable monthly cost | Trade-off |
| --- | ---: | --- |
| EC2 `t4g.small`, 20 GiB gp3, one IPv4 | `$0.0168 × 730 + $1.60 + $3.65` = **$17.51** | 2 vCPU/2 GiB, stable process and direct streaming; operator owns patching and single-host recovery. |
| ECS Fargate, separate 0.5-vCPU/1-GiB web and worker | About **$36.04** x86 task compute + **$7.30** two public IPv4; stable ingress normally adds an ALB from **$16.43**, total **≈$59.77** | Better deployment isolation and task replacement; too much fixed ingress/duplicate capacity for this target. |
| Lambda + API Gateway | Potentially **$0** inside Lambda's perpetual 1M request/400k GB-s allowance; HTTP API is $1/M after its time-limited allowance | Excellent idle cost, but connection churn, execution/streaming adapters, 15-minute jobs and the export contract force a large rewrite. |
| App Runner 1 vCPU/2 GiB | **$10.22** provisioned memory plus `$0.064/vCPU-hour` while active; AWS's 8-active-hours/day example is $25.50 | Stable HTTPS and managed scaling; a polling/background worker has awkward billing/lifecycle and needs another execution path. |
| Lightsail 2-GiB IPv4 bundle | **$12** | Predictable VM/storage/address bundle, but weaker VPC/IAM/SSM/RDS integration and a less transferable deployment path for a $5.51 saving. |

Prices use current official [EC2](https://aws.amazon.com/ec2/pricing/on-demand/),
[EBS](https://docs.aws.amazon.com/prescriptive-guidance/latest/optimize-costs-microsoft-workloads/ebs-migrate-gp2-gp3.html),
[Fargate](https://aws.amazon.com/fargate/pricing/),
[Lambda](https://aws.amazon.com/lambda/pricing/),
[App Runner](https://aws.amazon.com/apprunner/pricing/) and
[Lightsail](https://aws.amazon.com/lightsail/pricing/) rates. The temporary T4g free trial ending
2027 is excluded.

## Decision

Run one On-Demand `t4g.small` Amazon Linux instance with 20 GiB encrypted gp3 as an ECS EC2 capacity
provider/Auto Scaling group with desired capacity one in the selected AZ. ECS itself adds no control-
plane charge. Caddy runs as a minimal host service; FastAPI web and worker run as separate ECS tasks
with distinct task roles and explicit memory/CPU limits. The instance profile permits ECS/ECR/SSM
host operations but no application secret, Anthropic call or database login. The web process holds
the bounded PostgreSQL pool. The worker owns asynchronous jobs and yields/pauses when web memory or
load breaches a guardrail. No migration or provider work runs in the web request process.

The common EC2 kernel/root remains a trust boundary—task roles protect against an ordinary task
compromise, not a host-root compromise. Two fixed local bridge ports allow inactive/active web task
slots behind Caddy. A one-off migration task has a third, short-lived role. The worker wake-up loop
uses database schedule state; schedule durability does not depend on a host cron file.

CPU-heavy results that can be precomputed—including playoff simulation—become jobs and persisted
facts. Request-time chart assembly uses set-based queries and bounded vector operations. Do not hide
an unbounded process pool inside 2 GiB. One and two concurrent streamed exports must pass the target
Linux/cgroup measurement before deployment; otherwise raise to `t4g.medium` and record the roughly
**+$12.26/month** compute delta before changing the bill.

Use Graviton only after the locked Python/Node dependencies and image build pass arm64 CI. A failure
falls back to `t3.small`/x86 and is repriced; architecture does not depend on an emulated image.

Set `CpuCredits=standard` explicitly in the launch template. T4g otherwise launches as Unlimited by
default, where Linux surplus credits cost $0.04/vCPU-hour. Standard keeps the EC2 line fixed and
accepts throttling to the `t4g.small` baseline after the earned balance is exhausted. Monitor the
AWS-published `CPUCreditBalance` and `CPUUtilization`; alarm when the balance is below 48 credits
(two baseline-hours at the published 24-credit/hour earn rate) while CPU stays above the 20%
per-vCPU baseline for three 5-minute periods. The nightly job admission loop pauses CPU-heavy
simulation/recompute work at that alarm; request serving wins. This is an explicit fixed-cost
choice, not reliance on the account default
([EC2 default and configuration](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-how-to.html),
[T4g surplus rate](https://aws.amazon.com/ec2/pricing/on-demand/)).

Blue/green cutover has a different memory profile from steady state. The Linux/cgroup release gate
must measure the ECS agent, Caddy, worker, active web slot under the heaviest representative read,
and the candidate web slot through readiness and a canary at the same time. It also exercises one
active export while the candidate is resident; two simultaneous full exports across slots are not
an allowed cutover load. If the observed sum plus 25% headroom does not fit 2 GiB, either resize to
`t4g.medium` for the recorded **+$12.26/month**, or drain new requests for at most two minutes,
stop the old slot, start the new slot and accept a brief maintenance interruption. The latter is a
staged drain/replace, not blue/green and not “zero downtime.”

## Consequences and cost

Selected compute costs **$17.51/month**, which exceeds $10 and is justified because one allocation
provides the measured memory headroom, streaming origin, warm connection pool, scheduler and worker
without a $16.43 load balancer or second task. SSM Run Command/Session Manager replaces SSH at $0
for an EC2 node. The one host is simple to inspect and restore.

Accepted failures: host/AZ maintenance takes both API and worker down; worker and web share CPU/memory
despite cgroups; Standard-mode credit exhaustion throttles sustained Monte Carlo/import work rather
than adding cost; the operator owns monthly image/OS patching. ECS/Auto Scaling with desired count
one repairs instance failure only after
termination, EIP reassociation, boot, image pull and task health—it is not HA and is not described as
such. Host root can inspect all local tasks and their memory.

## Revisit when

Move web and worker to separate Fargate services when concurrent users or worker contention needs
independent scaling, a second AZ/ALB is already funded, or patch toil exceeds the savings. Revisit
Lambda for independently bounded event handlers, not for a wholesale FastAPI port. Resize when
Linux profiles, CPU-credit balance, OOM kills or p95 latency breach R9.
