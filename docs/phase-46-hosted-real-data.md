# Phase 46 — real ESPN data on the hosted site, for an invited group

Decision: [ADR-012](sprint-9/adr/012-hosted-real-data-for-an-invited-group.md); custody amendment C8
in `sprint-9/03-architecture.md`. **Status: gate G1 recorded 2026-10-16 ("yes to all"); WU-1 deployed at gate G2 on 2026-10-22 (apply
#11, `APP_MODE` unchanged); WU-2 applied at gate G3 on 2026-10-22/23 (bootstrap; apply #12 the key and alias;
the operator's secret under the key; apply #13 the flip -- web:7 reads `FERNET_KEY`, still `public_synthetic`).
WU-3 built 2026-10-23 (code only; runs for nobody but the operator until G6). WU-4 built and deployed 2026-10-23
(`s3 sync` + invalidation, CI #58 green; the live page says "hosted on synthetic data" until G4 flips
the mode). G7's design decided 2026-10-23 in the operator's words (keys stored encrypted per tenant;
WU-7 below), and WU-7 built the same evening (code only; ships after G5 as an image, migration 0018
and a plan-first apply). The operator's "let's start the build to 100% complete" (2026-10-23) opened the build of
everything that remains. **G4 applied 2026-10-24: plan #12 read `1/1/1` against the prediction, apply #14
approved by the operator for `production`, `Apply complete! Resources: 1 added, 1 changed, 1 destroyed`;
`/api/health` reports `private_invited` from the edge. The hosted site is the invited site, for him alone
until G6; nothing has touched ESPN.** Confirmed on the page the same day: his sign-in, the Portfolio board,
the Manage page in invited mode (consent sentences, the account form, "No accounts yet", the one public
league, the invited footer). Preparing G5's erase check found that the consent sentence could not be kept
once a league was linked (WU-8 below; built 2026-10-24, ships in the WU-7 image). **G5 done in deed
2026-10-26 and proven 2026-10-27: the operator's own session on the invited site, 25 of his leagues synced from
AWS (301 ESPN requests in 347 s, every raw-cache row his tenant's, the limiter's cadence measured, no credential
in the log).** Next: the image carrying WU-7, WU-8 and the two fixes; migration 0018; plan-first apply; the
erase check; then G6 and G7. Each later gate is its own approval, recorded in
`docs/agent-handoffs/ORCHESTRATION_STATE.md` before the work it admits begins.**

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

- G4 (commit built 2026-10-23; applied 2026-10-24 -- plan run 40062481684 read `1 to add, 1 to change,
  1 to destroy` with the one semantic change `public_synthetic -> private_invited` beside eight unchanged
  entries; apply run 40063081062 approved by the operator; health `private_invited` read from the edge): `local.web_environment` in `containers.tf` is
  the shared environment with `APP_MODE` rewritten to `private_invited` -- a literal, not a variable,
  because it is the custody boundary and a tfvars line is not where a reader checks one; the web
  task's `app` container reads it; migrate (needs nothing real) and the parked worker keep the
  synthetic literal; the `custody` output says so; the pin test became
  `test_the_web_task_is_the_invited_site_and_the_other_tasks_stay_synthetic` (35 policy tests,
  controls: the web container pointed back at the shared list; `public_synthetic` left in the web
  list; the migrate task pointed at the web list; a host `ANTHROPIC_API_KEY` added to the shared
  secrets -- each fails it). Plan-first; predicted `1 to add, 1 to change, 1 to destroy` (the web
  task definition replaced, the web service moved; no IAM change). Verified after the apply:
  `/api/health` reports the mode; the consent copy and the form appear; `/api/accounts` is `[]`; no
  account exists, so no provider has been constructed. README §6b. Confirmed on the page by the
  operator the same day (screenshots: the Portfolio board signed in; Manage with the three consent
  sentences, the "Add ESPN account" form, "No accounts yet", the "Add league" form, league 569446945
  `public · 2026 · synced never`, footer "ESPN Edge · hosted for an invited group"; Caddy's 24-hour
  histogram: 4 `/api/auth/callback` lines, one `302` in the last half hour). His first sign-in after
  the apply had failed with "sign-in could not be completed": the 10-minute `state.nonce` flow cookie
  had expired while the Cognito tab sat open (400), then the same code was replayed (401) -- nothing
  mode-dependent, and the callback logged nothing to say so. `7df80b1` makes it log a reason code
  (`flow_cookie_missing_or_expired`, `state_mismatch`, the OIDC error class), never a code, state or
  nonce; pinned by `test_a_failed_sign_in_logs_its_reason_code_and_no_value` -- which passed alone
  and failed in the full suite until `alembic/env.py` stopped muting existing loggers
  (`fileConfig(..., disable_existing_loggers=False)`; the migrate task is unaffected). Lesson 52.
- G5, after its own approval: the operator adds his own ESPN session on the live site, adds one
  league, presses Sync. Verified from the browser and the console: the league syncs; the raw cache
  row carries his tenant; the web log shows no `SWID`, no `espn_s2`, no cookie value (the filter
  that found none on 2026-10-12 is rerun); the limiter's one-second cadence is visible in the
  request timings. Then one restore-shaped check: `DELETE` the account on the page and prove the
  ciphertext row is gone. Instructions handed over 2026-10-24 (README §6c): the league already on
  the page, 569446945, was added public before any account existed, so it is added again with the
  account selected -- `add_league` re-points an existing row to the account and clears `is_public`
  rather than duplicating it -- and the manual add is the gate's path (one host,
  `lm-api-reads.fantasy.espn.com`), discovery (`fan.api.espn.com`, a second host) afterwards if
  wanted. The erase check waits for the WU-8 image: on web:8 the delete answers 409.
  **Done 2026-10-26, proven 2026-10-27.** The operator added his session and used Discover → Import &
  sync: 25 leagues (the public one re-pointed, 24 added), one at a time. Console: no cookie value in
  any log line, no auth failure, no traceback, 25 × `200` on the sync route. The tunnel read
  (`ops/cache_rows.py`, as the owner): 301 rows, every one `tenant=1`; 25 × 12 requests plus one
  `pro_schedule`; one account hash on every league line, never `public`; a row for every request of
  every league, so no fetch failed; 347 s for 301 requests, request starts never under a second apart
  per account -- the gaps read ~1000 ms between like-sized requests, ~1150 before the heavier week
  view, ~1330 before the player pool and ~850 for the light boxscore after the heavy view, because
  `fetched_at` is stamped on store while the limiter spaces starts. The one unknown is answered: ESPN
  (both hosts) answers AWS addresses. Two runbook preconditions surfaced on the way -- the Mac venv
  predated the declared driver; the SSM tunnel had timed out -- and are guard lines in README §6c/§7
  now (lesson 54). Figures in `docs/evidence/phase-46-release-e5e7a67.txt`.

### WU-8. The erase the form promises (found preparing G5; built 2026-10-24)

The form says *"Deleting the account here erases it."* On web:8 that is true only while no league
points at the account: `DELETE /api/accounts/{id}` answered 409 "re-point or remove them before
deleting this account", and no route ever existed to re-point a league away from an account or to
remove one (`add_league` re-points only *towards* an account; there is no league `DELETE`). So a
person who used the site as intended -- account, league, sync -- could not erase the credential,
and acceptance criterion 8 held only for the empty case. Decided and built 2026-10-24: the delete
detaches the account's leagues first (`account_id` NULL, `is_public` True -- the state a league
added with no account has) in the same transaction, then deletes the row; the leagues and
everything synced under them stay. A private league detached this way syncs on the public path and
reports auth failed until it is added again with an account, which re-points it. The alternative,
a league `DELETE` that would also take the synced data, was named to the operator and not chosen
unless he asks for it. Proof: `test_delete_account_detaches_its_leagues_and_keeps_their_data`
(SQLite: 204, the league listed with no account and public, its team row intact, re-pointing by a
second add) and `test_delete_account_detaches_on_postgres_where_the_foreign_key_is_enforced`
(the application role, tenant bound, a provisioned tenant, the foreign key enforced; revoked
afterwards with `leagues: 1, accounts: 0`). No schema change. Ships in the WU-7 image; the G5 erase
check runs on it.

### WU-6. Invitations (gate G6) and per-user AI (gate G7)

- G6, one person at a time: Cognito user by the operator (console, invitation email); WU-3 script;
  the person signs in and adds their own session. The first invitee is also the revocation drill.
- G7: the per-request header design was replaced on 2026-10-23 by the operator's decision --
  *they pay for their own AI API, and we store what's needed and practical, per best practice* --
  recorded in ADR-012's decision note and specified as WU-7 below. The host-key ledger path stays
  for `public_synthetic`; the gate is still the first AI call on the hosted site.

### WU-7. Per-user AI key custody (gate G7; image + migration + plan-first apply)

Built 2026-10-23, the same evening the design was decided; measured below. Not deployed: it
ships as an image, migration 0018 and a plan-first apply after G5, and the gate is the first AI
call on the hosted site (README §8).

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

**The Synthesis -- as built and measured.** `alembic/versions/0018_ai_credentials.py` (the table,
FORCE row security, 0005's policy shape; 0016's default privileges give `edge_app` its DML);
`api/models.py::AiCredential`; `api/services/ai_keys.py` (verify against the provider's model
listing, encrypt through `api/crypto.py`, upsert, read, delete, decrypt for the call, note the use);
`api/routers/ai.py` (`GET`/`PUT`/`DELETE /api/ai/key`; `/api/ai/status` now takes the tenant's
session, since "enabled" is a fact about the tenant on the invited site); `api/services/ai.py`
(`AiService` resolves its key by mode -- the tenant's on the invited site, never the host's, no
ledger; `AnthropicLlmClient` is told at construction whose spend it is and fails closed when
built any other way); `api/main.py` (the `PUT` gets the credential-neutral 422); the recovery
format moved to **v5** (`ai_credentials` allowlisted and NOT_BUNDLED -- a credential never travels;
the three catalog digests re-pinned from real databases, v4 retired); `ops/provision_user.py`
takes the row on revoke and counts it; the SPA's `AI_KEY_CONSENT` sentences in `copy.ts`, the
write-only panel on Manage in `private_invited` only, `getAiKey`/`putAiKey`/`deleteAiKey` through
the CSRF-carrying `send()`.

**Measured.** `tests/test_ai_key_custody.py`: 16 offline (stored encrypted and never echoed; one
row, replaced on a second PUT; a refused key and an unverifiable one store nothing, 400 and 503; the
synthetic instance answers the chokepoint's 403 and asks the provider nothing; the malformed body
gets the value-free 422; no CSRF header, 403; DELETE erases, then 404; `/api/ai/status` true for a
tenant with a key only, even with a host key configured; the service builds its client from the
tenant's key with the ledger's `reserve` patched to fail if called; a failed call records the class
`AiError` and not a message carrying the key; the other two modes unchanged; nothing of the key in
the log capture; the real verifier distinguishes 401 from an outage against a fake transport) and
1 on PostgreSQL 16 as the application role (two provisioned tenants, each sees only its own row,
nothing bound sees nothing, A cannot write into B's tenant, A's DELETE leaves a row a superuser
cannot find, revoking B counts `ai_credentials=1`). Full Python suite 1,354 passed in the cloud
workspace plus the recovery suites 321; Playwright 62 (61 before; the invited panel's three
sentences, its absence on the synthetic and local instances, and the save/replace/delete flow
asserting the key is sent once and never rendered); `tsc` clean; `ruff` clean.

**Controls, each failing exactly the test that names it:** the chokepoint bypassed with a fresh
Fernet (the synthetic refusal); verification skipped (four tests); the key added to the response
(never echoed); the host key used on the invited site (status and service); the ledger consulted
for a tenant's own spend (three tests, the patched `reserve` firing); the failure message recorded
(class only); the 422 redaction forgetting the PUT (two tests); the revoke order forgetting the
table (the order test); and on PostgreSQL, row security switched off on the table -- tenant A reads
both keys and the isolation test fails. Acceptance criterion 10.

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
| `DELETE /api/accounts/{id}` detaches the account's leagues instead of refusing with 409 | WU-8; `api/routers/accounts.py` docstring; README §6c |

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
   in the web log. **Met 2026-10-26/27: 25 leagues, 301 rows in tenant 1, cookie grep 0 (G5).**
7. Two provisioned people on PostgreSQL see only their own rows; revoking one leaves the other
   intact.
8. Deleting an account on the page erases the ciphertext row -- also when leagues point at it: they
   are detached and keep their synced data (WU-8).
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
