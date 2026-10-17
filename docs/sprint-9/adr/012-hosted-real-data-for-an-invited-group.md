# ADR-012 — Real ESPN data on the hosted site, for an invited group

**Status:** accepted by the operator on 2026-10-16 ("yes to all" to the six questions in the orchestration record, the first of which was this ADR as written).
**Date:** 2026-10-12. **Supersedes, in part:** the custody row of `03-architecture.md`
("public synthetic AWS; private real data local; private AWS unselected") and the public half of
ADR-006 ("no public CMK"). **Does not change:** the tenant kernel, RLS, the session model, Phase 31's
chokepoints as *mechanisms*, the plan-first apply procedure, or who runs AWS mutations.

## The Question

The operator's words, 2026-10-08 and again 2026-10-12: the hosted site should work for him and about
ten invited people, each with their own ESPN account, *the way it worked locally*. Which boundary
moves, which stays, and what does the site then hold that it does not hold today?

## The Lens

Custody first: the dangerous asset is an ESPN session (`espn_s2` + `SWID`), which is full account
access at ESPN, not a scoped token. Zero trust as the project practises it: a dangerous operation
should be unexpressible by a principal that must not perform it, and a structural guard beats a
procedural one. Least authority. And the operator's standing direction: a practice project, narrowed
and time-boxed; done beats complete.

## The Selection

**What the architecture already says.** `03-architecture.md` selects synthetic-only for the public
site and keeps real data on the operator's laptop; its 100× column admits real strangers only "after
written provider authorization, dedicated security/on-call ownership and per-environment
account/key/data cells." None of those three exists, and ESPN does not give written authorization
for this kind of automation. The honest statement is therefore: this ADR accepts a risk the
architecture declined, for a bounded group, on the operator's decision, with the mitigations below.
It is not sanctioned by the earlier design; it amends it.

**What does not change.** One AWS account, one host, one database, one public origin. The tenant
kernel and forced RLS: every invited person is their own tenant, and the 27-attack suite that proved
isolation on PostgreSQL is the isolation argument. Cognito remains invitation-only; the operator
creates every user. Mutations remain the operator's; Agent 1 reads.

**The mode.** A third `Settings.app_mode`, `private_invited`: hosted (sessions, CSRF, OIDC, default
deny -- everything `public_synthetic` requires of a request) and real (provider, credential path and
raw cache admitted). Phase 31's three chokepoints keep their shape and test the right question: the
codebase today asks `is_public_synthetic` in two senses -- "is this hosted?" (`db.py`, `security.py`,
`auth.py`) and "is this synthetic?" (`crypto.py`, `espn.py`, `cache.py`) -- and the new mode is
exactly the case where the two answers differ. Each site is reclassified to `is_hosted` or
`is_synthetic`, and a test enumerates every caller so a new one cannot land unclassified.
`public_synthetic` stays as it is; the synthetic site can be kept or retired later.

**Key custody.** Cookies are Fernet-encrypted at rest with `FERNET_KEY`. Three places the key could
live in AWS:

| Option | Cost | Who can read the ciphertext's key |
| --- | ---: | --- |
| Secrets Manager secret under the AWS-managed key | $0.40/mo | Any principal granted `GetSecretValue` on it; account administrators by policy change. No boundary change. |
| **Secrets Manager secret encrypted with a customer-managed KMS key whose policy names one reader** | **$1.40/mo** | Only the principal the key policy names, via Secrets Manager; a policy change is a logged KMS event. One bootstrap change: `kms:CreateKey` leaves the deploy boundary's Deny for this one key. |
| Envelope encryption against the CMK in the application | $1.00/mo | As above, with per-row data keys and encryption context; the design ADR-006 wrote for the unapplied private module. Changes the crypto code. |

Selected: the middle row. It is the design ADR-006 reserved for "a concrete custody requirement",
which this is; it changes no application crypto; and the key policy, not an IAM allow-list, is what
names the reader -- the web task's execution role, via `kms:ViaService` Secrets Manager only. The
deploy role may administer the key (create, tag, describe, schedule deletion with the 30-day
minimum) and may not decrypt with it. A policy test pins all of that and the boundary's `CreateKey`
Allow to exactly one key.

*Implementation note, WU-2 (2026-10-17).* IAM cannot bind `kms:CreateKey` to one key -- the key
does not exist when the call is authorized -- so "exactly one" is held by
`test_the_only_kms_key_is_the_custody_key` and by the plan workflow's gate, which admits
`aws_kms_key.custody` by name and refuses any other key. The deploy role never uses the key: an
explicit Deny by alias (`kms:ResourceAliases`) covers Encrypt, Decrypt, ReEncrypt, GenerateDataKey
and CreateGrant, and the Allow it holds for AWS-managed keys is scoped to `alias/aws/*`, so no path
reaches this key. The key policy names the account root (the lock-out guard, which is what lets IAM
govern the key) and the one reader; the deploy role's administration lives in the boundary, beside
its Deny. The secret itself is the operator's, not Terraform's: Secrets Manager validates a new
secret's key by decrypting with it on the caller's behalf, which the deploy role may not do, so the
operator creates `espn-edge/fernet-key` with its value and the stack reads its ARN behind a
variable, `wire_fernet_key`. G3 is therefore two applies -- key and alias; the operator's
`create-secret`; the flip that wires the web task.

**Sync.** `POST /api/leagues/{id}/sync` runs the provider inside the request today, which is how the
UI works locally; it needs no worker. The worker service stays parked at zero and scheduled syncing
stays deferred, as Phase 39 left it. The shared one-request-per-second limiter is per process, which
on one host is global; it is enough for eleven people and a cap per tenant is a later hardening.

**The people.** Each invitee: a Cognito user the operator creates; a tenant, a membership and an
identity row written by an operator script that generalises 0017's recipe; their own ESPN session
pasted by them into the site, with the page saying plainly what is kept and how to erase it; deletion
of the account row erases the ciphertext; the operator can disable the Cognito user and delete the
tenant. Consent is the sentence on the form and the invitation that precedes it; there is no
click-through theatre.

**AI.** Per the operator: each user's own Anthropic key, held client-side and forwarded per request,
never persisted or logged. Last in the order, because nothing above depends on it.

## The Synthesis

Phase 46 opens with this ADR and proceeds in gates, each its own approval, in this order:

| Gate | What is approved | What is proven before the next gate |
| --- | --- | --- |
| **G1** | This ADR, by the operator. | The custody-row amendment (C8) is in the architecture document. |
| **G2** | The mode and its tests, in an image that is deployed **still in `public_synthetic`**. | The reclassification: every `is_public_synthetic` caller named and tested; `private_invited` proven offline to be hosted-and-real; `public_synthetic` behaviour unchanged. |
| **G3** | The key: bootstrap boundary change, the CMK, the secret, the task's read path. Applied plan-first. | The secret exists and holds a key the operator generated and never pasted into chat; the policy simulator shows the web execution role may read it through Secrets Manager and the deploy role may not decrypt. |
| **G4** | The mode switch: `APP_MODE=private_invited` on the web task, with the consent copy on the page. | The site refuses no hosted behaviour it had; the account form appears; no account exists yet, so no live read has happened. |
| **G5** | **The first live ESPN read**, with the operator's own session only. | One league of the operator's syncs from AWS; the raw cache row is his tenant's; CloudWatch shows no credential; the limiter's cadence is visible. |
| **G6** | Invitations, one person at a time. | Each person's rows are their own tenant; the 27-attack argument applies unchanged; revocation measured on the first invitee. |
| G7 | Per-user Anthropic key. | AI answers for a user with a key and is absent for one without; nothing of the key in logs. |

Estimate: five to seven sessions. Cost: about $1.40/month on top of today's ≈$35.

## Consequences, accepted knowingly

- The site holds ESPN sessions at rest, in RDS, as Fernet ciphertext whose key is a CMK-encrypted
  secret. A host-root compromise reaches them; that was true of ADR-006's trust statement already.
- ESPN's terms do not permit this automation; the exposure is the invitees' ESPN accounts, and it is
  the operator who invites them. The invitation says so.
- The public synthetic promise ("no stranger credential, no raw payload") no longer describes this
  deployment once G4 is applied. The architecture document's outcome paragraph is amended, not
  quietly contradicted.
- One more boundary Allow, one more key, one more secret, one more mode; each with a test that
  names it and a control that removes it.

## Revisit when

A second host, a second administrator, or a twelfth user: the per-tenant limiter, the worker loop,
and ADR-006's full envelope design come back in that order. Written provider authorization, if it
ever existed, would retire the "accepted knowingly" paragraph rather than this ADR.
