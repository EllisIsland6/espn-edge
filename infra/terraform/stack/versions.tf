# The selected public-only, synthetic-only topology from
# docs/sprint-9/03-architecture.md: one public-egress t4g.small with an EIP
# running ECS-on-EC2 (web + worker), a single-AZ RDS PostgreSQL 16 in a
# private subnet, S3/CloudFront in front, Route 53 + ACM, Cognito's hosted UI.
# No NAT gateway, no ALB, no interface endpoints, no WAF, no KMS custody key.
#
# STATUS: written and policy-tested offline. Never initialised, planned or
# applied -- the environments this was written in cannot reach the Terraform
# registry or AWS. The first `terraform plan` is the first real review.

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    # bucket / dynamodb_table are supplied at init from the bootstrap outputs:
    #   terraform init -backend-config="bucket=<state_bucket>" \
    #                  -backend-config="dynamodb_table=<lock_table>"
    key     = "stack/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "espn-edge"
      Stack     = "public-synthetic"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
