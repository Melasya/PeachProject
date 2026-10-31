# Bootstrap: what HCP Terraform needs before it can touch AWS at all - an IAM
# OIDC provider that trusts app.terraform.io, and two roles a run may assume.
# Applied once from a laptop with your own credentials; its state is local and
# gitignored (it holds no secrets). Everything else lives in infra/.
#
# Narrower than the lab's single AdministratorAccess role (ADR 0008):
# - each role trusts one workspace and one run phase only;
# - the plan role can read, and read the one database secret infra/ needs;
# - the apply role can additionally write, but only peach-* resources of the
#   kinds infra/ manages.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

variable "tfc_organization" {
  type        = string
  description = "HCP Terraform organization that owns the workspace."
}

variable "tfc_workspace" {
  type    = string
  default = "peach-dev"
}

variable "project_name" {
  type    = string
  default = "peach"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      PROJECT_NAME = var.project_name
      ManagedBy    = "terraform-bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  name    = "hcp-terraform-${var.project_name}"
  prefix  = var.project_name
}

data "tls_certificate" "tfc" {
  url = "https://app.terraform.io"
}

resource "aws_iam_openid_connect_provider" "tfc" {
  url             = "https://app.terraform.io"
  client_id_list  = ["aws.workload.identity"]
  thumbprint_list = [data.tls_certificate.tfc.certificates[0].sha1_fingerprint]
}

# One trust policy per run phase: a token for a plan cannot assume the apply role.
data "aws_iam_policy_document" "trust" {
  for_each = toset(["plan", "apply"])

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.tfc.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "app.terraform.io:aud"
      values   = ["aws.workload.identity"]
    }

    condition {
      test     = "StringLike"
      variable = "app.terraform.io:sub"
      values   = ["organization:${var.tfc_organization}:project:*:workspace:${var.tfc_workspace}:run_phase:${each.key}"]
    }
  }
}

resource "aws_iam_role" "tfc" {
  for_each = data.aws_iam_policy_document.trust

  name                 = "${local.name}-${each.key}"
  assume_role_policy   = each.value.json
  max_session_duration = 3600
}

# Both phases read everything they refresh. ReadOnlyAccess does not include
# secret values; the one secret infra/ reads is granted below.
resource "aws_iam_role_policy_attachment" "read" {
  for_each = aws_iam_role.tfc

  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "database_secret" {
  statement {
    sid       = "ReadDatabaseUrl"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:${local.account}:secret:${local.prefix}/backend/database-url-*"]
  }
}

resource "aws_iam_role_policy" "database_secret" {
  for_each = aws_iam_role.tfc

  name   = "read-database-url"
  role   = each.value.name
  policy = data.aws_iam_policy_document.database_secret.json
}

# Writes for apply only, limited to peach-* names and the services in infra/.
data "aws_iam_policy_document" "write" {
  statement {
    sid       = "Cognito"
    actions   = ["cognito-idp:*"]
    resources = ["arn:aws:cognito-idp:${var.region}:${local.account}:userpool/*"]
  }

  statement {
    sid       = "CognitoDomains"
    actions   = ["cognito-idp:CreateUserPoolDomain", "cognito-idp:UpdateUserPoolDomain", "cognito-idp:DeleteUserPoolDomain", "cognito-idp:DescribeUserPoolDomain"]
    resources = ["*"]
  }

  statement {
    sid       = "Functions"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.region}:${local.account}:function:${local.prefix}-*"]
  }

  statement {
    sid = "EventSourceMappings"
    actions = [
      "lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping",
      "lambda:GetEventSourceMapping", "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${var.region}:${local.account}:event-source-mapping:*"]
  }

  statement {
    sid = "ProjectRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:TagRole", "iam:UntagRole",
    ]
    resources = ["arn:aws:iam::${local.account}:role/${local.prefix}-*"]
  }

  statement {
    sid       = "PassProjectRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/${local.prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReportsBucket"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${local.prefix}-reports-${local.account}-${var.region}"]
  }

  statement {
    sid       = "MailerCode"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.prefix}-artifacts-${local.account}-${var.region}/mailer/*"]
  }

  statement {
    sid       = "Queues"
    actions   = ["sqs:*"]
    resources = ["arn:aws:sqs:${var.region}:${local.account}:${local.prefix}-*"]
  }

  statement {
    sid       = "LogGroups"
    actions   = ["logs:*"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/${local.prefix}-*"]
  }

  statement {
    sid       = "S3GatewayEndpoint"
    actions   = ["ec2:CreateVpcEndpoint", "ec2:ModifyVpcEndpoint", "ec2:DeleteVpcEndpoints", "ec2:CreateTags", "ec2:DeleteTags"]
    resources = ["*"]
  }

  statement {
    sid       = "Schedule"
    actions   = ["scheduler:*"]
    resources = ["arn:aws:scheduler:${var.region}:${local.account}:schedule/default/${local.prefix}-*"]
  }
}

resource "aws_iam_role_policy" "write" {
  name   = "write-peach-infra"
  role   = aws_iam_role.tfc["apply"].name
  policy = data.aws_iam_policy_document.write.json
}

output "plan_role_arn" {
  description = "TFC_AWS_PLAN_ROLE_ARN in the workspace."
  value       = aws_iam_role.tfc["plan"].arn
}

output "apply_role_arn" {
  description = "TFC_AWS_APPLY_ROLE_ARN in the workspace."
  value       = aws_iam_role.tfc["apply"].arn
}
