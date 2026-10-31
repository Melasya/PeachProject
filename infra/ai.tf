# Agenda parser (Lab 6; was infra/ai.yml, stack peach-ai): a Lambda (the
# backend image, handler app.ai.lambda_handler.handler) that turns pasted agenda
# text into suggested meeting fields with Google's Gemini API. It runs outside
# the VPC - it needs the Gemini API, SSM and Cognito's signing keys, never the
# database - and answers on its own function URL. The function verifies the
# Cognito access token itself, so the URL's auth type is NONE.
#
# The Gemini API key is an SSM SecureString created by hand. Terraform knows
# only its name; the key never reaches a .tf file, a variable or the state.

locals {
  parser_name = "${var.project_name}-agenda-parser"
}

resource "aws_cloudwatch_log_group" "parser" {
  name              = "/aws/lambda/${local.parser_name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "parser" {
  # The name CloudFormation generated; renaming would replace the role.
  name = "peach-ai-ParserRole-HYgBmQSSuCPw"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

# Logs only - no VPC access, no database.
resource "aws_iam_role_policy_attachment" "parser_logs" {
  role       = aws_iam_role.parser.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# This one parameter only. It is encrypted with the AWS-managed aws/ssm key,
# whose key policy already lets SSM decrypt for callers allowed to read it.
resource "aws_iam_role_policy" "parser_read_key" {
  name = "read-gemini-api-key"
  role = aws_iam_role.parser.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadGeminiApiKey"
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${var.gemini_api_key_parameter}"
    }]
  })
}

resource "aws_lambda_function" "parser" {
  function_name = local.parser_name
  description   = "Suggests meeting fields from pasted agenda text (Gemini API)"
  role          = aws_iam_role.parser.arn

  package_type = "Image"
  image_uri    = "${local.ecr_repository}:${var.parser_image_tag}"
  image_config {
    command = ["app.ai.lambda_handler.handler"]
  }

  architectures = [var.architecture]
  memory_size   = 512
  # Up to two model calls (one validation retry), each bounded by the SDK timeout.
  timeout = 120

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.parser.name
  }

  # No vpc_config on purpose: outside the VPC it reaches the Gemini API, SSM
  # and Cognito directly, and it has no route to RDS at all.
  environment {
    variables = {
      PARSER_MODEL  = var.parser_model
      PARSER_EFFORT = var.parser_effort
      # The parameter's name; the key itself is read from SSM at run time.
      GEMINI_API_KEY_PARAMETER = var.gemini_api_key_parameter
      PARSER_TIMEZONE          = "Europe/Kyiv"
      COGNITO_ISSUER           = local.cognito_issuer
      COGNITO_CLIENT_ID        = aws_cognito_user_pool_client.web.id
    }
  }

  tags = local.tags

  lifecycle {
    # The image is application code: the app pipeline deploys new ones.
    ignore_changes = [image_uri]
  }
}

# CORS is answered here, not in the handler, so headers are never doubled.
resource "aws_lambda_function_url" "parser" {
  function_name      = aws_lambda_function.parser.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = [var.site_url, var.local_url]
    allow_methods = ["POST"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}

# A public function URL needs both grants, as on the API function. The
# statement IDs are the ones CloudFormation generated.
resource "aws_lambda_permission" "parser_url" {
  statement_id           = "peach-ai-ParserUrlPermission-Fs1jfyZYVfzg"
  function_name          = aws_lambda_function.parser.function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "parser_url_invoke" {
  statement_id             = "peach-ai-ParserUrlInvokePermission-bwkxZvjcU95J"
  function_name            = aws_lambda_function.parser.function_name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}
