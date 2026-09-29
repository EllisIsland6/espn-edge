# ADR-006 — Secrets Manager for app secrets; KMS only for conditional private custody

**Status:** accepted. **Date:** 2026-08-09.

## Context

The public stack needs an Anthropic API key and a recoverable RDS master/migration credential. Runtime
database connections can use IAM authentication. It holds no ESPN credential. A separate private AWS
stack, if ever applied, needs unattended cookie decryption whose public-account principals can never
perform.

## Options

| Option | Monthly cost | Trade-off |
| --- | ---: | --- |
| Secrets Manager with AWS-managed encryption | **$0.40/secret + $0.05/10k calls** | Resource policies, rotation integration and audit; nonzero fixed charge. |
| SSM Parameter Store SecureString | Standard parameter **$0**; KMS/API charges as applicable | Cheap configuration store, but weaker rotation/version workflow for credentials. |
| Encrypted environment/GitHub secret injected at deploy | **$0 AWS** | Secret appears in deployment/task/process environment and rotation requires redeploy; broader accidental disclosure. |
| Self-hosted Vault | Software $0, but another durable HA service/ops allocation | Strong workflows at a cost and failure surface larger than the app. |

## Decision

The public account stores two Secrets Manager secrets: Anthropic API key and RDS-managed master
credential. The ECS web task role can retrieve only Anthropic at process initialization; the one-off
migration/recovery task role alone can retrieve the DB master. The worker task role can retrieve
neither. Web/worker use IAM DB authentication and cannot read the master. The EC2 instance profile
has no application-secret permission. Cache secrets only in process for their required lifetime and
never log secret ARN versions. ECS task roles do not protect secrets from a compromise of host root;
the one EC2 host is the public application's trusted compute boundary.

Use AWS-managed service keys for public RDS, S3, EBS, ECR and Secrets Manager encryption: a customer-
managed KMS key would add $1/month without changing the public administrator blast radius. Non-secret
settings use typed config and free Standard Parameter Store where useful.

The **unapplied private-account module** creates one customer-managed KMS key and envelope-encrypts
the operator ESPN cookie bundle. Its key policy directly names only the private enrollment and worker
roles; the public account, organization principals and account-wide wildcards receive no decrypt
path. `tenant_id`/`account_id` are non-secret encryption context. KMS outage fails sync closed.

Local private mode uses the OS keychain for its Fernet/data key and full-disk encryption for cache;
no AWS key exists. Do not claim Python plaintext zeroization.

## Consequences and cost

Public cost is **≤$0.81/month**: two secrets at $0.40 plus ≤2,000 retrievals. Conditional private AWS
adds **$1.00/month** KMS key cost and expected requests remain below the 20,000-request free tier.
Prices: [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[KMS](https://aws.amazon.com/kms/pricing/).

Accepted failures: a Secrets Manager outage prevents a cold start/migration and makes AI cached-only;
an already-running process has a temporarily cached Anthropic key; AWS account administrators can
ultimately change public secret policy; the private AWS root remains a custody trust. IAM DB token
generation/expiry makes pool configuration more complex.

## Revisit when

Adopt a dedicated vault only with multiple platforms/issuers and an operating owner. Add a public
CMK only for a concrete cross-account/key-deletion/audit requirement. Rotate immediately on suspected
exposure and rehearse DB-master and Anthropic rotation separately.
