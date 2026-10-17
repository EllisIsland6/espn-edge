# ADR-012 -- the custody key, and the Fernet secret it encrypts.
#
# ESPN sessions are stored as Fernet ciphertext; FERNET_KEY is what unlocks
# them. In the synthetic deployment no such key existed (that was the custody
# boundary). For the invited group the key lives in a Secrets Manager secret
# encrypted with the one customer-managed KMS key the stack creates. The
# key's own policy names the reader -- the web task's execution role, through
# Secrets Manager only -- and the deploy role that creates the key can
# administer it and never use it: no Encrypt, no Decrypt, no data key, no
# grant, by alias (see bootstrap/main.tf).
#
# The SECRET is the operator's, not Terraform's. Secrets Manager validates a
# new secret's key by calling GenerateDataKey and Decrypt with it on the
# caller's behalf (its documentation names them: SecretVersionId
# "RequestToValidateKeyAccess"), and the deploy role may never decrypt with
# this key -- so the deploy role cannot create this secret, and should not.
# The operator creates it, with its value, in his terminal (README section
# 6), and the stack reads its ARN once `wire_fernet_key` says it exists. The
# value is in no plan, no state, no log.

data "aws_iam_policy_document" "custody_key" {
  # The lock-out guard every key policy carries, and what lets IAM policies
  # in this account govern the key. The account's administrators -- the
  # operator -- can therefore reach it; ADR-006 already names that as the
  # custody trust. The deploy role's IAM policy allows no use of this alias
  # and denies it outright, so this statement does not reach it.
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
# is scoped to alias/aws/* and its Deny on using this key is scoped to this
# name.
resource "aws_kms_alias" "custody" {
  name          = "alias/espn-edge/custody"
  target_key_id = aws_kms_key.custody.key_id
}

# Read, never created, here: see the header. `count` is the wiring gate --
# with it off the data source does not exist, so a plan made before the
# operator has created the secret does not fail on a missing name.
data "aws_secretsmanager_secret" "fernet_key" {
  count = var.wire_fernet_key ? 1 : 0
  name  = "espn-edge/fernet-key"
}
