output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "plan_role_arn" {
  description = "Put this in the repository's Actions variables as AWS_PLAN_ROLE_ARN. It is not a secret: it can be assumed only by this repository's own workflows."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "AWS_DEPLOY_ROLE_ARN, likewise. Assumable only from the protected `production` environment."
  value       = aws_iam_role.deploy.arn
}
