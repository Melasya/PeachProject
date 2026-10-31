# Adopts the resources the three CloudFormation stacks created, with their real
# IDs (read from the stacks before they were deleted with DeletionPolicy: Retain).
# Delete this file after the import has been applied.

locals {
  import_queue_base = "https://sqs.${var.region}.amazonaws.com/${local.account_id}"
}

# --- sign-in (peach-auth) ------------------------------------------------------

import {
  to = aws_cognito_user_pool.main
  id = "us-east-1_PKwFocCA3"
}

import {
  to = aws_cognito_identity_provider.google
  id = "us-east-1_PKwFocCA3:Google"
}

import {
  to = aws_cognito_user_pool_client.web
  id = "us-east-1_PKwFocCA3/2jto8b9u9t71mbg58udp1mkett"
}

import {
  to = aws_cognito_user_pool_domain.main
  id = "peach-melasya"
}

import {
  to = aws_cognito_managed_login_branding.web
  id = "us-east-1_PKwFocCA3,017c5326-0c18-4f67-83ab-ca95b5c59734"
}

# --- weekly report (peach-reports) ---------------------------------------------

import {
  to = aws_s3_bucket.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_public_access_block.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_ownership_controls.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_lifecycle_configuration.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_policy.reports
  id = local.reports_bucket
}

import {
  to = aws_s3_bucket_notification.reports
  id = local.reports_bucket
}

import {
  to = aws_vpc_endpoint.s3
  id = "vpce-04f3a333d500092a0"
}

import {
  to = aws_sqs_queue.requests_dlq
  id = "${local.import_queue_base}/peach-report-requests-dlq"
}

import {
  to = aws_sqs_queue.requests
  id = "${local.import_queue_base}/peach-report-requests"
}

import {
  to = aws_cloudwatch_log_group.builder
  id = "/aws/lambda/peach-reports-builder"
}

import {
  to = aws_iam_role.builder
  id = "peach-reports-BuilderRole-6B4fySTdooIT"
}

import {
  to = aws_iam_role_policy_attachment.builder_vpc
  id = "peach-reports-BuilderRole-6B4fySTdooIT/arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

import {
  to = aws_iam_role_policy.builder
  id = "peach-reports-BuilderRole-6B4fySTdooIT:build-reports"
}

import {
  to = aws_lambda_function.builder
  id = "peach-reports-builder"
}

import {
  to = aws_lambda_event_source_mapping.builder
  id = "67855843-f350-4e0e-acc4-762daaeb4b30"
}

import {
  to = aws_cloudwatch_log_group.mailer
  id = "/aws/lambda/peach-reports-mailer"
}

import {
  to = aws_iam_role.mailer
  id = "peach-reports-MailerRole-2FxdBfY48FVF"
}

import {
  to = aws_iam_role_policy_attachment.mailer_logs
  id = "peach-reports-MailerRole-2FxdBfY48FVF/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

import {
  to = aws_iam_role_policy.mailer
  id = "peach-reports-MailerRole-2FxdBfY48FVF:mail-reports"
}

import {
  to = aws_lambda_function.mailer
  id = "peach-reports-mailer"
}

import {
  to = aws_lambda_permission.mailer_from_s3
  id = "peach-reports-mailer/peach-reports-MailerInvokePermission-GKQ6WkXiBgKJ"
}

import {
  to = aws_iam_role.scheduler
  id = "peach-reports-SchedulerRole-AkALDNhfrIE2"
}

import {
  to = aws_iam_role_policy.scheduler
  id = "peach-reports-SchedulerRole-AkALDNhfrIE2:send-report-requests"
}

import {
  to = aws_scheduler_schedule.weekly
  id = "default/peach-weekly-report"
}

# --- agenda parser (peach-ai) --------------------------------------------------

import {
  to = aws_cloudwatch_log_group.parser
  id = "/aws/lambda/peach-agenda-parser"
}

import {
  to = aws_iam_role.parser
  id = "peach-ai-ParserRole-HYgBmQSSuCPw"
}

import {
  to = aws_iam_role_policy_attachment.parser_logs
  id = "peach-ai-ParserRole-HYgBmQSSuCPw/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

import {
  to = aws_iam_role_policy.parser_read_key
  id = "peach-ai-ParserRole-HYgBmQSSuCPw:read-gemini-api-key"
}

import {
  to = aws_lambda_function.parser
  id = "peach-agenda-parser"
}

import {
  to = aws_lambda_function_url.parser
  id = "peach-agenda-parser"
}

import {
  to = aws_lambda_permission.parser_url
  id = "peach-agenda-parser/peach-ai-ParserUrlPermission-Fs1jfyZYVfzg"
}

import {
  to = aws_lambda_permission.parser_url_invoke
  id = "peach-agenda-parser/peach-ai-ParserUrlInvokePermission-bwkxZvjcU95J"
}
