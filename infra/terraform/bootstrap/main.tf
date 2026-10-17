# ------------------------------------------------------------- state backend
# Versioned so a bad apply's prior state is one click back; encrypted; private.

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "lock" {
  name         = "espn-edge-terraform-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# ------------------------------------------------------------- budget first
# Phase 42's guarantee list begins with cost. This exists before `apply` is
# typed in the stack root. Two notifications: 80% of the envelope on ACTUAL
# spend, and 100% on FORECAST -- the forecast one is the useful one, because
# it fires in the first week of a month that is going wrong.

resource "aws_budgets_budget" "monthly" {
  name         = "espn-edge-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

# ------------------------------------------------------- GitHub OIDC, no keys
# "No long-lived GitHub AWS key" is a Phase 42 guarantee. Actions presents a
# short-lived OIDC token; AWS checks the repository claim; the role's session
# lasts an hour. There is nothing to leak from the repository's settings.

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_github_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"] # unused by AWS for GitHub since 2023; the field is required
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# The PLAN role: may read everything, may touch state, may change nothing.
# A pull request from a fork cannot assume it -- the `sub` condition names
# this repository and only pull_request / main-branch refs.
data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:pull_request",
        "repo:${var.github_repository}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "espn-edge-terraform-plan"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "state_access" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.state.arn}/stack/*"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.lock.arn]
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "state"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state_access.json
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# The plan role may read the VALUES of exactly the secrets whose versions the
# stack manages. Refresh reads an aws_secretsmanager_secret_version with
# GetSecretValue, which ReadOnlyAccess deliberately lacks, so the first plan
# after the stack existed failed on all three (run 38285733572; the earlier
# plans had nothing in state to refresh). This is not a widening: those three
# values are also in the Terraform state, which this role already reads. The
# database URLs are not -- their versions were written by hand, outside
# Terraform, and hold the database passwords -- and they stay out, as does
# everything else. tests/test_terraform_policy.py pins the list to the
# stack's aws_secretsmanager_secret_version resources.
data "aws_caller_identity" "current" {}

locals {
  stack_managed_secret_names = [
    "espn-edge/session-secret",
    "espn-edge/oidc-client-secret",
    "espn-edge/origin-verify",
  ]
}

data "aws_iam_policy_document" "plan_managed_secret_values" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      for name in local.stack_managed_secret_names :
      "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${name}-*"
    ]
  }
}

resource "aws_iam_role_policy" "plan_managed_secret_values" {
  name   = "managed-secret-values"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_managed_secret_values.json
}

# The DEPLOY role: assumable only from the `production` environment of the
# main branch, which GitHub gates behind a required reviewer. Its permissions
# are bounded by an explicit boundary that can never grant the things Phase
# 42 forbids, whatever a future policy on this role says.
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:production"]
    }
  }
}

# What the stack root may create. Named services only, and an explicit Deny
# on the resources the architecture rejected, so that even a permissive
# policy attached later cannot buy a NAT gateway, an ALB, an interface
# endpoint or a WAF by accident. `tests/test_terraform_policy.py` asserts
# these Deny actions stay present. kms:CreateKey left the Deny with ADR-012:
# the stack now creates exactly one key, under the statements below.
data "aws_iam_policy_document" "deploy_boundary" {
  statement {
    sid = "StackServices"
    actions = [
      "ec2:*", "ecs:*", "ecr:*", "rds:*", "s3:*", "cloudfront:*", "acm:*",
      "route53:*", "cognito-idp:*", "ses:*", "secretsmanager:*", "logs:*",
      "cloudwatch:*", "sns:*", "ssm:*", "iam:GetRole", "iam:PassRole",
      "iam:CreateRole", "iam:DeleteRole", "iam:AttachRolePolicy",
      "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:TagRole", "iam:UntagRole", "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile", "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile", "iam:GetInstanceProfile",
      # default_tags land on the instance profile; CreateInstanceProfile
      # with tags needs the Tag action as well. Found by reading the first
      # plan against this boundary, not by running it.
      "iam:TagInstanceProfile", "iam:UntagInstanceProfile",
      "iam:ListInstanceProfilesForRole",
      "iam:CreateServiceLinkedRole", "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
  # The role's inline `state` policy grants the lock-table actions, but a
  # permissions boundary caps EVERY policy on the role, its own state policy
  # included -- and this document named no DynamoDB action, so the first
  # apply died acquiring the state lock before planning anything. Found by
  # running it: the simulator probes had only covered the stack's actions.
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.lock.arn]
  }
  # AWS-managed keys only, and only through the services that use them: EBS
  # for the host's root volume, RDS for storage and the managed master
  # password, ACM for the certificate's private key, Secrets Manager. The
  # first apply that got past the lock lost the host AND the certificate to
  # the Deny on kms:CreateGrant -- written to keep a custody key out, it also
  # stopped those services creating grants on the account's aws/* keys
  # ("InvalidKMSKey.InvalidState" on the instance; AccessDenied on ACM).
  # ViaService means the role cannot use KMS on its own account at all, and
  # the alias condition (ADR-012) means this statement reaches AWS-managed
  # keys only -- every aws/* key carries an alias/aws/* alias and the custody
  # key below carries alias/espn-edge/custody -- so the Decrypt granted here
  # can never be the custody key's.
  statement {
    sid       = "AwsManagedKeysViaService"
    actions   = ["kms:CreateGrant", "kms:DescribeKey", "kms:Decrypt", "kms:Encrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "ec2.${var.region}.amazonaws.com",
        "rds.${var.region}.amazonaws.com",
        "acm.${var.region}.amazonaws.com",
        "secretsmanager.${var.region}.amazonaws.com",
      ]
    }
    condition {
      test     = "ForAnyValue:StringLike"
      variable = "kms:ResourceAliases"
      values   = ["alias/aws/*"]
    }
  }
  # ADR-012: the stack creates ONE customer-managed key, the custody key for
  # the Fernet secret that encrypts ESPN sessions at rest. The deploy role
  # administers it -- creates, aliases, tags, rotates, can schedule its
  # deletion with the 30-day minimum -- and can encrypt into it through
  # Secrets Manager (CreateSecret with a kms_key_id). It can never decrypt
  # with it: the Deny below says so by alias, so no later Allow can change
  # that, and the key's own policy names the web task's execution role as
  # the one reader.
  statement {
    sid = "CustodyKeyAdministration"
    actions = [
      "kms:CreateKey", "kms:DescribeKey", "kms:GetKeyPolicy", "kms:PutKeyPolicy",
      "kms:GetKeyRotationStatus", "kms:EnableKeyRotation", "kms:ListResourceTags",
      "kms:TagResource", "kms:UntagResource", "kms:UpdateKeyDescription",
      "kms:EnableKey", "kms:DisableKey", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
      "kms:CreateAlias", "kms:UpdateAlias", "kms:DeleteAlias", "kms:ListAliases",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "CustodyKeyEncryptOnlyViaSecretsManager"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "ForAnyValue:StringLike"
      variable = "kms:ResourceAliases"
      values   = ["alias/espn-edge/custody"]
    }
  }
  statement {
    sid       = "CustodyKeyNeverDecryptedByDeploy"
    effect    = "Deny"
    actions   = ["kms:Decrypt", "kms:ReEncrypt*", "kms:CreateGrant"]
    resources = ["*"]
    condition {
      test     = "ForAnyValue:StringLike"
      variable = "kms:ResourceAliases"
      values   = ["alias/espn-edge/custody"]
    }
  }
  statement {
    sid    = "ArchitectureRejected"
    effect = "Deny"
    actions = [
      "ec2:CreateNatGateway",
      "elasticloadbalancing:*",
      "wafv2:*",
      "waf:*",
      "rds:CreateDBCluster",
      "iam:CreateUser",
      "iam:CreateAccessKey",
    ]
    resources = ["*"]
  }
  # Interface endpoints are rejected ($7.30/month each, and the architecture
  # has no private subnet that needs one); the free S3 *gateway* endpoint is
  # in the stack. An unconditional Deny on CreateVpcEndpoint refused it --
  # found by reading the first plan against this boundary. The statement is
  # scoped to the vpc-endpoint ARN on purpose: the same call also touches the
  # vpc and route-table resources, where ec2:VpceServiceName is absent from
  # the request context, and StringNotEquals on an absent key is true -- an
  # unscoped statement would deny the gateway endpoint as well.
  statement {
    sid       = "ArchitectureRejectedInterfaceEndpoints"
    effect    = "Deny"
    actions   = ["ec2:CreateVpcEndpoint"]
    resources = ["arn:aws:ec2:*:*:vpc-endpoint/*"]
    condition {
      test     = "StringNotEquals"
      variable = "ec2:VpceServiceName"
      values   = ["com.amazonaws.${var.region}.s3"]
    }
  }
}

resource "aws_iam_policy" "deploy_boundary" {
  name   = "espn-edge-deploy-boundary"
  policy = data.aws_iam_policy_document.deploy_boundary.json
}

resource "aws_iam_role" "deploy" {
  name                 = "espn-edge-terraform-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  permissions_boundary = aws_iam_policy.deploy_boundary.arn
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "deploy_state" {
  name   = "state"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.state_access.json
}

resource "aws_iam_role_policy_attachment" "deploy_services" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_boundary.arn
}
