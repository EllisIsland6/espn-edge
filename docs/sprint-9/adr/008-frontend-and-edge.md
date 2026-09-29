# ADR-008 — S3/CloudFront single-origin SPA and API edge

**Status:** accepted. **Date:** 2026-08-09.

## Context

The Vite SPA is static, currently bakes `VITE_API_BASE` at build time, and the session requirement is
a secure host-only cookie. The API must stream large exports. The edge needs stable HTTPS without an
ALB, while the S3 bucket must stay private.

## Options

| Option | Monthly cost | Trade-off |
| --- | ---: | --- |
| S3 private origin + CloudFront PAYG | **≈$0.01 storage/requests + $0 CloudFront** within the perpetual 1-TB/10M-request allowance | Fine cache/security control and one origin; more explicit behaviors/deploy logic. |
| Amplify Hosting | Usually **<$1** at this artifact's storage/build/transfer, plan-dependent | Simpler deploy previews; duplicates existing GitHub workflow and hides edge/session routing details being demonstrated. |
| Vercel Hobby | **$0** at hobby limits | Excellent DX; adds an external control plane and still needs deliberate API/cookie routing. |
| Serve SPA from EC2/Caddy | **$0 incremental** | Simplest origin, but couples static availability/deploy/transfer to the one host and gives up CDN caching. |

## Decision

Use one CloudFront PAYG distribution and `app.example.com`:

- default behavior serves a private S3 bucket through Origin Access Control; hashed assets cache for
  one year immutable, while `index.html`, service worker and runtime config do not cache;
- `/api/*` and `/auth/*` forward uncached to `origin.app.example.com`, the EC2 Elastic IP/Caddy HTTPS
  origin. Forward required cookies, CSRF header, methods and response streams only; strip viewer
  headers the app does not need;
- the browser sees one origin, so app CORS is disabled and the `__Host-session` cookie is valid for
  SPA/API/callback paths. Cognito's domain remains an OAuth redirect boundary;
- CloudFront response-header policy enforces CSP, HSTS, frame/object/referrer/content-type and
  permissions controls. API also emits defensive headers;
- a non-secret `/config.json` contains environment/build/API schema version and relative paths.
  Build artifacts are identical across environments; no secret or environment URL enters Vite;
- deploy content-addressed assets first, then index/config. Retain the previous two asset sets and
  invalidate only index/config. Rollback restores the prior entry document.

Use Route 53 for the zone and alias, ACM for the viewer certificate, and a free DNS-01 origin
certificate. The origin DNS record is not advertised in the SPA and its security group accepts only
CloudFront origin-facing addresses; Host/origin-header checks add defense in depth.

Caddy normally attempts renewal around the last third of a certificate's lifetime (subject to the
CA's ACME Renewal Information window). A daily SSM association reads the certificate actually
served by `origin.app.example.com` and publishes `origin_certificate_days_to_expiry`. Its alarm
fires at **21 days remaining** and treats two missing daily samples out of three as breaching. This
leaves weeks for repair without generating an expected alert at Caddy's usual 30-day renewal
boundary. Host-status and ECS-stop signals cover a host that is already down; the expiry signal
covers a healthy host whose ACME path is broken
([Caddy renewal window](https://caddyserver.com/docs/caddyfile/directives/tls)).

The manual recovery runbook is tested before launch and quarterly: validate the served certificate,
Caddy ACME logs, clock and DNS delegation; validate the unchanged Caddyfile; temporarily assume a
break-glass role; force a graceful Caddy reprovision/reload; confirm the new `notAfter` from outside
the origin; then revoke the break-glass session. If Let's Encrypt is unavailable, use the configured
second ACME issuer rather than retrying into a CA rate limit. The normal instance role may change
only TXT records for the exact normalized `_acme-challenge.origin.app.example.com` name in one
hosted zone, with Route 53 conditions limiting action to `UPSERT`/`DELETE` and record type `TXT`.
It cannot edit the application, zone apex, NS, A or alias records
([Route 53 condition keys](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/specifying-conditions-route53.html)).

When the API origin is unreachable, cached S3 SPA assets remain available. `/api/*` and `/auth/*`
fail closed: CloudFront maps origin 502/504 to a static, tenant-data-free `503 service_unavailable` JSON
response with a near-zero error TTL; the SPA shows an outage state. It does not serve stale tenant
data, cached authorization or queued writes. Static availability during an API outage is a useful
failure mode, not a claim that the product is up.

## Consequences and cost

CloudFront is **$0 expected** below its perpetual 1-TB/10M-request monthly allowance; S3 static
storage/requests are budgeted at **$0.01**; Route 53 hosted zone is **$0.50** and alias queries to
CloudFront are $0; ACM is $0. Allow **$14/year = $1.17/month** for a new `.com` registration, or $0
incremental when reusing an owned domain. Total conservative edge/DNS/domain is **$1.68/month**.
The first 1,000 invalidation paths/month are expected to cover entry-only deploys.

Sources: [CloudFront allowance](https://aws.amazon.com/cloudfront/getting-started/),
[Route 53 pricing](https://aws.amazon.com/route53/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/).

Accepted failures: CloudFront misconfiguration can cache authenticated content, the single EC2
origin remains a backend failure domain, DNS-01 certificate automation has a narrowly scoped DNS
write role, and the public origin address exists even though its SG is restricted.

## Revisit when

Use a private CloudFront VPC origin/ALB when NAT/HA is already justified, not to conceal one address.
Move to Amplify/Vercel when preview/product velocity matters more than demonstrating the edge. Add
WAF only after measured abuse; the CloudFront free flat-rate plan is evaluated separately because
its allowance/features differ from PAYG.
