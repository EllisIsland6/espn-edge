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
- The domain registered in Route 53 **in this account**, verification email clicked, hosted zone
  present (`eaedgefinder.com`).
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
4. Provision yourself (0017's recipe).
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
3. Create the secret in your terminal, its value from a variable that is never printed. The ARN
   the command returns is not secret. Then the read-only checks:

```bash
cd ~/espn-edge
KEY_ARN=$(aws kms describe-key --key-id alias/espn-edge/custody --query KeyMetadata.Arn --output text)
FK=$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
aws secretsmanager create-secret --name espn-edge/fernet-key --kms-key-id "${KEY_ARN}" --secret-string "${FK}" --description "ADR-012: the Fernet key behind ESPN sessions at rest. Created by the operator, read by Terraform, never rotated." --query ARN --output text
unset FK
echo 'expect: AWSCURRENT'
aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query 'VersionIdsToStages.*[]' --output text
echo 'expect: the same ARN twice (the secret is encrypted with the custody key)'
echo "${KEY_ARN}"
aws secretsmanager describe-secret --secret-id espn-edge/fernet-key --query KmsKeyId --output text
echo 'expect: true (the KMS key rotates; the Fernet value never does -- a new value makes every stored session unreadable)'
aws kms get-key-rotation-status --key-id alias/espn-edge/custody --query KeyRotationEnabled --output text
```

4. In one commit: `default = false` becomes `true` in `stack/variables.tf`, and the `is False`
   assertion in `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired` becomes
   `is True` -- the test records which side of the secret's creation the stack is on. Plan-first
   apply: predicted `1 to add, 2 to change, 1 to destroy` (the web task definition is replaced by a
   revision carrying `FERNET_KEY`; the execution role's secrets policy and the web service change
   in place). ECS rolls the service. Then the last probe, and `https://eaedgefinder.com/api/health`
   still answers `public_synthetic` -- the key is loaded and unused until G4 flips the mode, which
   is its own approval:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
echo 'expect: allowed (the web task execution role is the one reader; before step 4 this is implicitDeny)'
aws iam simulate-principal-policy --policy-source-arn "arn:aws:iam::${ACCT}:role/espn-edge-task-execution" --action-names secretsmanager:GetSecretValue --resource-arns "arn:aws:secretsmanager:us-east-1:${ACCT}:secret:espn-edge/fernet-key-AAAAAA" --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text
```

Nothing in these steps prints, logs or stores the value anywhere but the secret: Terraform never
holds the value or the secret (a `data` source reads the ARN), the plan role cannot read the value
(`managed-secret-values` names three other secrets), and the deploy role cannot use the key.

## Guarantees, and where each is held

| Guarantee (Phase 42; ADR-012 rows marked) | Held by |
| --- | --- |
| No NAT, ALB, interface endpoint, WAF, Aurora, IAM user or key; one KMS key, the custody key (ADR-012) | `test_no_forbidden_resource_types_in_the_stack`, `test_the_only_kms_key_is_the_custody_key`; the deploy boundary's `Deny`; the plan workflow's JSON check, which admits `aws_kms_key.custody` by name |
| The custody key has one application reader; the deploy role administers it and never uses it; the secret is the operator's, read not created (ADR-012) | `test_the_custody_key_policy_names_one_application_reader`, `test_the_deploy_role_can_administer_but_never_use_the_custody_key`, `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired`; the simulator probes above |
| Private subnets have no route out | `test_private_route_table_has_no_route_out` |
| Host answers CloudFront only, on 443, no SSH | `test_the_host_admits_only_cloudfront_on_443`, `test_no_ssh_and_no_key_pair` |
| Database private, single-AZ, encrypted, password never in state | `test_the_database_is_private_single_az_encrypted` |
| `public_synthetic` until G4; `FERNET_KEY` reaches the web task only, and only once wired; every variable read | `test_every_task_runs_public_synthetic_until_gate_g4`, `test_the_fernet_secret_reaches_exactly_the_web_task_once_wired`, `test_every_variable_the_stack_sets_is_one_the_application_reads` |
| No long-lived GitHub key; plan cannot deploy | `test_roles_trust_only_this_repository_via_oidc`, `test_the_plan_role_cannot_write` |
| Budget before anything else | `test_the_budget_exists_before_anything_else_can` |
| Alarms are Phase 41's catalog, not a copy | `ops/render_alarms.py --check`, run as a test |

Every one of those was control-removed: the resource planted, the route added, the mode flipped,
the Deny deleted, port 22 opened, Decrypt handed to the deploy role, a data-key action dropped from
its Deny, the alias scope dropped from the AWS-managed Allow, the alias string drifted between
roots, the wiring gate flipped early, a second reader named on the key, the secret planted as a
Terraform resource — each fails the test that names it.
