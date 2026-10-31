# Non-secret values are in terraform.tfvars. Secrets and personal data are
# workspace variables in HCP Terraform, never in a file: google_client_secret
# (sensitive), report_sender and report_recipients.

variable "project_name" {
  type    = string
  default = "peach"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

# --- sign-in (Lab 4) ---------------------------------------------------------

variable "cognito_domain_prefix" {
  type        = string
  description = "Managed login lives at https://<prefix>.auth.<region>.amazoncognito.com."

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", var.cognito_domain_prefix))
    error_message = "Lower-case letters, digits and hyphens, 1-63 characters."
  }
}

variable "site_url" {
  type        = string
  description = "The deployed frontend's origin, with no trailing slash."

  validation {
    condition     = can(regex("^https://[^/]+$", var.site_url))
    error_message = "An https:// origin with no path or trailing slash."
  }
}

variable "local_url" {
  type    = string
  default = "http://localhost:3000"
}

variable "google_client_id" {
  type        = string
  description = "Client ID of the Google OAuth web client. Not a secret."
}

variable "google_client_secret" {
  type        = string
  sensitive   = true
  description = "Google OAuth client secret. A sensitive workspace variable, never in a file."
}

# --- weekly report (Lab 5) ---------------------------------------------------

variable "vpc_id" {
  type        = string
  description = "The backend's VPC, where RDS lives."
}

variable "subnet_ids" {
  type        = list(string)
  description = "The backend function's subnets; the builder runs in the same ones."
}

variable "route_table_ids" {
  type        = list(string)
  description = "Route tables of those subnets, for the S3 gateway endpoint."
}

variable "function_security_group_id" {
  type        = string
  description = "The backend function's security group; RDS already admits it on 5432."
}

variable "database_url_secret_name" {
  type        = string
  default     = "peach/backend/database-url"
  description = "Secrets Manager secret with the backend's SQLAlchemy URL (owned by peach-backend)."
}

variable "builder_image_tag" {
  type        = string
  description = "peach-backend image tag the builder was created with. Later images are deployed by the app pipeline."
}

variable "report_timezone" {
  type    = string
  default = "Europe/Kyiv"
}

variable "report_sender" {
  type        = string
  description = "Verified SES identity the report is sent from. A workspace variable."
}

variable "report_recipients" {
  type        = string
  description = "Comma-separated recipients (verified while SES is in the sandbox). A workspace variable."
}

variable "mailer_code_key" {
  type        = string
  description = "Key of the mailer zip in the artifact bucket the function was created from."
}

variable "schedule_state" {
  type    = string
  default = "ENABLED"

  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.schedule_state)
    error_message = "ENABLED or DISABLED."
  }
}

variable "simulate_builder_failure" {
  type        = bool
  default     = false
  description = "TEST ONLY - the DLQ redrive demonstration: every valid request fails before it touches RDS or S3."
}

# --- agenda parser (Lab 6) ---------------------------------------------------

variable "parser_image_tag" {
  type        = string
  description = "peach-backend image tag the parser was created with. Later images are deployed by the app pipeline."
}

variable "parser_model" {
  type    = string
  default = "gemini-3.5-flash-lite"

  validation {
    condition     = can(regex("^gemini-[a-z0-9.-]+$", var.parser_model))
    error_message = "A Gemini model code, for example gemini-3.5-flash-lite."
  }
}

variable "parser_effort" {
  type    = string
  default = "low"

  validation {
    condition     = contains(["low", "medium", "high"], var.parser_effort)
    error_message = "low, medium or high."
  }
}

variable "gemini_api_key_parameter" {
  type        = string
  default     = "/peach/gemini-api-key"
  description = "Name (not value) of the SSM SecureString holding the Gemini API key. Created by hand, not by Terraform."
}

# --- shared --------------------------------------------------------------------

variable "architecture" {
  type    = string
  default = "arm64"
}

variable "log_retention_days" {
  type    = number
  default = 14
}
