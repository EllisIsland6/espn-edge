# ADR-010 — Terraform with S3 state and reusable but unapplied private modules

**Status:** accepted. **Date:** 2026-08-09.

## Context

The architecture spans VPC, EC2, RDS, CloudFront/S3, Cognito, IAM, secrets, DNS, alarms and CI roles.
It needs a reproducible public stack and a separately scoped private-account design without deploying
the latter by accident. One developer must be able to inspect plans in an interview and destroy/rebuild
non-data resources predictably.

## Options

| Option | Monthly tool/state cost | Trade-off |
| --- | ---: | --- |
| Terraform | Binary/provider **$0**; tiny S3 state **≤$0.01** | Widely transferable declarative plan and multi-account modules; state/bootstrap/provider-upgrade burden. |
| AWS CDK (Python) | Framework/CloudFormation **$0**; bootstrap storage pennies | Same language and AWS-native constructs; synthesized templates/implicit resources can obscure costs and drift. |
| AWS SAM | Framework/CloudFormation **$0** | Excellent Lambda/API workflows, but selected EC2/RDS/CloudFront topology falls outside its strength. |
| Click-ops/scripts | Tool **$0** | Fast spike, but no reviewable desired state, drift proof or repeatable account boundary. |

## Decision

Use Terraform with pinned CLI/provider versions and a small bootstrap stack for an encrypted,
versioned S3 state bucket. Use S3 native state locking/lockfile; do not add DynamoDB solely from an
outdated template. Separate state, AWS accounts, GitHub environments and provider roles for public
production and any future private stack.

Modules expose cost-bearing toggles explicitly: NAT, endpoints, ALB, Multi-AZ, RDS Proxy, private
KMS/raw-cache and additional compute default off. A `public` root applies the selected architecture.
A `private` root composes reviewed modules but CI only validates/plans it; no apply role exists until
an ADR explicitly changes its status. State contains no application secret values; Secrets Manager
resources receive generated/externally supplied values through controlled bootstrap, not committed
variables or plan output.

Every pull request runs format, validate, provider lock verification, static policy checks and a
speculative plan with cost-bearing replacements highlighted. Production apply is manual through a
protected GitHub environment. Drift detection runs weekly read-only and opens an operator alert; it
does not auto-remediate.

## Consequences and cost

Terraform software costs **$0/month**. State S3 storage/version/request allowance is budgeted at
**$0.01/month**. No Terraform Cloud, Config recorder, Control Tower or policy platform is required.

Accepted failures: state/bootstrap is another recovery artifact; a bad provider upgrade can create
replacement plans; one operator remains able to approve destructive changes; AWS resources created
outside Terraform can drift until weekly detection. Versioned state helps rollback metadata but does
not restore an RDS database.

## Revisit when

Choose CDK when reusable AWS constructs materially reduce code and the team accepts CloudFormation as
the review unit. Add remote policy/approval tooling with multiple deployers or regulated controls.
Never apply the private root merely to prove it compiles.

