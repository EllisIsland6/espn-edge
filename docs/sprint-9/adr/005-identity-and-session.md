# ADR-005 — Cognito Lite, classic hosted UI and an opaque application session

**Status:** accepted. **Date:** 2026-08-09.

## Context

The artifact needs managed authentication with PKCE and TOTP, but tenant roles and RLS context are
application data. B4 confirms Cognito Lite has authenticator-app MFA and the classic hosted UI; the
newer branded Managed Login is an Essentials feature. B5 requires an identity-loss/re-link contract.

## Options

| Option | Monthly cost at 1 / 10 / 50 direct users | Trade-off |
| --- | ---: | --- |
| Cognito Lite + classic hosted UI | **$0 / $0 / $0** below 10k MAU | AWS-native OIDC/TOTP and basic hosted pages; dated UI and AWS coupling. |
| Cognito Essentials + Managed Login | **$0 / $0 / $0** below 10k MAU; then $0.015/MAU | Better login branding/passkeys/email MFA; those features are not requirements and paid growth is 2.7× Lite's first rate. |
| Auth0 Free / Essentials | **$0** free; **$35/month** Essentials at 500 MAU | Mature identity UX; free organization limits and a >$10 step with no needed capability. |
| Clerk Hobby / Pro | **$0** Hobby; **$20/month** Pro billed annually | Strong React/org UX; external authorization still does not replace RLS. |
| Roll our own | `$0` service line plus email | Password/MFA/recovery/abuse/on-call burden becomes ours; rejected security work. |

Current prices: [Cognito](https://aws.amazon.com/cognito/pricing/),
[Auth0](https://auth0.com/pricing), [Clerk](https://clerk.com/pricing).

## Decision

Use Cognito Lite with a prefix domain and classic hosted UI, authorization code + PKCE, required
authenticator-app TOTP for owner/operator roles, and email verification through SES. Do not enable
SMS, Plus threat protection, passkeys, access-token customization or Cognito groups for tenants.

The browser starts at `/auth/login`, returns to the same application origin at `/auth/callback`, and
the backend exchanges/validates tokens. It creates a cryptographically random opaque application
session; only a hash and stable `users.id` live in PostgreSQL. No Cognito access/refresh token is
placed in local storage or retained in the database. The host-only `__Host-session` cookie is
Secure/HttpOnly/SameSite=Lax and state mutations require a per-session CSRF token. Session lifetime
does not exceed the validated Cognito token lifetime; expiry sends the user through login again.
Ordinary API routes validate this application session rather than accepting a Cognito bearer from
JavaScript; Cognito validation is concentrated at the same-origin authentication gateway.

`external_identities` maps Cognito `sub` to stable application user ID. Cognito proves identity;
`tenant_memberships` and route policy authorize actions; RLS enforces data access. Terraform owns
pool/client/domain configuration. A daily non-secret identity inventory in S3 supports B5
reconciliation but cannot restore passwords or TOTP seeds. Pool loss means manual re-enrollment and
verified operator re-link, up to two business days for 50 users.

Run that exporter as an SSM State Manager association on the trusted EC2 host. The instance profile
can list/describe only the selected pool and write only the identity-inventory S3 prefix; it cannot
read the application database or secrets. This avoids giving either ECS application task a cross-user
identity permission. A missed daily run alarms and is retried; the prior 35 days remain versioned.

## Consequences and cost

Cognito costs **$0/month** at 1–50 MAU. Budget **$0.01/month** for up to 100 SES recipients at the
$0.10/1,000 à-la-carte rate; actual verification/reset volume should be lower. The opaque session
adds no key secret and revokes immediately by deleting its row.

Accepted failures: classic UI is less polished; Cognito outage blocks login; hourly/session expiry
can force another login; total pool loss exceeds the infrastructure RTO for all users; manual
re-linking is operator work. Database-only identities are inert and pool-only identities have no
tenant access, which fails closed after PITR divergence.

## Revisit when

Choose Essentials when passkeys, email MFA or visual branding has product value, not merely because
it is currently free. Reconsider Auth0/Clerk when their organization lifecycle removes enough app
code to justify >$10/month. Add multi-Region identity only when identity access has a calendar RTO
and recovery drills show manual re-enrollment is unacceptable.
