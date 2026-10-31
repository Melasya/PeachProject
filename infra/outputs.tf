# Everything the Makefile, the deploy scripts and the frontend build read
# (terraform -chdir=infra output -raw <name>). All public values.

# --- sign-in -------------------------------------------------------------------

output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "The public web client - NEXT_PUBLIC_COGNITO_CLIENT_ID."
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_authority" {
  description = "OIDC issuer - NEXT_PUBLIC_COGNITO_AUTHORITY."
  value       = local.cognito_issuer
}

output "cognito_domain" {
  description = "Managed login and /logout - NEXT_PUBLIC_COGNITO_DOMAIN."
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "login_page_url" {
  value = "${var.site_url}/login/"
}

# --- weekly report -------------------------------------------------------------

output "reports_bucket" {
  value = aws_s3_bucket.reports.id
}

output "reports_queue_url" {
  value = aws_sqs_queue.requests.url
}

output "reports_queue_arn" {
  value = aws_sqs_queue.requests.arn
}

output "reports_dlq_url" {
  value = aws_sqs_queue.requests_dlq.url
}

output "reports_builder_function" {
  value = aws_lambda_function.builder.function_name
}

output "reports_mailer_function" {
  value = aws_lambda_function.mailer.function_name
}

output "reports_schedule_name" {
  value = aws_scheduler_schedule.weekly.name
}

output "reports_scheduler_role_arn" {
  description = "Used by scripts/reports.sh schedule-once for the end-to-end test."
  value       = aws_iam_role.scheduler.arn
}

output "reports_builder_log_group" {
  value = aws_cloudwatch_log_group.builder.name
}

output "reports_mailer_log_group" {
  value = aws_cloudwatch_log_group.mailer.name
}

output "reports_simulate_failure" {
  description = "TEST ONLY. Must be false outside the DLQ redrive demonstration."
  value       = var.simulate_builder_failure
}

# --- agenda parser -------------------------------------------------------------

output "parser_url" {
  description = "Base URL of the agenda parser - NEXT_PUBLIC_PARSER_URL."
  value       = aws_lambda_function_url.parser.function_url
}

output "parser_function" {
  value = aws_lambda_function.parser.function_name
}

output "parser_log_group" {
  value = aws_cloudwatch_log_group.parser.name
}

output "parser_model" {
  value = var.parser_model
}
