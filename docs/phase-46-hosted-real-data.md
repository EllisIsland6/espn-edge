# Phase 46 — real ESPN data on the hosted site, for an invited group

Decision: [ADR-012](sprint-9/adr/012-hosted-real-data-for-an-invited-group.md); custody amendment C8
in `sprint-9/03-architecture.md`. **Status: gate G1 recorded 2026-10-16 ("yes to all"); WU-1 deployed at gate G2 on 2026-10-22 (apply
#11, `APP_MODE` unchanged); WU-2 applied at gate G3 on 2026-10-22/23 (bootstrap; apply #12 the key and alias;
the operator's secret under the key; apply #13 the flip -- web:7 reads `FERNET_KEY`, still `public_synthetic`).
WU-3 built 2026-10-23 (code only; runs for nobody but the operator until G6). WU-4 built and deployed 2026-10-23
(`s3 sync` + invalidation, CI #58 green; the live page says "hosted on synthetic data" until G4 flips
the mode). G7's design decided 2026-10-23 in the operator's words (keys stored encrypted per tenant;
WU-7 below). Next: G4 in the operator's words. Each later gate is its own approval,
recorded in `docs/agent-handoffs/ORCHESTRATION_STATE.md` before the work it admits begins.**

Operator's scope, 2026-10-12: the site should work for him and about ten invited people, each with
their own ESPN account, the way it worked locally. Delivered in order: first for him alone (G2–G5),
then the group (G6), then AI (G7). Every gate is its own approval; the first live ESPN read from AWS
is G5 and nothing before it touches ESPN.

## The Question

How does one hosted deployment become hosted-and-real without weakening the three guarantees the
synthetic site proved -- a request without a session gets nothing, a tenant sees only its own rows,
a dangerous operation is unexpressible rather than discouraged -- and how is every step reversible
until the one that is not?

## The Lens

Structural over procedural (Phase 31); the database enforces isolation, not the application (Phase
36–37b); nothing green that establishes something other than what it claims (lessons 39–48); every
AWS mutation by the operator, plan read before apply (lesson 47); no secret in chat, logs, evidence,
fixtures, commits or handoffs, ever (lesson 46 and the incident behind it).

## The Selection — work units

### WU-1. The third mode (gate G2; code only; deployed still synthetic)

- `Settings.app_mode: Literal["private_operator", "public_synthetic", "private_invited"]`, still
  required and frozen.
- Two properties replace one question: `is_hosted` (`public_synthetic` or `private_invited`) and
  `is_synthetic` (`public_synthetic` only). `is_public_synthetic` is removed, so no caller can keep
  asking the ambiguous question.
- Reclassification, each a one-line change with its reason in the diff: `db.py` default-deny →
  `is_hosted`; `security.py` cookies/CSRF → `is_hosted`; `routers/auth.py` front door → `is_hosted`;
  `crypto.py` custody → `is_synthetic`; `services/espn.py` provider and `services/cache.py` raw
  cache → `is_synthetic`; `services/ai.py` ledger → `is_synthetic` for the host-key ledger, with the
  per-user key arriving in WU-6.
- **Test that holds it:** `tests/test_mode_classification.py` walks the AST of `api/` for every
  attribute read of `is_hosted` / `is_synthetic` and asserts the set of (file, property) pairs equals
  a register in the test with a reason per entry; a new caller fails it until classified. A second
  test boots the app in each of the three modes and asserts the matrix: synthetic refuses provider
  construction, custody and cache write; invited admits all three and still 401s without a session;
  operator admits all three and has no 401. Controls: a site flipped to the other property fails the
  matrix; the register with one entry removed fails the walk.
- The image is built, pushed, and deployed with `APP_MODE` unchanged (`public_synthetic`), so G2's
  apply changes behaviour nowhere. Plan-first; expect task-definition replacements only.

### WU-2. Key custody (gate G3; bootstrap + stack; plan-first, two applies)

Built 2026-10-17, corrected the same day before any apply (see the orchestration state); nothing
applied until G3 is given in words.

- Bootstrap: the deploy boundary's unconditional Deny loses `kms:CreateKey`. Two statements arrive.
  `CustodyKeyAdministration`: create, describe, key-policy get/put, rotation status and enable,
  tags, description, enable/disable, schedule and cancel deletion, alias create/update/delete/list
  -- the set the AWS provider reads and writes for a key and an alias, nothing cryptographic.
  `CustodyKeyNeverUsedByDeploy`: an explicit Deny on `Encrypt`, `Decrypt`, `ReEncrypt*`,
  `GenerateDataKey*` and `CreateGrant` for `alias/espn-edge/custody`, which no later Allow can
  undo. The AWS-managed-key ViaService Allow gains `kms:ResourceAliases = alias/aws/*` -- a
  condition of the key, not the request, valid for every operation on a key, and every AWS-managed
  key carries its `alias/aws/<service>` alias in the account -- so the Decrypt it grants for RDS,
  EBS, ACM and the other secrets can never be the custody key's. No Allow anywhere in the boundary
  reaches a cryptographic action on the custody alias; the deploy role administers the key and
  never uses it.
- Stack (`custody.tf`): `aws_kms_key.custody` (rotation on, 30-day deletion window) whose policy has
  two statements: the account root -- the lock-out guard every key policy carries, and what lets
  IAM govern the key; the deploy role's administration is IAM's business, where its Deny lives --
  and `WebTaskReadsTheFernetSecret`: the web task's **execution** role, `kms:Decrypt` and
  `kms:DescribeKey`, via Secrets Manager only. `aws_kms_alias.custody`. And the secret is **read,
  not created**: `data "aws_secretsmanager_secret" "fernet_key"` with `count` on `wire_fernet_key`.
- Why the secret is the operator's (the correction): Secrets Manager validates a new secret's key by
  calling `GenerateDataKey` and `Decrypt` with it on the caller's behalf -- its documentation names
  the calls, `SecretVersionId` `RequestToValidateKeyAccess` -- so a `CreateSecret` by the deploy
  role would have been refused by its own Deny at the first apply. The policy tests read policy
  text; they cannot see what a service does on the caller's behalf. Found by reading the service's
  "how it uses KMS" page before the apply (CLOSE-OUT lesson 49). The operator creates the secret
  with its value in one command (README §6), which also retires the empty-version problem: the
  secret is born with its value.
- The wiring gate: `variable "wire_fernet_key"` (default `false`) drives the data source's `count`;
  the web task's `secrets` are `local.web_secrets` -- the shared list plus one `FERNET_KEY` entry
  per element of the splat `data...fernet_key[*].arn` (none until wired); the execution role's
  secrets policy concatenates the same splat; the worker and migrate tasks keep the shared list.
  G3 is two applies: key + alias; the operator creates the secret; the flip -- one line in
  `variables.tf` and the assertion that records which side of the secret's creation the stack is
  on -- and the second apply. `infra/terraform/README.md` §6 is the procedure, paste-safe.
- The plan workflow's gate, which refuses any `aws_kms_key` in a plan, admits exactly
  `aws_kms_key.custody` by name. IAM cannot bind `CreateKey` to one key (none exists when the call
  is authorized); "exactly one key" is held there and by `test_the_only_kms_key_is_the_custody_key`.
- The value: generated by the operator in his terminal, passed to `create-secret` from a variable,
  never printed, never in chat. Terraform holds neither the value nor the secret; the plan role's
  `managed-secret-values` names three other secrets.
- **Tests** (`tests/test_terraform_policy.py`, 35): the only key is the custody key; the key policy
  names one reader under the ViaService condition and the alias string agrees across the two roots;
  the admin statement carries no cryptographic action, every Allow in the boundary that carries one
  is scoped to `alias/aws/*`, the Deny names the alias and all five cryptographic actions, and
  `CreateKey` is out of the unconditional Deny; no `aws_secretsmanager_secret` named `fernet_key`
  exists, the data source is gated by `count` on the variable, the gate defaults to `false`,
  `FERNET_KEY` reaches the web task through `web_secrets` and no other task, the execution role's
  policy carries the same splat. **Controls run, each failing exactly the test that names it:**
  `Decrypt` added to the admin statement; `GenerateDataKey*` dropped from the Deny; the alias scope
  dropped from the AWS-managed Allow; the Deny's alias string drifted; the gate's default flipped
  early; a second reader named on the key; the worker task given `web_secrets`; the secret planted
  as a resource. Probes in the README: deploy role `kms:Decrypt` and `kms:GenerateDataKey` via
  Secrets Manager on the custody alias → `explicitDeny`, `Decrypt` on `alias/aws/secretsmanager` →
  `allowed`; execution role `GetSecretValue` on the secret → `allowed` once wired.

### WU-3. Provisioning as a script, not a paste (gate G4)

Built 2026-10-23; runs for nobody but the operator until gate G6.

- `ops/provision_user.py provision <email> <sub>`: runs as the owner through the tunnel
  (`DATABASE_URL` without a password, `PGPASSWORD` from the RDS-managed secret, `OIDC_ISSUER`);
  creates the tenant (slug from the email's local part, `uq_tenants_slug` deciding uniqueness
  with a suffix retried inside a savepoint -- the policied table cannot be pre-read), the user,
  the membership and the identity in one transaction -- 0017's shape one level up: `tenants` is
  policied on `id = current_tenant()`, so the tenant id is reserved and bound before its own row
  is inserted. Prints `tenant_id= user_id= membership_id=` and nothing else: no email, no subject,
  in any output. Idempotent on the identity. `revoke <sub>` deletes one person who is their own
  tenant in the order the keys permit (leagues before accounts; jobs, schedules, outbox by tenant;
  sessions and identity by user; membership, user, tenant), prints a count per table, and refuses
  the operator's tenant (`TENANT_ID`), a user with more than one membership, and a tenant with
  more than one member -- it will not guess whose rows those are.
- **Measured** on PostgreSQL 16 as a NOSUPERUSER NOBYPASSRLS owner (the cluster in the cloud
  workspace, migrated to 0017 by that role): two people provisioned; through `edge_app`, the
  login read path finds the identity with nothing bound and binds the user to its one membership;
  bound to one person, the other's tenant, league and team are invisible; `edge_app` cannot write
  `identities` (42501, as 0017 says); the revoke takes the person's league, account, team (by
  cascade, which referential integrity runs under, bypassing row security), identity, membership,
  user and tenant, and a superuser read proves the rows gone rather than hidden; the other person
  is untouched; the slug collision lands on `-2`; both refusals refuse before any delete. The
  owner-without-binding control reproduces 0017's finding verbatim: `new row violates row-level
  security policy for table "tenants"`.
- **Tests** (`tests/test_provision_user.py`, 21): the offline half runs everywhere (slug, arguments,
  the delete order, a refusal naming no person); the PostgreSQL half runs where
  `PROVISION_PG_OWNER_URL`/`PROVISION_PG_APP_URL` point at a migrated cluster and skips with that
  reason otherwise, and its fixture refuses a superuser or BYPASSRLS owner as the premise.
  **Controls, each failing exactly the test that names it:** the tenant inserted before its id is
  bound; accounts deleted before leagues; the shared-tenant refusal removed; the operator refusal
  removed; the identity lookup skipped; the subject printed in a refusal; a superuser offered as
  the owner.

### WU-4. The page tells the truth (gate G4; SPA; `s3 sync`)

Built and deployed 2026-10-23. Static assets only -- no Terraform, no task definition; it reached
the site by `npm run build`, `aws s3 sync --delete`, and a CloudFront invalidation after CI #58 on
`d765dc5` was green. Read from the edge afterwards: `/` and `/index.html` reference the new bundle
and not the old one, the old bundle answers 403 (deleted), `x-cache: Miss from cloudfront` on the
fresh `index.html`, and the served bundle carries the three footer labels, the synthetic empty state
and the consent sentences. Until G4 flips the mode the live page says "hosted on synthetic data".

- `web/src/copy.ts` holds every word the page says about custody, by mode, as plain strings, so
  the backend's tests can hold them by literal: `INVITED_CONSENT` (three sentences: *"Your ESPN
  session (SWID and espn_s2) will be stored encrypted on this server and used only to read your
  leagues. Deleting the account here erases it. ESPN's terms do not permit this kind of access;
  you are choosing to do it."*), `SYNTHETIC_NOTICE`, `LOCAL_NOTICE`, `FOOTER_LABEL` per mode, and
  `holdsCredentials(mode)`, which is true for `private_operator` and `private_invited` only.
- The Manage page keeps the mode `/api/health` reports (`Mode | null`), not a boolean. Three
  branches say three different things; the account form renders only when `holdsCredentials(mode)`;
  an unknown mode -- health not yet answered, or answered without one -- shows no notice and no
  form, which is the honest default. The accounts empty state names the synthetic instance when
  that is what it is. The footer fetches health once and says which deployment this is
  (`ESPN Edge · local portfolio analytics` / `hosted on synthetic data` / `hosted for an invited
  group`), `ESPN Edge` until it knows.
- **Measured:** `tsc -b --noEmit` clean on the operator's machine; Playwright 61 passed in the cloud
  workspace (57 before; the four new tests mock `/api/health` for every mode and assert the terms
  sentence appears in the invited mode and nowhere else, the form is absent on a synthetic
  instance, nothing is claimed until the mode is known, and the operator's local copy is
  unchanged); the pin tests 10 passed (`tests/test_spa_mode_copy.py`, 5, and `tests/test_spa_csrf.py`,
  5, whose synthetic-form test now reads the `holdsCredentials` gate).
- **Controls, each failing exactly the test that names it:** the form gate admitting
  `public_synthetic` (the pin test, and in the browser the synthetic test sees the `Add ESPN
  account` heading it must not); a consent sentence softened; the terms sentence rendered in the
  synthetic branch too; the footer label hard-coded instead of read from the mode; a mode dropped
  from `MODES` while the backend still names it.

### WU-5. The mode switch and the first live read (gates G4 and G5)

- G4: `APP_MODE=private_invited` on the web task only (a variable, so a one-line stack change);
  plan-first; the migrate task stays `public_synthetic` (it needs nothing real); the worker stays
  parked. Verified: `/api/health` reports the mode; the form appears; `/api/accounts` is `[]`; no
  account exists, so no provider has been constructed.
- G5, after its own approval: the operator adds his own ESPN session on the live site, adds one
  league, presses Sync. Verified from the browser and the console: the league syncs; the raw cache
  row carries his tenant; the web log shows no `SWID`, no `espn_s2`, no cookie value (the filter
  that found none on 2026-10-12 is rerun); the limiter's one-second cadence is visible in the
  request timings. Then one restore-shaped check: `DELETE` the account on the page and prove the
  ciphertext row is gone.

### WU-6. Invitations (gate G6) and per-user AI (gate G7)

- G6, one person at a time: Cognito user by the operator (console, invitation email); WU-3 script;
  the person signs in and adds their own session. The first invitee is also the revocation drill.
- G7: the per-request header design was replaced on 2026-10-23 by the operator's decision --
  *they pay for their own AI API, and we store what's needed and practical, per best practice* --
  recorded in ADR-012's decision note and specified as WU-7 below. The host-key ledger path stays
  for `public_synthetic`; the gate is still the first AI call on the hosted site.

### WU-7. Per-user AI key custody (gate G7; image + migration + plan-first apply)

**The Question.** Where does an invited person's Anthropic key live, who can read it, and what
else is kept with it?

**The Lens.** The ESPN session already answers the same question (ADR-012, C8): entered by its
owner, encrypted at rest under the custody key through one chokepoint, readable by one tenant,
erasable by its owner. A second kind of credential gets the same treatment, not a new one; and
because the spend is the person's own, the host's spend ledger -- built to bound the host's money
-- has nothing to bound on this path.

**The Selection.**

- *Storage.* Migration `0018_ai_credentials`: table `ai_credentials` -- `tenant_id` (primary key,
  FK `tenants`, no cascade, like `accounts`), `provider` (`anthropic`), `api_key_encrypted`
  (Fernet ciphertext through `api/crypto.py`), `key_hint` (the last four characters), `created_at`,
  `updated_at`, `last_used_at` (nullable), `last_error` (nullable; the exception class, never a
  message). `FORCE ROW LEVEL SECURITY`; policy `tenant_id = current_tenant()` for USING and WITH
  CHECK; `edge_app` granted SELECT, INSERT, UPDATE, DELETE (0016's pattern). `ops/provision_user.py`'s
  `REVOKE_ORDER` gains `("ai_credentials", "tenant_id")` after `accounts`, so revoking a person
  takes their key with them.
- *Chokepoint.* The ciphertext is written and read through `api/crypto.py::_fernet`, so a synthetic
  instance refuses custody of this credential exactly as it refuses the ESPN session, and the key
  behind both is the one the custody key protects.
- *API.* `GET /api/ai/key` → `{provider, hint, created_at, last_used_at, last_error}` or `null`;
  `PUT /api/ai/key {api_key}` → the key is checked against Anthropic's `GET /v1/models` (a listing
  call that costs no tokens; a refusal stores nothing and answers 400 "the key was refused by
  Anthropic"), then encrypted and upserted; answers the GET shape; `DELETE /api/ai/key` → the row is
  gone (204). Every mutation carries the CSRF header like the rest. The request body is never
  logged (the `_redact_account_validation_errors` pattern for the `api_key` field); no response
  ever carries the key.
- *Service.* `AiService` resolves its key by mode: `private_invited` → the tenant's stored key,
  decrypted at the moment of the call and held no longer than the call; no host key, no ledger
  reservation (the spend is theirs); a tenant with no key gets `enabled: false` and the same
  "absent" answers the no-key deployment gives today. `private_operator` and `public_synthetic`
  are unchanged byte for byte: the operator's key from his environment, the host key behind the
  ledger. `last_used_at` is written on success; `last_error` on failure, class name only.
- *Page.* An "AI key" panel on Manage, rendered in `private_invited` only, with its sentences in
  `web/src/copy.ts` (`AI_KEY_CONSENT`): *"Your Anthropic API key will be stored encrypted on this
  server and used only for the reports you ask for. Anthropic bills you directly for those calls.
  Deleting the key here erases it."* The field is write-only (a password input); after saving, the
  page shows the provider, `…` plus the hint, and when it was last used; Replace and Delete.
- *Deployment.* A backend change, so an image: preflight, push, the migrate task for 0018, then
  plan-first apply (both task definitions replaced for the new tag; the web service moved). Gate
  G7 is the first AI call afterwards: the operator saves his own key, asks for one report, and the
  log grep is clean. Built after G5 (so the first AI call is not tangled with the first ESPN call);
  may be built while G6 invitations proceed.

**The Synthesis -- tests, each with a control that removes it.** The consent copy pinned by literal
and rendered only in the invited branch; the chokepoint reused (a control that encrypts with a
second Fernet instance fails it); on PostgreSQL, two tenants, one key each, each invisible to the
other through `edge_app`, and the owner read after DELETE proving the row gone, not hidden; the
synthetic instance refusing `PUT` with the hosted-mode 403 and answering `GET` with `null`; a
refused validation storing nothing; the log capture holding no fragment of the key after a `PUT`
and a report; the host key untouched on the invited site when a tenant has no key (control: host
key configured, request made, no outbound call, `enabled: false`); the ledger not consulted on
the per-user path; `revoke` deleting the credential row and counting it. Acceptance criterion 10.

## Explicit deltas and guarantees

| Keeps holding | Held by |
| --- | --- |
| No session, no data (hosted) | `test_hosted_default_deny.py`, now parametrised over both hosted modes |
| A tenant sees only its own rows | the 27-attack suite, unchanged; WU-3's two-person test on PostgreSQL |
| Synthetic mode constructs no provider, keeps no credential, writes no cache | `test_hosted_mode.py`, unchanged in substance; the mode matrix |
| No secret in logs | the console filter rerun at G5; `_redact_account_validation_errors` unchanged |
| The deploy role cannot decrypt the custody key | boundary test + README probe |
| Every apply read as a plan first | the procedure; the orchestration row for each gate names the plan run |

| Changes | Stated where |
| --- | --- |
| The site holds ESPN sessions at rest for invited people | ADR-012, C8, this file, the page copy |
| One customer-managed key and one more secret (+$1.40/month) | ADR-012; budget untouched at $52 |
| `is_public_synthetic` is gone | WU-1 |

## Acceptance criteria

1. In `private_invited`, a request without a session is refused on every route the synthetic site
   refused it on; measured, not assumed.
2. In `public_synthetic`, nothing changes: the mode matrix and the existing hosted tests pass byte
   for byte in intent.
3. Every `is_hosted` / `is_synthetic` reader in `api/` is in the register with a reason.
4. The custody key's policy names exactly two principals; the deploy role's simulated `kms:Decrypt`
   is `implicitDeny`; the boundary's Allow has no `Decrypt`.
5. `FERNET_KEY` reaches exactly the web task, from the CMK-encrypted secret, and appears in no log.
6. The operator's own league syncs from AWS with the raw-cache row in his tenant and no credential
   in the web log.
7. Two provisioned people on PostgreSQL see only their own rows; revoking one leaves the other
   intact.
8. Deleting an account on the page erases the ciphertext row.
9. `ruff`, `tsc -b --noEmit`, the policy tests and the full suite pass; the plan workflow precedes
   every apply.
10. On the invited site, a tenant's AI key is readable by that tenant alone, is never returned
    after it is saved, appears in no log, and leaves with the tenant; a tenant without a key gets
    the no-key answers; the host key is never used there.

## Non-goals

Scheduled or background syncing (the worker loop stays parked); a per-tenant rate limiter; the
CloudWatch metrics sink; a second host or AZ; any ESPN write; any user who did not receive an
invitation from the operator; a twelfth user without revisiting ADR-012.

## Grants and gates

Every gate is an approval the operator gives in words, recorded in the orchestration state before
the work it admits begins. G1 admits WU-1 (code only). G2's apply admits nothing new at runtime. G3
is the first custody change. G4 is the first behaviour change on the site. G5 is the first live ESPN
call from AWS. G6 is the first stranger's credential. G7 is the first AI call on the hosted site.
