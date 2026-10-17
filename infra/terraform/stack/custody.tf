# ADR-012 -- the custody key and the Fernet secret.
#
# ESPN sessions are stored as Fernet ciphertext; FERNET_KEY is what unlocks
# them. In the synthetic deployment no such key existed (that was the custody
# boundary). For the invited group the key lives here: a Secrets Manager
# secret encrypted with the one customer-managed KMS key the stack creates.
# The key's own policy names the reader -- the web task's execution role,
# through Secrets Manager only -- and the deploy role that creates all of
# this can administer the key and encrypt into it but never decrypt with it
# (the boundary says so by alias; see bootstrap/main.tf).
#
# The VALUE is never Terraform's: the operator generates it in his terminal
# and writes it with put-secret-value, so it is in no plan, no state, no log.

data "aws_iam_policy_document" "custody_key" {
  # The lock-out guard every key policy carries, and what lets IAM policies
  # in this account govern the key. The account's administrators -- the
  # operator -- can therefore reach it; ADR-006 already names that as the
  # custody trust. The deploy role's IAM policy allows no Decrypt on this
  # alias, so this statement does not reach it.
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  # The one application reader: the execution role fetching the Fernet
  # secret at task start, through Secrets Manager and nothing else.
  statement {
    sid       = "WebTaskReadsTheFernetSecret"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.task_execution.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "custody" {
  description             = "espn-edge: encrypts the Fernet secret that encrypts ESPN sessions at rest (ADR-012)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.custody_key.json
}

# The alias is load-bearing: the deploy boundary's Allow for AWS-managed keys
# is scoped to alias/aws/* and its Deny on Decrypt is scoped to this name.
resource "aws_kms_alias" "custody" {
  name          = "alias/espn-edge/custody"
  target_key_id = aws_kms_key.custody.key_id
}

resource "aws_secretsmanager_secret" "fernet_key" {
  name                    = "espn-edge/fernet-key"
  kms_key_id              = aws_kms_key.custody.arn
  recovery_window_in_days = 7
  # The alias must exist before the secret: the boundary admits the deploy
  # role's encrypt-side calls on this key by alias, not by ARN.
  depends_on = [aws_kms_alias.custody]
}
