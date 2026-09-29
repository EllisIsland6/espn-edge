# ADR-009 — bounded CloudWatch/X-Ray with OpenTelemetry

**Status:** accepted. **Date:** 2026-08-09.

## Context

One operator needs evidence of silent sync failure, queue staleness, RPO lag, isolation failures and
AI spend without running an observability platform. Tenant IDs are useful in logs/traces but would
explode metric cardinality. Stage 2 caps logs, metrics, alarms and traces at current free allowances.

## Options

| Option | Monthly cost at required volume | Trade-off |
| --- | ---: | --- |
| CloudWatch Logs/metrics/alarms + X-Ray, OTel instrumentation | **$0 expected** within 5 GB logs, 10 custom metrics, 10 alarms and 100k traces | Native IAM/service metrics and no new vendor; query/UX less polished and overruns can become expensive. |
| Sentry SaaS plus CloudWatch infrastructure metrics | Developer tier **$0**, paid tiers/volume add external cost | Better exception/release UX; a second telemetry trust boundary and still needs AWS operational metrics. |
| Self-hosted Prometheus/Grafana/Loki | Software **$0**, but another instance/storage/backup allocation | Maximum control; operational and memory burden is disproportionate to one host. |
| Logs only | Potentially **$0** | Cannot reliably detect stale-but-quiet sync or connect request/DB/job/provider latency. |

## Decision

Instrument FastAPI, SQLAlchemy, job leases, synthetic/provider calls, Anthropic and KMS adapters with
OpenTelemetry. Run the lightweight collector/CloudWatch agent on EC2; emit redacted JSON logs and
sample bounded successful traces while retaining all errors. SQL text may be normalized, never
parameters. Tenant ID stays searchable in logs/traces and never becomes a metric dimension.

The public stack's ten low-cardinality custom series are `worker_heartbeat`, `sync_due_leagues`,
`sync_clean_succeeded_leagues`, `sync_oldest_success_age_seconds`,
`job_oldest_eligible_age_seconds`, `dead_job_count`, `provider_pipeline_failure_count`,
`ai_authorized_spend_microdollars`, `restore_drill_age_days` and
`origin_certificate_days_to_expiry`. The provider-failure series aggregates schema and recompute
failure counts; the structured event code distinguishes them. Partial syncs and AI rejections stay
in logs because due-versus-clean-success and the spend ledger are the actionable gauges. Public
mode has no ESPN calls. If private AWS is ever applied in its separate account, that account uses
the ESPN-specific R18 series without consuming public cardinality.

Every five minutes a telemetry loop separate from the queue-claim loop publishes every applicable
worker gauge, including healthy zeros. Every alarm whose source is an application/host-published
metric sets `treat_missing_data = "breaching"`; fast gauges use 3-of-4 five-minute periods so a
normal deploy restart under ten minutes does not page but approximately 15 minutes of silence does.
Daily certificate/restore gauges use 2-of-3 one-day periods. This is necessary because CloudWatch's
default missing-data treatment can leave an alarm in `INSUFFICIENT_DATA`
([CloudWatch missing-data behavior](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarms-and-missing-data.html)).

Nine alarm families, composed from the custom series and AWS-published metrics, notify one operator
SNS topic:

1. **host health:** EC2 `StatusCheckFailed_Instance OR StatusCheckFailed_System`, or
   `CPUCreditBalance <48` while CPU exceeds baseline;
2. **database health/credits:** RDS `DatabaseConnections <1` or missing, or any
   `CPUSurplusCreditsCharged`;
3. **worker heartbeat:** missing/not-one for 3-of-4 periods;
4. **sync staleness:** due-minus-clean-success remains positive after the window, or oldest clean
   success exceeds 26 hours;
5. **queue poison/stall:** oldest eligible job exceeds two hours or dead jobs exceed zero;
6. **pipeline failure:** provider schema/recompute failure count exceeds zero;
7. **AI spend:** authorized spend reaches 80%; the ledger rejects at 100%, so a second alarm adds no
   detection value;
8. **recovery:** `LatestRestorableTime` lag exceeds ten minutes or the quarterly restore drill is
   overdue; and
9. **origin certificate:** served certificate has 21 days or fewer remaining, or daily publication
   is missing.

EC2/RDS metrics are published by AWS rather than by the host, so host death is observed outside the
failed boundary. EventBridge AWS service events for EC2 state change, ECS task `STOPPED` and RDS
failure/failover also notify the topic; they are event signals, not substitutes for absence alarms.
`CPUCreditBalance`, `CPUSurplusCreditBalance`, `CPUSurplusCreditsCharged`, `CPUUtilization`,
`FreeableMemory`, connections, storage and backup lag remain on the operator dashboard without being
republished as custom metrics.

### Failure-to-signal matrix

| Scenario | Expected signal order | Is silent failure covered? |
| --- | --- | --- |
| EC2 host stop/system failure | EC2 state-change event in seconds/minutes; AWS status-check alarm at the next evaluation; worker/custom-metric missing alarm at about 15 minutes; RDS connections then fall below one. | Yes; neither traffic nor the worker must publish. |
| Web or worker container crash loop | ECS task-`STOPPED` event on each crash; worker heartbeat missing at about 15 minutes if it is the worker; API origin returns 503 if it is web. | Yes; task events are external to the container. |
| Database unreachable | RDS event when AWS detects an instance failure; `DatabaseConnections` low/missing; request health fails; queue/sync gauges breach or become missing. | Yes, including a security-group/network break that leaves the host healthy. |
| Claim loop deadlocks, host/process telemetry healthy | `worker_heartbeat=1`; `job_oldest_eligible_age_seconds` crosses two hours. If the scheduler also stops materializing jobs, due-versus-success/oldest-success catches it; if the entire process loop freezes, heartbeat is missing first. | Yes. The two-hour job-age bound is the detection latency for a live process that never claims. |

So the original eight alarms did **not** reliably detect a worker that was alive but never claiming:
a heartbeat or zero-valued queue metric only proves the publisher ran. The amended design detects an
eligible row whose age increases while heartbeat stays healthy, and independently detects the
absence of expected clean syncs when no row was enqueued. When no work is due, no queue alarm is
correct behavior.

One AWS Budget at 80% and 100% of the $150 ceiling and Cost Anomaly Detection provide wallet alarms;
they do not auto-delete resources.

Logs retain 14 days, application audit events retain 90 days in PostgreSQL, and neither contains
cookies, owner names, prompt content, response bodies or raw payloads. A monthly cost review checks
actual log bytes, metric series, alarms and trace count before adding any signal.

## Consequences and cost

Selected cost is **$0/month expected** under the explicit free-tier caps, but reserve **up to
$0.50/month** because metric-math inputs and the certificate/credit additions can exceed the ten
free standard alarm metrics even while there are only ten custom metric series
([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)). Standard SNS email and AWS
service events at this volume are expected $0. No high-cardinality metric, Application Signals,
Container Insights, full SQL logs, paid dashboard or third-party APM is enabled.

Accepted failures: trace sampling can miss a successful slow path; CloudWatch regional failure hides
telemetry with the workload; 14-day logs limit retrospective debugging; staying in the free tier
requires discipline. Errors are retained, but a process crash before flush can lose its last spans.

## Revisit when

Add Sentry when error/release triage time has measured value. Buy more CloudWatch signal when an
incident question cannot be answered within current caps. Use self-hosted metrics only after the
application already needs a multi-node operations platform.
