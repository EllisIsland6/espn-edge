# Phase 46 — real ESPN data on the hosted site, for an invited group

Decision: [ADR-012](sprint-9/adr/012-hosted-real-data-for-an-invited-group.md); custody amendment C8
in `sprint-9/03-architecture.md`. **Status: gate G1 recorded 2026-10-16 ("yes to all"); WU-1 deployed at gate G2 on 2026-10-22 (apply
#11, `APP_MODE` unchanged); WU-2 applied at gate G3 on 2026-10-22/23 (bootstrap; apply #12 the key and alias;
the operator's secret under the key; apply #13 the flip -- web:7 reads `FERNET_KEY`, still `public_synthetic`).
Next: WU-3, WU-4, then G4 in the operator's words. Each later gate is its own approval,
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

- `ops/provision_user.py`: runs as the owner through the tunnel; arguments are an email and a
  Cognito `sub`; creates the tenant (slug derived from the email's local part, unique), the user,
  the membership, the identity -- 0017's transaction generalised to a new tenant -- and prints the
  three ids and nothing else. Idempotent on the identity. A `--revoke` form deletes the identity,
  the membership and the tenant's rows in the order RLS permits, and prints the counts.
- **Test:** against PostgreSQL in the container (the only place RLS is real): provision two people,
  prove each sees only their own rows through the app role, revoke one, prove the other is
  untouched and the revoked one's rows are gone. The SQLite suite gets the argument-parsing and
  idempotency tests only, and says so.

### WU-4. The page tells the truth (gate G4; SPA; `s3 sync`)

- `/api/health` already reports `mode`. In `private_invited` the Manage page shows the account form
  with this copy and no other: *"Your ESPN session (`SWID` and `espn_s2`) will be stored encrypted on
  this server and used only to read your leagues. Deleting the account here erases it. ESPN's terms
  do not permit this kind of access; you are choosing to do it."* The synthetic copy stays for
  `public_synthetic`; the local copy for `private_operator`.
- The accounts empty-state and the footer become mode-aware in the same change.
- **Test:** the SPA pin test grows a third branch; the copy's two sentences are pinned by literal.

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
- G7: `X-Anthropic-Key` (or equivalent) read per request, used for that call, never stored, never
  logged; the host-key ledger path stays for `public_synthetic`. Pinned by a test that greps the
  log capture for the key after a request that used it. Specified fully when G6 is done, not before.

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

## Non-goals

Scheduled or background syncing (the worker loop stays parked); a per-tenant rate limiter; the
CloudWatch metrics sink; a second host or AZ; any ESPN write; any user who did not receive an
invitation from the operator; a twelfth user without revisiting ADR-012.

## Grants and gates

Every gate is an approval the operator gives in words, recorded in the orchestration state before
the work it admits begins. G1 admits WU-1 (code only). G2's apply admits nothing new at runtime. G3
is the first custody change. G4 is the first behaviour change on the site. G5 is the first live ESPN
call from AWS. G6 is the first stranger's credential. G7 is the first AI call on the hosted site.
