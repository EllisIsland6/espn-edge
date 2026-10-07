variable "region" {
  type    = string
  default = "us-east-1"
}

variable "domain" {
  description = "The registered domain, which is also the hosted zone name and the hostname the app is served on. The zone is looked up, never created: Route 53 registration created it."
  type        = string
  default     = "eaedgefinder.com"
}

variable "origin_hostname" {
  description = "The hostname CloudFront uses to reach the EC2 origin, and the name on the origin certificate Caddy obtains by DNS-01. A subdomain of `domain`."
  type        = string
  default     = "origin.eaedgefinder.com"
}

variable "operator_email" {
  description = "The one person this deployment signs in. Verified in SES for Cognito's mail, and the SNS alarm subscriber."
  type        = string
}

variable "instance_type" {
  description = "The selected host. docs/sprint-9/03-architecture.md prices t4g.small; a resize to t4g.medium is the one legitimate change and must update the bill first."
  type        = string
  default     = "t4g.small"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_backup_retention_days" {
  description = "RDS automated backups. 14 days is the selected RPO window; the architecture document's 1/10-budget column is where that gets weakened, explicitly."
  type        = number
  default     = 14
}

variable "metrics_namespace" {
  description = "The CloudWatch namespace the application's sink publishes under. Deployment fact, not catalog property -- see ops/render_alarms.py."
  type        = string
  default     = "EspnEdge"
}

variable "app_image_tag" {
  description = "Immutable image tag (a digest-pinned tag written by the release workflow). Never `latest`."
  type        = string
}

variable "caddy_image_tag" {
  type = string
}

variable "tenant_id" {
  description = "The single synthetic tenant the service binds. A fresh migrated database has exactly one, id 1 (docs/aws-deploy.md s4)."
  type        = number
  default     = 1
}

variable "season" {
  type    = number
  default = 2026
}
