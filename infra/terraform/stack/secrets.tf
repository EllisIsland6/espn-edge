# Secrets Manager holds what the tasks read at start. Nothing here is a real
# ESPN credential or a custody key: there is no FERNET_KEY in a synthetic
# deployment (docs/aws-deploy.md s0a), and the RDS master password is managed
# by RDS itself -- and read where it lives: the migrate task takes it as one
# JSON key of the RDS-managed secret (containers.tf, WU-9). A hand-written
# copy of it, `espn-edge/database-url-owner`, lived here from 2026-10-08 to
# WU-9 and was stale by its second week, since RDS rotates the password every
# seven days and nothing refreshed the copy; the first migration after a
# rotation was refused (2026-10-27). Its 7-day recovery window is the undo.
# Values here are generated or written by the operator after apply; none
# passes through a terminal or a repository.

# The application role's connection string. The role and its password are
# created by the operator as the owner (docs/aws-deploy.md s2) and the URL
# written into this secret by hand -- a password is not something to plan.
# This one is static (edge_app is not RDS-managed), so a copy is not stale.
resource "aws_secretsmanager_secret" "database_url_app" {
  name                    = "espn-edge/database-url-app"
  recovery_window_in_days = 7
}

resource "random_password" "session_secret" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "session_secret" {
  name                    = "espn-edge/session-secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "session_secret" {
  secret_id     = aws_secretsmanager_secret.session_secret.id
  secret_string = random_password.session_secret.result
}

# Optional. Empty disables the AI features cleanly; the $5 ledger is the
# application's own (DELIBERATELY GLOBAL -- see the spend tests).
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "espn-edge/anthropic-api-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "oidc_client_secret" {
  name                    = "espn-edge/oidc-client-secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "oidc_client_secret" {
  secret_id     = aws_secretsmanager_secret.oidc_client_secret.id
  secret_string = aws_cognito_user_pool_client.app.client_secret
}

resource "aws_secretsmanager_secret" "origin_verify" {
  name                    = "espn-edge/origin-verify"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "origin_verify" {
  secret_id     = aws_secretsmanager_secret.origin_verify.id
  secret_string = random_password.origin_verify.result
}
