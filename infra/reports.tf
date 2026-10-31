# Weekly meetings report (Lab 5; was infra/reports.yml, stack peach-reports).
# EventBridge Scheduler (Monday 07:00 Europe/Kyiv) sends
# {"week":null,"source":"schedule"} to the report-requests queue (with a DLQ).
# The builder Lambda - the backend image, in the VPC next to RDS - builds the CSV
# and writes reports/<ISO-week>.csv to a private bucket through a free S3
# gateway endpoint. The bucket's ObjectCreated notification invokes the mailer
# Lambda (a small zip, outside the VPC), which emails the CSV through SES.
#
# In CloudFormation the bucket had to depend on the mailer's invoke permission,
# so the permission spelled the bucket ARN out by hand to avoid a cycle. Here the
# bucket, the permission and the notification are three resources, and plain
# references order them: bucket -> permission -> notification.

locals {
  reports_bucket = "${var.project_name}-reports-${local.account_id}-${var.region}"
  # Created by the old deploy script for the mailer zip; not managed here.
  artifact_bucket = "${var.project_name}-artifacts-${local.account_id}-${var.region}"

  # The names CloudFormation generated; renaming a role would replace it.
  builder_role_name   = "peach-reports-BuilderRole-6B4fySTdooIT"
  mailer_role_name    = "peach-reports-MailerRole-2FxdBfY48FVF"
  scheduler_role_name = "peach-reports-SchedulerRole-AkALDNhfrIE2"

  lambda_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The builder has no route to Secrets Manager without a NAT gateway or a paid
# endpoint, so the URL goes into its environment, read here from peach-backend's
# secret. It is therefore in the Terraform state, which HCP Terraform encrypts.
data "aws_secretsmanager_secret_version" "database_url" {
  secret_id = var.database_url_secret_name
}

# --- storage -----------------------------------------------------------------

# Private and encrypted; reports expire after 90 days. Holds every report sent:
# nothing here may force its replacement (see ADR 0008).
resource "aws_s3_bucket" "reports" {
  bucket = local.reports_bucket
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    id     = "expire-reports"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "reports" {
  bucket = aws_s3_bucket.reports.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.reports.arn, "${aws_s3_bucket.reports.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# The ObjectCreated notification invokes the mailer directly - no queue between.
resource "aws_s3_bucket_notification" "reports" {
  bucket = aws_s3_bucket.reports.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.mailer.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "reports/"
    filter_suffix       = ".csv"
  }

  # S3 checks the invoke permission when the notification is written.
  depends_on = [aws_lambda_permission.mailer_from_s3]
}

# Free. Lets the builder, which has no internet route, write to S3. Its policy
# admits only writes of reports into this bucket, so nothing else in the VPC
# gains S3 access through it.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.route_table_ids

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "ReportsWriteOnly"
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:PutObject", "s3:AbortMultipartUpload"]
      Resource  = "${aws_s3_bucket.reports.arn}/reports/*"
    }]
  })

  tags = local.tags
}

# --- queue -------------------------------------------------------------------

resource "aws_sqs_queue" "requests_dlq" {
  name                      = "${var.project_name}-report-requests-dlq"
  message_retention_seconds = 1209600 # 14 days, to inspect and redrive
  max_message_size          = 1048576 # SQS's default since 2025; the provider still assumes 256 KiB
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "requests" {
  name = "${var.project_name}-report-requests"
  # At least 6 x the builder's 60 s timeout, so a message is never handed to a
  # second builder while the first may still be working on it.
  visibility_timeout_seconds = 360
  message_retention_seconds  = 345600  # 4 days
  max_message_size           = 1048576 # SQS's default since 2025
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  # Three failed builds and the request moves to the DLQ.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.requests_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.tags
}

# --- builder -----------------------------------------------------------------

resource "aws_cloudwatch_log_group" "builder" {
  name              = "/aws/lambda/${var.project_name}-reports-builder"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "builder" {
  name               = local.builder_role_name
  assume_role_policy = local.lambda_trust
  tags               = local.tags
}

# Logs, plus the network interfaces a function in a VPC needs.
resource "aws_iam_role_policy_attachment" "builder_vpc" {
  role       = aws_iam_role.builder.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "builder" {
  name = "build-reports"
  role = aws_iam_role.builder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeRequests"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.requests.arn
      },
      {
        Sid      = "WriteReports"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.reports.arn}/reports/*"
      },
    ]
  })
}

resource "aws_lambda_function" "builder" {
  function_name = "${var.project_name}-reports-builder"
  description   = "Builds the weekly meetings CSV from RDS and writes it to S3"
  role          = aws_iam_role.builder.arn

  package_type = "Image"
  image_uri    = "${local.ecr_repository}:${var.builder_image_tag}"
  image_config {
    command = ["app.reports.lambda_handler.handler"]
  }

  architectures = [var.architecture]
  memory_size   = 512
  timeout       = 60

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.builder.name
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.function_security_group_id]
  }

  environment {
    variables = merge(
      {
        APP_ENV         = "production"
        DATABASE_URL    = data.aws_secretsmanager_secret_version.database_url.secret_string
        DB_POOLING      = "false"
        REPORT_TIMEZONE = var.report_timezone
        REPORTS_BUCKET  = aws_s3_bucket.reports.id
      },
      # TEST ONLY: present only during the DLQ redrive demonstration.
      var.simulate_builder_failure ? { REPORTS_SIMULATE_FAILURE = "true" } : {},
    )
  }

  tags = local.tags

  lifecycle {
    # The image is application code: the app pipeline deploys new ones.
    ignore_changes = [image_uri]
  }
}

# One message per invocation; a failure returns just that message to the
# queue. At most two builders at once, so the micro instance never sees a burst
# of connections. (Reserved concurrency is not possible: the account's limit
# is 10, all of which must stay unreserved.)
resource "aws_lambda_event_source_mapping" "builder" {
  event_source_arn = aws_sqs_queue.requests.arn
  function_name    = aws_lambda_function.builder.arn
  batch_size       = 1
  enabled          = true

  scaling_config {
    maximum_concurrency = 2
  }

  tags = local.tags
}

# --- mailer ------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "mailer" {
  name              = "/aws/lambda/${var.project_name}-reports-mailer"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "mailer" {
  name               = local.mailer_role_name
  assume_role_policy = local.lambda_trust
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "mailer_logs" {
  role       = aws_iam_role.mailer.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "mailer" {
  name = "mail-reports"
  role = aws_iam_role.mailer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Read only, and only reports - the mailer can never write one and
      # trigger itself.
      {
        Sid      = "ReadReports"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.reports.arn}/reports/*"
      },
      # Send only as the configured sender.
      {
        Sid       = "SendAsReportSender"
        Effect    = "Allow"
        Action    = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource  = "arn:${local.partition}:ses:${var.region}:${local.account_id}:identity/*"
        Condition = { StringEquals = { "ses:FromAddress" = var.report_sender } }
      },
    ]
  })
}

resource "aws_lambda_function" "mailer" {
  function_name = "${var.project_name}-reports-mailer"
  description   = "Emails each new reports/*.csv through SES"
  role          = aws_iam_role.mailer.arn

  # A zip of backend/app/reports/mailer.py alone - independent of the image.
  runtime   = "python3.14"
  handler   = "mailer.handler"
  s3_bucket = local.artifact_bucket
  s3_key    = var.mailer_code_key

  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 30

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.mailer.name
  }

  environment {
    variables = {
      REPORT_SENDER     = var.report_sender
      REPORT_RECIPIENTS = var.report_recipients
    }
  }

  tags = local.tags

  lifecycle {
    # The zip is application code, deployed outside Terraform; Lambda does not
    # report where it came from, so these would always look changed.
    ignore_changes = [s3_bucket, s3_key, s3_object_version]
  }
}

resource "aws_lambda_permission" "mailer_from_s3" {
  # The statement ID CloudFormation generated.
  statement_id   = "peach-reports-MailerInvokePermission-GKQ6WkXiBgKJ"
  function_name  = aws_lambda_function.mailer.function_name
  action         = "lambda:InvokeFunction"
  principal      = "s3.amazonaws.com"
  source_account = local.account_id
  source_arn     = aws_s3_bucket.reports.arn
}

# --- schedule ----------------------------------------------------------------

resource "aws_iam_role" "scheduler" {
  name = local.scheduler_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "send-report-requests"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.requests.arn
    }]
  })
}

# The schedule targets the queue, not the builder: a request survives a failed
# build, is retried, and ends in the DLQ if it keeps failing.
resource "aws_scheduler_schedule" "weekly" {
  name        = "${var.project_name}-weekly-report"
  description = "Monday 07:00 Kyiv - report the ISO week that just ended"

  schedule_expression          = "cron(0 7 ? * MON *)"
  schedule_expression_timezone = "Europe/Kyiv"
  state                        = var.schedule_state

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sqs_queue.requests.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{\"week\":null,\"source\":\"schedule\"}"

    retry_policy {
      maximum_retry_attempts = 3
    }
  }
}
