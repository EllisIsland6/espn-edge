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
no ALB, no interface endpoint, no WAF, no KMS custody key, no private stack. The CDK draft in
`infra/app.py` is the *unselected* topology and stays an artefact.

## What is not in Terraform, deliberately

- **Domain registration.** A registrar transaction is not a resource to plan. The hosted zone it
  created is a `data` source.
- **The application role and its password.** Created as the owner after the first apply
  (`docs/aws-deploy.md` §2), written into the `espn-edge/database-url-app` secret by hand. A password
  is not something to put in state.
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
(bounded by a policy that *denies* NAT, ALB, interface endpoints, WAF, KMS keys, IAM users and
access keys, whatever is attached later). Keep this root's local `terraform.tfstate` with the
account; it is gitignored.

If the account already has a GitHub OIDC provider, pass `-var create_github_oidc_provider=false`.

**Re-apply this root whenever the deploy boundary changes** (it did on 2026-10-07: a scoped,
conditional Deny so the free S3 gateway endpoint is allowed while interface endpoints stay
denied, and the IAM tag action the instance profile needs). The plan only ever ran under the
plan role, so the boundary is probed directly, read-only, before the first stack apply:

```bash
ROLE=$(terraform output -raw deploy_role_arn)
ACCT=$(aws sts get-caller-identity --query Account --output text)
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names ec2:CreateVpcEndpoint \
  --resource-arns "arn:aws:ec2:us-east-1:$ACCT:vpc-endpoint/*" \
  --context-entries ContextKeyName=ec2:VpceServiceName,ContextKeyValues=com.amazonaws.us-east-1.s3,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: allowed
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names ec2:CreateVpcEndpoint \
  --resource-arns "arn:aws:ec2:us-east-1:$ACCT:vpc-endpoint/*" \
  --context-entries ContextKeyName=ec2:VpceServiceName,ContextKeyValues=com.amazonaws.us-east-1.ssm,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: explicitDeny
aws iam simulate-principal-policy --policy-source-arn "$ROLE" \
  --action-names iam:TagInstanceProfile iam:CreateInstanceProfile ec2:CreateNatGateway \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: allowed allowed explicitDeny
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names dynamodb:PutItem \
  --resource-arns "$(terraform output -raw lock_table | sed "s#^#arn:aws:dynamodb:us-east-1:$ACCT:table/#")" \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: allowed -- the boundary caps the role's own state policy too
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateGrant \
  --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=acm.us-east-1.amazonaws.com,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: allowed -- ACM, EBS and RDS create grants on aws/* keys
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateGrant \
  --context-entries ContextKeyName=kms:ViaService,ContextKeyValues=lambda.us-east-1.amazonaws.com,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: implicitDeny -- not through a service the stack uses
aws iam simulate-principal-policy --policy-source-arn "$ROLE" --action-names kms:CreateKey \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text      # expect: explicitDeny -- the custody line
```

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
./ops/preflight-image.sh                  # 18 checks, must pass
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

## Guarantees, and where each is held

| Guarantee (Phase 42) | Held by |
| --- | --- |
| No NAT, ALB, interface endpoint, WAF, KMS custody key, Aurora, IAM user or key | `test_no_forbidden_resource_types_in_the_stack`; the deploy boundary's `Deny`; the plan workflow's JSON check |
| Private subnets have no route out | `test_private_route_table_has_no_route_out` |
| Host answers CloudFront only, on 443, no SSH | `test_the_host_admits_only_cloudfront_on_443`, `test_no_ssh_and_no_key_pair` |
| Database private, single-AZ, encrypted, password never in state | `test_the_database_is_private_single_az_encrypted` |
| `public_synthetic`, no `FERNET_KEY`, every variable read | `test_every_task_runs_public_synthetic_and_no_fernet_key`, `test_every_variable_the_stack_sets_is_one_the_application_reads` |
| No long-lived GitHub key; plan cannot deploy | `test_roles_trust_only_this_repository_via_oidc`, `test_the_plan_role_cannot_write` |
| Budget before anything else | `test_the_budget_exists_before_anything_else_can` |
| Alarms are Phase 41's catalog, not a copy | `ops/render_alarms.py --check`, run as a test |

Every one of those was control-removed: the resource planted, the route added, the mode flipped,
the Deny deleted, port 22 opened — each fails the test that names it.
