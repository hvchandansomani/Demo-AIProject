terraform {
  # 1.11+: S3-native state locking (use_lockfile) is generally available.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets these tags: easy to find, and easy to see the cost of.
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Stack     = "infra"
    }
  }
}
