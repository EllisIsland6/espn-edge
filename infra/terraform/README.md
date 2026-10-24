# Terraform — the selected topology, plan-only

**Status.** Written and policy-tested offline; **initialised, validated and planned by the operator
in his own account on 2026-10-07 — bootstrap applied, stack not applied.** The environments this was
written in cannot reach the Terraform registry or any AWS endpoint, so every Terraform command runs
in the operator's terminal. python-hcl2 parses every file and `tests/test_terraform_policy.py`
(23 tests) asserts the architecture's guarantees against the text; provider-schema errors are not
something that parser can see. The first `validate` found none; the first `plan` was the first real
review and found one defect (CloudFront's error-page rewrite masking API status codes), fixed before
the re-plan: **88 to add, 0 to change, 0 to destroy**, kept at `docs/evidence/phase-42-first-plan.txt`.
**The stack `apply` is a separate approval.**

This is the architecture `docs/sprint-9/03-architecture.md` selects, at **$34.26/month** by its
own bill: one public-egress `t4g.small` with an EIP running ECS-on-EC2, single-AZ RDS PostgreSQL 16
in a private subnet, S3 + CloudFront in front, Route 53 + ACM, Cognito's hosted UI. No NAT gateway,
no ALB, no interface endpoint, no WAF, no private stack, and one customer-managed KMS key --
ADR-012's custody key, behind gate G3. The CDK draft in
`infra/app.py` is the *unselected* topology and stays an artefact.

## What is not in Terraform, deliberately

- **Domain registration.** A registrar transaction is not a resource to plan. The hosted zone it
  created is a `data` source.
- **The application role and its password.** Created as the owner after the first apply
  (`docs/aws-deploy.md` §2), written into the `espn-edge/database-url-app` secret by hand. A password
  is not something to put in state.
- **The Fernet secret** (ADR-012). Secrets Manager validates a new secret's key by decrypting with
  it on the caller's behalf, and the deploy role may never decrypt with the custody key -- so the
  operator creates `espn-edge/fernet-key`, with its value, in one command, and the stack reads its
  ARN (a `data` source behind `wire_fernet_key`). The key and its alias are the stack's.
- **Users, memberships, identities.** Provisioned as the owner with the recipe in
  `alembic/versions/0017_identities.py` — not three plain INSERTs; `FORCE ROW LEVEL SECURITY` binds
  the owner too.
- **The worker service's running task.** Defined at `desired_count = 0` and held there by test: no
  long-running entrypoint exists (Phase 39 narrowed). See `KNOWN_MISSING_ENTRYPOINTS`.
- **The CloudWatch metrics sink.** The ten catalog alarms are applied and will sit in ALARM until the
  application publishes (amendment C1: silence is a condition). That is the signal the sink is next.
- **The RDS credit-guard Lambda** from the architecture diagram, and the **N−1 compatibility CI job**.
  Both deferred; neither blocks a plan.

## Order of operations

Everything below runs on **your** machine or in GitHub Actions. Nothing here can be run from the
session that wrote it.

### 0. Prerequisites in the account (once)

- Root: MFA on, no access keys.
- The domain registered in Route 53 **in this account**, hosted zone present (`eaedgefinder.com`),
  and the registrant email **verified -- checked, not remembered**: the registrar suspends the domain
  (`clientHold`, nothing resolves) when the verification is not done within 15 days of registration,
  which is what happened on 2026-10-22, fifteen days after the 7th.

```bash
aws route53domains get-contact-reachability-status --region us-east-1 --domain-name eaedgefinder.com --output json
echo 'expect: DONE -- PENDING means click the email; resend it with: aws route53domains resend-contact-reachability-email --region us-east-1 --domain-name eaedgefinder.com'
aws route53domains get-domain-detail --region us-east-1 --domain-name eaedgefinder.com --query StatusList --output json
echo 'expect: no clientHold or serverHold'
```
- An email you read, for the budget, the alarms and SES.

### 1. Bootstrap (once, with your own credentials)

```bash
cd infra/terraform/bootstrap
terraform init
terraform plan \
  -var state_bucket_name=espn-edge-tfstate-<account-id> \
  -var budget_alert_email=you@example.com
terraform apply ...same vars...
```

Creates: the state bucket and lock table, **the budget alarms (80% actual, 100% forecast of $52)**,
the GitHub OIDC provider, the **plan role** (`ReadOnlyAccess` + state) and the **deploy role**
(bounded by a policy that *denies* NAT, ALB, interface endpoints, WAF, IAM users, access keys
and any Decrypt with the custody key, whatever is attached later). Keep this root's local `terraform.tfstate` with the
account; it is gitignored.

If the account already has a GitHub OIDC provider, pass `-var create_github_oidc_provider=false`.

**Re-apply this root whenever the deploy boundary changes** (it did on 2026-10-07: a scoped,
conditional Deny so the free S3 gateway endpoint is allowed while interface endpoints stay
denied, and the IAM tag action the instance profile needs; and again on 2026-10-17 for ADR-012:
`kms:CreateKey` left the Deny, the custody-key statements arrived, and the AWS-managed-key Allow
is scoped to `alias/aws/*` by `kms:ResourceAliases` -- a condition of the key, not the request,
valid for every operation on a key, and every AWS-managed key carries its `alias/aws/<service>`
alias in the account). The plan only ever ran under the plan role, so the boundary is probed
directly, read-only, before the first stack apply:

```bash
ROLE=$(terraform output -raw deploy_role_arn)
PLAN_ROLE=$(terraform output -raw plan_role_arn)
ACCT=$(aws sts get-caller-identity --query Account --output text)
LOCK_ARN="arn:aws:dynamodb:us-east-1:${ACCT}:table/$(terraform output -raw lock_table)"
echo 'expect: allowed'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names ec2:CreateVpcEndpoint --resource-arns "arn:aws:ec2:us-east-1:${ACCT}:vpc-endpoint/*" --context-entries ContextKeyName=ec2:VpceServiceName,ContextKeyValues=com.amazonaws.us-east-1.s3,ContextKeyType=string --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: explicitDeny'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names ec2:CreateVpcEndpoint --resource-arns "arn:aws:ec2:us-east-1:${ACCT}:vpc-endpoint/*" --context-entries ContextKeyName=ec2:VpceServiceName,ContextKeyValues=com.amazonaws.us-east-1.ssm,ContextKeyType=string --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed allowed explicitDeny'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names iam:TagInstanceProfile iam:CreateInstanceProfile ec2:CreateNatGateway --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed (the boundary caps the role'"'"'s own state policy too)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names dynamodb:PutItem --resource-arns "$LOCK_ARN" --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed (ACM, EBS and RDS create grants on aws/* keys)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateGrant --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=acm.us-east-1.amazonaws.com,ContextKeyType=string ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/aws/acm,ContextKeyType=stringList --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: implicitDeny (not through a service the stack uses)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateGrant --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=lambda.us-east-1.amazonaws.com,ContextKeyType=string ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/aws/lambda,ContextKeyType=stringList --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed (ADR-012: the stack creates exactly one key, the custody key)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateKey --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: explicitDeny (the deploy role never decrypts with the custody key, not even through Secrets Manager)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:Decrypt --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=secretsmanager.us-east-1.amazonaws.com,ContextKeyType=string ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/espn-edge/custody,ContextKeyType=stringList --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: explicitDeny (nor generate a data key with it -- the deploy role never uses the key, which is why the secret is the operator'"'"'s to create)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:GenerateDataKey --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=secretsmanager.us-east-1.amazonaws.com,ContextKeyType=string ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/espn-edge/custody,ContextKeyType=stringList --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed (the aws/secretsmanager key behind the other secrets is untouched)'
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:Decrypt --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=secretsmanager.us-east-1.amazonaws.com,ContextKeyType=string ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/aws/secretsmanager,ContextKeyType=stringList --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
echo 'expect: allowed for session-secret, implicitDeny for database-url-app'
aws iam simulate-principal-policy --policy-source-arn "$PLAN_ROLE" --action-names secretsmanager:GetSecretValue --resource-arns "arn:aws:secretsmanager:us-east-1:${ACCT}:secret:espn-edge/session-secret-AAAAAA" "arn:aws:secretsmanager:us-east-1:${ACCT}:secret:espn-edge/database-url-app-AAAAAA" --query 'EvaluationResults[].ResourceSpecificResults[].[EvalResourceName,EvalResourceDecision]' --output text
```

The block is written to be pasted whole into an interactive zsh: no comments (zsh passes `# ...`
to the command unless `interactivecomments` is set), no line continuations, the expectation as an
`echo` before each probe, and `${ACCT}` braced because `$ACCT:secret` and `$ACCT:table` are zsh
modifiers.

### 2. Repository settings

Actions → Variables (none are secrets):

| Variable | From |
| --- | --- |
| `AWS_PLAN_ROLE_ARN`, `AWS_DEPLOY_ROLE_ARN` | bootstrap outputs |
| `TF_STATE_BUCKET`, `TF_LOCK_TABLE` | bootstrap outputs |
| `OPERATOR_EMAIL` | you |
| `APP_IMAGE_TAG`, `CADDY_IMAGE_TAG` | the immutable tags you push to ECR (step 4) |

Environments → `production` → **Required reviewers: you**. The deploy role trusts only this
environment's OIDC subject; the apply workflow cannot assume it from anywhere else.

### 3. First plan

Open a pull request touching `infra/terraform/stack/`. The `terraform plan` workflow runs the
offline policy tests, then `fmt`, `init`, `validate`, `plan` with the read-only role, refuses any
plan that would create a rejected resource type, uploads the plan, and comments the summary. **Paste
or commit the plan under `docs/evidence/` for review** — that is how the session that wrote this
reads it.

The first plan will not succeed until ECR holds the images the stack references (step 4) — ECS task
definitions reference image URLs by tag, and `plan` will accept that, but `apply` would fail to
start tasks. Build images first.

### 4. Images

```bash
./ops/preflight-image.sh <ecr_app>:<sha>  # 19 checks, must pass; builds the tag it will push
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr>
docker build --platform linux/arm64 -t <ecr_app>:<sha> .          # arm64: the host is Graviton
docker build --platform linux/arm64 -t <ecr_caddy>:<sha> ops/caddy
docker push ...
```

Set `APP_IMAGE_TAG` / `CADDY_IMAGE_TAG` to those tags. Repositories are `IMMUTABLE`; `latest` is
refused by test.

### 5. Apply (behind the reviewer)

Actions → **terraform apply** → type `apply`. The job re-plans, waits for the environment reviewer,
applies the saved plan. Then, in order (`docs/aws-deploy.md`):

1. Create the application role as the owner (§2); write its URL into `espn-edge/database-url-app`.
2. Write the owner URL into `espn-edge/database-url-owner` (the password is in the RDS-managed secret
   the stack outputs).
3. Run the migrate task (`aws ecs run-task` with `migrate_task_definition`).
4. Provision yourself (0017's recipe). Anyone else: section 7, and not before gate G6.
5. Upload `web/dist` to the SPA bucket; invalidate CloudFront.
6. Restart the web service; watch `/espn-edge/web` for Caddy obtaining the origin certificate
   (DNS-01 against `_acme-challenge.origin.eaedgefinder.com`, the only record its role may write).
7. `https://eaedgefinder.com/api/health`, then sign in.

### 6. Gate G3 -- the custody key, in two applies (ADR-012)

The Fernet secret is the operator's to create. Secrets Manager validates a new secret's key by
calling `GenerateDataKey` and `Decrypt` with it on the caller's behalf (its documentation names the
calls: `SecretVersionId` `RequestToValidateKeyAccess`), and the deploy role may never decrypt with
the custody key -- a refusal no policy test could see, found by reading the service's own page on
how it uses KMS before the first apply. So the key and its alias are the stack's; the secret is
created by hand, with its value, in one command; and the stack reads its ARN behind
`wire_fernet_key`. Every apply is plan-first (lesson 47): run **terraform plan** on the commit,
read its `Plan:` line against the prediction, then **terraform apply**.

1. Re-apply the bootstrap root (the boundary changed) and run the probe block above, including
   the four ADR-012 lines.
2. Stack apply with `wire_fernet_key` at its default `false`. Predicted: `2 to add` (the key and
   its alias), nothing changed, nothing destroyed, no task definition touched -- plus whatever an
   image-tag change in the same commit brings.
3. **After step 2 has applied**, create the secret in your terminal, its value from a variable that
   is never printed. The ARN the command returns is not secret. The block guards its precondition:
   with no key, `KEY_ARN` is empty and nothing is created. Then the read-only checks:

```bash
cd ~/espn-edge
KEY_ARN=$(aws kms describe-key --key-id alias/espn-edge/custody --query KeyMetadata.Arn --output text)
echo "custody key: ${KEY_ARN}"
echo 'expect: an arn:aws:kms:... line above; if it is empty or an error, step 2 has not applied -- stop here'
FK=$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
[ -n "${KEY_ARN}" ] && aws secretsmanager create-secret --name espn-edge/fernet-key --kms-key-id "${KEY_ARN}" --secret-string "${FK}" --description "ADR-012: the Fernet key behind ESPN sessions at rest. Created by the operator, read by Terraform, never rotated." --query ARN --output text
unset FK
echo 'expect: AWSCURRENT'
aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query 'VersionIdsToStages.*[]' --output text
echo 'expect: the same ARN twice (the secret is encrypted with the custody key)'
echo "${KEY_ARN}"
aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query KmsKeyId --output text
echo 'expect: true (the KMS key rotates; the Fernet value never does -- a new value makes every stored session unreadable)'
aws kms get-key-rotation-status --key-id "${KEY_ARN}" --query KeyRotationEnabled --output text
```

   If `KmsKeyId` prints `None`, the secret was created before the key existed and sits under the
   default `aws/secretsmanager` key (it happened on 2026-10-22: the block was run ahead of step 2,
   with an empty `KEY_ARN` and no guard). One command moves it; Secrets Manager re-encrypts the
   existing version with the new key, so the value stays:

```bash
aws secretsmanager update-secret --secret-id espn-edge/fernet-key --kms-key-id "${KEY_ARN}" --query ARN --output text
aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query KmsKeyId --output text
```

4. In one commit (made 2026-10-22): `default = false` becomes `true` in `stack/variables.tf`, and
   the `is False` assertion in `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired`
   becomes `is True` -- the test records which side of the secret's creation the stack is on. Plan-first
   apply: predicted `1 to add, 2 to change, 1 to destroy` (the web task definition is replaced by a
   revision carrying `FERNET_KEY`; the execution role's secrets policy and the web service change
   in place). ECS rolls the service. Then the last probe, and `https://eaedgefinder.com/api/health`
   still answers `public_synthetic` -- the key is loaded and unused until G4 flips the mode, which
   is its own approval (section 6b):

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
FK_ARN=$(aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query ARN --output text)
echo 'expect: allowed (the web task execution role is the one reader; before step 4 this is implicitDeny)'
aws iam simulate-principal-policy --policy-source-arn "arn:aws:iam::${ACCT}:role/espn-edge-task-execution" --action-names secretsmanager:GetSecretValue --resource-arns "${FK_ARN}" --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
aws ecs describe-services --cluster espn-edge --services web --query 'services[0].deployments[].[status,taskDefinition,runningCount,failedTasks,rolloutState]' --output text
echo 'expect: PRIMARY, the new revision, 1, 0, COMPLETED -- the task starting is the proof: ECS fetched the secret and KMS decrypted it with the custody key'
```

   The execution role's policy names the secret's exact ARN (the six-character suffix included), so
   the `-AAAAAA` placeholder that works against the plan role's wildcard reads `implicitDeny` here;
   resolve the ARN first, as above.

Nothing in these steps prints, logs or stores the value anywhere but the secret: Terraform never
holds the value or the secret (a `data` source reads the ARN), the plan role cannot read the value
(`managed-secret-values` names three other secrets), and the deploy role cannot use the key.

### 6b. Gate G4 -- the mode switch (ADR-012)

One commit (made 2026-10-23): `local.web_environment` in `stack/containers.tf` is the shared
environment with `APP_MODE` rewritten to `private_invited`, and the web task's `app` container
reads it; migrate and the parked worker keep `public_synthetic`; the `custody` output and
`test_the_web_task_is_the_invited_site_and_the_other_tasks_stay_synthetic` say so. No image, no
migration, no IAM change. Plan-first: predicted `1 to add, 1 to change, 1 to destroy` -- the web
task definition replaced by a revision whose only difference is that literal, and the web service
moved to it. Then the reads, from your terminal:

```bash
aws ecs describe-services --cluster espn-edge --services web --query 'services[0].deployments[].[status,taskDefinition,runningCount,failedTasks,rolloutState]' --output text
echo 'expect: PRIMARY, espn-edge-web:8, 1, 0, COMPLETED'
curl -sS https://eaedgefinder.com/api/health
echo
echo 'expect: status ok, mode private_invited, the postgresql backend'
```

Then, signed in: the footer reads "ESPN Edge · hosted for an invited group", Manage shows the
consent sentences and the account form, and the accounts list is empty. No account exists, so no
provider has been constructed and nothing has touched ESPN; the first live read is gate G5 and is
yours to perform, with your own session, when you say so.

### 6c. Gate G5 -- the first live ESPN read, your own session (ADR-012)

Nothing before this touches ESPN from AWS, and nobody can do it for you: on the live Manage page,
signed in, add your own account (SWID and espn_s2 from your browser's cookies, as locally), add
one of your leagues, press Sync. The page shows the sync's outcome. Then the reads, which are the
gate's proof -- the raw cache row is your tenant's, the limiter's cadence is visible, no credential
reached the log:

```bash
cd ~/espn-edge
aws logs tail /espn-edge/web --since 30m --format short | grep -c -E 'espn_s2=[A-Za-z0-9%._-]{8,}|SWID=\{[0-9A-Fa-f-]{8,}'
echo 'expect: 0 -- no cookie value in any log line (Caddy redacts Cookie; the app never logs one)'
aws logs tail /espn-edge/web --since 30m --format short | grep -E 'league [0-9]+:|Traceback|Error' | tail -10
echo 'expect: nothing -- the sync logs only its failures (auth failed, fetch failed, metrics); a clean sync is silent'
aws logs tail /espn-edge/web --since 30m --format short | grep -E '"uri":"/api/leagues/[0-9]+/sync"' | grep -o '"status":[0-9]*' | sort | uniq -c
echo 'expect: one or more "status":200 -- the sync requests Caddy saw, and their answers'
```

Then, through the tunnel (section 7, Terminal 1 open; Terminal 2 with `PGPASSWORD` and
`DATABASE_URL` set exactly as there):

```bash
cd ~/espn-edge
.venv/bin/python ops/cache_rows.py --tenant-id 1 --since-minutes 60
echo 'expect: one line per fetched view, every line tenant=1, consecutive fetches +1000 ms or more apart; the payload itself is never printed'
unset PGPASSWORD
```

Last, the erase check: remove the league on the page, then delete the account (the delete refuses
with 409 while a league still points at the account -- by design, so a delete can never orphan
synced data). The accounts list empties; the ciphertext row is gone, not hidden. `cache_rows.py`
still shows the cache rows, because the raw cache is the tenant's, not the account's, and it is
yours to keep or clear. Add the account and the league again afterwards if you want to keep using
the site.

### 7. Inviting a person -- gate G6, one person at a time (ADR-012)

The person signs up in Cognito (invitation-only there); you read their `sub` from the pool; the
script makes them their own tenant. It runs as the owner through an SSM tunnel to the host, the
password from the RDS-managed secret into `PGPASSWORD` and nowhere else. Terminal 1 holds the
tunnel open:

```bash
HOST=$(aws ec2 describe-instances --filters Name=tag:Name,Values=espn-edge-host Name=instance-state-name,Values=running --query 'Reservations[].Instances[].InstanceId' --output text)
RDS=$(aws rds describe-db-instances --db-instance-identifier espn-edge --query 'DBInstances[0].Endpoint.Address' --output text)
echo "tunnel to ${RDS} through ${HOST}"
aws ssm start-session --target "${HOST}" --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=${RDS},portNumber=5432,localPortNumber=15432"
```

Terminal 2, from the repo root, with the person's email and `sub` typed in place of the two
placeholders (the only two things in this block that are not a read):

```bash
cd ~/espn-edge
SECRET_ARN=$(aws rds describe-db-instances --db-instance-identifier espn-edge --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
export PGPASSWORD=$(aws secretsmanager get-secret-value --secret-id "${SECRET_ARN}" --query SecretString --output text | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["password"])')
export DATABASE_URL='postgresql+psycopg2://edge_owner@127.0.0.1:15432/edge?sslmode=require'
export OIDC_ISSUER=$(aws cognito-idp list-user-pools --max-results 10 --query 'UserPools[?Name==`espn-edge`].Id' --output text | sed 's#^#https://cognito-idp.us-east-1.amazonaws.com/#')
echo "${OIDC_ISSUER}"
echo 'expect: https://cognito-idp.us-east-1.amazonaws.com/us-east-1_... -- stop if it is empty'
.venv/bin/python ops/provision_user.py provision PERSON_EMAIL PERSON_SUB
echo 'expect: tenant_id=N user_id=N membership_id=N, one line, nothing else'
unset PGPASSWORD
```

`revoke PERSON_SUB` in place of the provision line undoes one person and prints a count per table;
it refuses your own tenant, a user with more than one membership and a tenant with more than one
member. The script never prints an email or a subject, in success or failure -- the terminal is a
log. Its PostgreSQL proof is `tests/test_provision_user.py`, run against a migrated cluster as a
NOSUPERUSER NOBYPASSRLS owner, which is what `edge_owner` is on RDS.

### 8. Each person's own AI key -- gate G7 (ADR-012)

Decided by the operator on 2026-10-23: *they pay for their own AI API, and we store what's needed
and practical, per best practice.* Built as WU-7 (`docs/phase-46-hosted-real-data.md`): one
`ai_credentials` row per tenant, the key Fernet ciphertext through `api/crypto.py` under the custody
key, policied `tenant_id = current_tenant()` (revision 0018), decrypted at the moment of the call,
never returned after saving, never logged, erased by `DELETE`. The invited site never uses a host
key; the other two modes are unchanged. It is a backend change, so it reaches the site as an
image, a migration and a plan-first apply -- in that order, because the migrate task must run the
new image before the web task that expects the table does:

1. Build, preflight and push the app image for the commit (section 4); set `APP_IMAGE_TAG` to it.
2. **terraform plan** on the commit. Predicted: `2 to add, 2 to change, 2 to destroy` -- the web and
   migrate task definitions replaced for the new tag, the web service and the migrate task family
   moved to them, nothing else. (The worker's definition carries the tag too; if the plan counts it,
   expect `3 / 2 / 3` and say so before the apply.)
3. **terraform apply**, approved by you. ECS rolls the web service onto the new image; until the
   migration runs the new code answers `GET /api/ai/key` with a 500 on a missing table, which is
   why step 4 follows at once.
4. Run the migrate task (`aws ecs run-task` with `migrate_task_definition`, as in section 5 step 3)
   and read its log: `Running upgrade 0017 -> 0018`.

```bash
aws logs tail /espn-edge/migrate --since 10m --format short | grep -E 'Running upgrade|ERROR|Traceback'
echo 'expect: Running upgrade 0017 -> 0018, ai_credentials ...; no ERROR, no Traceback'
curl -sS https://eaedgefinder.com/api/health
echo
echo 'expect: status ok, mode private_invited'
```

5. **The gate.** Signed in, on Manage: the "AI key" panel reads its three sentences; paste your own
   Anthropic key; the panel then shows the provider, the key's last four characters and "last used
   never". Ask for one report on one league. Then, in your terminal, the log read that is the
   acceptance criterion:

```bash
KEY_TAIL=xxxx
aws logs tail /espn-edge/web --since 15m --format short | grep -c -- "${KEY_TAIL}"
echo 'expect: 0 -- set KEY_TAIL to the last four characters the panel shows; the count of log lines carrying them must be zero'
aws logs tail /espn-edge/web --since 15m --format short | grep -i -E 'anthropic|ai key|AiError|APIError' | tail -5
echo 'expect: at most an exception CLASS name on a failure; never a message carrying a value'
```

Delete the key on the panel to prove the erase, and paste it again if you want to keep using it.
Everyone you invite does the same on their own Manage page; their key is theirs, their bill is
theirs, and revoking them (section 7) takes the row with them.

## Guarantees, and where each is held

| Guarantee (Phase 42; ADR-012 rows marked) | Held by |
| --- | --- |
| No NAT, ALB, interface endpoint, WAF, Aurora, IAM user or key; one KMS key, the custody key (ADR-012) | `test_no_forbidden_resource_types_in_the_stack`, `test_the_only_kms_key_is_the_custody_key`; the deploy boundary's `Deny`; the plan workflow's JSON check, which admits `aws_kms_key.custody` by name |
| The custody key has one application reader; the deploy role administers it and never uses it; the secret is the operator's, read not created (ADR-012) | `test_the_custody_key_policy_names_one_application_reader`, `test_the_deploy_role_can_administer_but_never_use_the_custody_key`, `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired`; the simulator probes above |
| Private subnets have no route out | `test_private_route_table_has_no_route_out` |
| Host answers CloudFront only, on 443, no SSH | `test_the_host_admits_only_cloudfront_on_443`, `test_no_ssh_and_no_key_pair` |
| Database private, single-AZ, encrypted, password never in state | `test_the_database_is_private_single_az_encrypted` |
| The web task is `private_invited` since G4 (2026-10-23); migrate and the worker stay `public_synthetic`; no host AI key; `FERNET_KEY` reaches the web task only, and only once wired; every variable read | `test_the_web_task_is_the_invited_site_and_the_other_tasks_stay_synthetic`, `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired`, `test_every_variable_the_stack_sets_is_one_the_application_reads` |
| Each tenant's AI key: one row per tenant, ciphertext through the custody chokepoint, readable by that tenant alone, never returned after saving, never the host's key on the invited site, never in a backup bundle (ADR-012 G7) | `tests/test_ai_key_custody.py` (the PostgreSQL half for the isolation), `test_a_tenants_ai_key_never_travels`, `test_every_table_is_classified`; this file's section 8 log read at the gate |
| No long-lived GitHub key; plan cannot deploy | `test_roles_trust_only_this_repository_via_oidc`, `test_the_plan_role_cannot_write` |
| Budget before anything else | `test_the_budget_exists_before_anything_else_can` |
| Alarms are Phase 41's catalog, not a copy | `ops/render_alarms.py --check`, run as a test |

Every one of those was control-removed: the resource planted, the route added, the mode flipped,
the Deny deleted, port 22 opened, Decrypt handed to the deploy role, a data-key action dropped from
its Deny, the alias scope dropped from the AWS-managed Allow, the alias string drifted between
roots, the wiring gate flipped early, a second reader named on the key, the secret planted as a
Terraform resource, the AI key's chokepoint bypassed, its verification skipped, the key echoed in
a response, the host key used on the invited site, the ledger consulted for a tenant's own spend,
the failure message recorded instead of its class, the 422 redaction forgetting the PUT, the
revoke order forgetting the table, row security switched off on it — each fails the test that
names it.
