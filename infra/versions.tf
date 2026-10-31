# Terraform for the parts of Peach that Labs 4-6 built: sign-in (auth.tf), the
# weekly report (reports.tf) and the agenda parser (ai.tf). The older stacks -
# peach-backend, peach-frontend, peach-github-oidc - stay CloudFormation.
#
# State lives in HCP Terraform. Plans and applies run there with dynamic AWS
# credentials (OIDC, no stored keys); see infra/bootstrap/ and ADR 0008.
terraform {
  required_version = ">= 1.10"

  cloud {
    organization = "ooad-melasya"

    workspaces {
      name = "peach-dev"
    }
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # PROJECT_NAME on everything that takes tags (the Lab 4 rule), as the
  # CloudFormation templates set it.
  tags = { PROJECT_NAME = var.project_name }

  ecr_repository = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com/${var.project_name}-backend"
}
