# ADR-001 — public-egress, single-AZ network without NAT

**Status:** accepted for the public synthetic baseline. **Date:** 2026-08-09.

## Context

The application needs TLS egress to Cognito's public OAuth/JWKS domain and Anthropic, plus selected
AWS APIs. PostgreSQL must not be publicly reachable. The measured read paths make same-AZ database
latency material, the availability target is 99.0% without an SLA, and a NAT gateway would consume
$36.50/month before application compute. B1 contains the complete unit-price derivation.

## Options

| Option | Monthly network delta | What it buys / loses |
| --- | ---: | --- |
| Private compute + one NAT gateway | **$36.50 + $0.045/GB** | Familiar private-subnet egress. One-AZ NAT remains a failure domain; two AZs cost $73 before data. |
| Private compute + interface endpoints | Three endpoints are **$21.90/AZ**; viable container set is at least **$36.50/AZ** | Private AWS API paths, but no Anthropic or Cognito user-pool-domain path. It is not a complete substitute for egress. |
| Public-subnet compute + restrictive security group | **$3.65** for one public IPv4; IGW and S3 gateway endpoint **$0** | Cheapest complete egress. The instance has a public address and outbound 443 cannot be restricted by DNS in a security group. |
| IPv6-only/effectively private compute | Potentially **$0** address/NAT | Depends on every external host and operational tool supporting IPv6 and changes the failure/debug surface. Not proven for the unofficial providers. |

## Decision

Use one VPC in `us-east-1` with:

- one application subnet in the chosen AZ, routed to an internet gateway; the single EC2 origin has
  one Elastic IP;
- private database subnets in two AZs because RDS requires a two-AZ subnet group, while the actual
  Single-AZ instance is pinned to the application's AZ;
- no NAT gateway, interface endpoint, public RDS address or bastion;
- a free S3 gateway endpoint on the application route table and restrictive bucket endpoint
  policies; and
- VPC DNS enabled. NACLs remain defaults; security groups carry the stateful policy.

The origin security group permits inbound TCP/443 only from AWS's CloudFront origin-facing managed
prefix list. There is no SSH or public port 80. CloudFront connects to Caddy over HTTPS; Caddy gets
its origin certificate using DNS-01 and a role limited to the one Route 53 record. A fixed origin
header/Host check rejects traffic relayed through another distribution. The instance allows
outbound DNS to the VPC resolver, PostgreSQL to the DB security group, HTTPS to the internet and
NTP/OS update paths as required. Application provider hosts are code/config allowlists; user input
can never select a destination.

The DB security group accepts 5432 only from the application security group. No rule identifies an
IP address. Compute and primary database remain same-AZ; a restore into another AZ either accepts
cross-AZ latency temporarily or redeploys compute beside it.

## Consequences and cost

Fixed selected network cost is **$3.65/month**, saving **$32.85/month** versus one NAT and at least
**$32.85/month** versus the smallest viable five-endpoint container set. S3 endpoint cost is $0.
Normal service and transfer meters remain in their service rows. The design retains one stable
address for origin DNS and egress audit.

Security is based on no arbitrary inbound path, patched minimal OS/container images, IAM roles,
TLS, application destination allowlists and redacted egress telemetry—not on the word “private.”
The public IP increases kernel/network blast radius and outbound 443 is broader than a hostname
allowlist. That is the accepted failure mode. An instance or selected-AZ failure makes the API and
worker unavailable until recovery; this matches the 99.0% target, not a stronger one.

## Revisit when

Add NAT or a managed egress proxy when untrusted code runs on compute, compliance requires network-
enforced destination filtering, two application AZs are funded, or public-address exposure causes
an incident. Reconsider IPv6-only after every required external endpoint is continuously tested
over IPv6. Re-benchmark same-AZ placement before enabling Multi-AZ RDS or RDS Proxy.

