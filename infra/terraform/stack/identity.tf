# Cognito Lite: one pool, invitation-only, email sign-in, TOTP available, a
# hosted UI on a Cognito-provided prefix, and one app client for the
# application's own OIDC code flow (api/oidc.py). There is no ALB and so no
# ALB authentication action; the application's front door is the gate.

resource "aws_cognito_user_pool" "main" {
  name = "espn-edge"

  # Invitation only. Self sign-up on a synthetic deployment is a list of
  # strangers with accounts; the architecture's "1-50 synthetic users" are
  # people the operator invited.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Cognito's own mail path: 50 messages/day, no SES configuration to get
  # wrong, enough for one operator and a handful of invitations. The SES
  # identity below is verified so this can be switched to DEVELOPER mode
  # without a second migration, which is the whole of what 42 asks of SES.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  deletion_protection = "ACTIVE"
}

resource "aws_cognito_user_pool_client" "app" {
  name         = "espn-edge-app"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = true

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = ["https://${var.domain}/api/auth/callback"]
  logout_urls   = ["https://${var.domain}/"]

  # Short tokens; the application mints its own 12-hour session and never
  # stores these.
  id_token_validity      = 15
  access_token_validity  = 15
  refresh_token_validity = 1
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "espn-edge-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

# The operator's address, verified so SES can be used for Cognito or
# alarms later; nothing sends through it yet.
resource "aws_ses_email_identity" "operator" {
  email = var.operator_email
}

locals {
  oidc_issuer = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}
