output "site_url" {
  value = "https://${var.domain}"
}

output "origin_hostname" {
  value = var.origin_hostname
}

output "host_eip" {
  value = aws_eip.host.public_ip
}

output "database_endpoint" {
  value = aws_db_instance.main.address
}

output "database_master_secret_arn" {
  description = "The RDS-managed owner password. Read it with the AWS CLI to create the application role (docs/aws-deploy.md s2); never paste it anywhere."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "ecr_app" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_caddy" {
  value = aws_ecr_repository.caddy.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.arn
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "oidc_issuer" {
  value = local.oidc_issuer
}

output "cognito_hosted_ui" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "spa_bucket" {
  value = aws_s3_bucket.spa.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "custody" {
  value = "public_synthetic; no FERNET_KEY, no ESPN credential, no raw cache, no private stack. docs/aws-deploy.md s0a."
}
