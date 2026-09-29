# ADR-011 — extend GitHub Actions with OIDC, ECR and SSM deployment

**Status:** accepted. **Date:** 2026-08-09.

## Context

The repository already runs backend pytest/ruff and frontend typecheck/build/Playwright in
`.github/workflows/ci.yml`. The next pipeline must add synthetic provenance, PostgreSQL/RLS,
multi-architecture image, Terraform and safe single-host deployment without long-lived AWS keys.
Stage 0b could not measure incremental GitHub minutes before those jobs exist.

## Options

| Option | Monthly platform delta | Trade-off |
| --- | ---: | --- |
| GitHub Actions + AWS OIDC + ECR + SSM | **$0 AWS execution**, ECR budget **$0.20** for ≤2 GB; GitHub minutes unmeasured under existing plan | Extends current workflow and review surface; GitHub remains external deploy control plane. |
| CodePipeline/CodeBuild | CodeBuild small Linux starts around **$0.005/build-minute** plus pipeline action billing | AWS-native audit/IAM; duplicates current CI and adds a second pipeline language/control plane. |
| Self-hosted runner on app EC2 | **$0 incremental compute** | CI competes with production CPU/memory and makes repository input a production execution path; rejected. |
| Manual local deploy | **$0** | Not repeatable/auditable and invites credential/schema mistakes. |

## Decision

Extend GitHub Actions in four gates:

1. **PR verify:** existing lanes plus Synthetic Fixture Factory provenance, PostgreSQL service
   container from empty Alembic, RLS attack suite using runtime role, query/memory gates, Terraform
   format/validate/policy and dependency/container scanning. Network remains disabled for ESPN tests.
2. **Build on protected main:** produce an arm64 image by digest, SBOM and signed provenance; run the
   image's smoke tests; push to private ECR. Upload content-addressed SPA assets under a release ID.
3. **Plan/approval:** GitHub OIDC assumes short-lived public deploy and migration roles. Terraform
   plan and Alembic upgrade plan are artifacts; a protected environment requires the solo operator's
   explicit approval. Private-account workflow has validate/plan permission only.
4. **Deploy:** update the inactive ECS-on-EC2 web service to the pinned task definition, run a one-off
   migration task under advisory lock, verify readiness and tenant canaries, switch Caddy with a
   narrowly scoped SSM command, then update index/config. Retain the prior task definition/image/assets
   for rollback. Worker changes start only after schema/app compatibility is proven.

No SSH, static AWS access key, automatic infrastructure apply, production ESPN live test or AI
generation exists in CI. ECR lifecycle retains the current and two prior digests; Trivy/static
scanning avoids paid Inspector enhanced scanning. Standard SSM Run Command on EC2 has no additional
charge ([Systems Manager pricing](https://aws.amazon.com/systems-manager/pricing/)); ECR private
storage is budgeted at the current $0.10/GB-month
([ECR pricing](https://aws.amazon.com/ecr/pricing/)).

## Consequences and cost

AWS CI/CD cost is **$0.20/month** ECR plus negligible S3 artifacts already budgeted; OIDC, standard
SSM and GitHub-to-AWS role assumption are $0. Incremental GitHub Actions cost is explicitly
**unmeasured**, not estimated: after implementation, record ten cold/warm runs as R19 requires and
compare billed minutes with the existing plan allowance.

Accepted failures: GitHub outage blocks deployment; a compromised approved workflow can assume its
narrow role; single-host blue/green does not survive host failure; migration rollback still depends
on expand/contract and backups. OIDC removes stored AWS keys, not the need to protect workflow files
and environments.

## Revisit when

Move deployment to an AWS-native pipeline when GitHub availability/trust becomes unacceptable or
multiple repos share a release train. Add a second host/managed service when zero-downtime becomes an
availability requirement rather than a cutover technique. Buy enhanced scanning after a concrete
coverage need.
