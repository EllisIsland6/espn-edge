# Applied ONCE, by the operator, with their own credentials, before anything
# else exists. Local state: there is nothing to put remote state in yet, and
# this root changes rarely. Keep `terraform.tfstate` from this directory out
# of the repository (see .gitignore) and back it up with the account.

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "espn-edge"
      Stack     = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}
