variable "region" {
  description = "Region for every resource. The architecture document selects us-east-1, which is also where CloudFront requires its certificate."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository whose Actions may assume the plan and deploy roles. Nothing else can."
  type        = string
  default     = "EllisIsland6/espn-edge"
}

variable "budget_alert_email" {
  description = "Where the budget alarms go. Required: a budget nobody is told about is decoration."
  type        = string
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform state of the stack root."
  type        = string
}

variable "monthly_budget_usd" {
  description = "The accepted operating envelope. docs/sprint-9/03-architecture.md: $34.26 expected, $51.76 envelope. Alerts fire at 80% actual and 100% forecast."
  type        = number
  default     = 52
}

variable "create_github_oidc_provider" {
  description = "An account holds at most one GitHub OIDC provider. true creates it; false looks up the existing one."
  type        = bool
  default     = true
}
