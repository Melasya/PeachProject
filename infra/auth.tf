# Sign-in (Lab 4; was infra/auth.yaml, stack peach-auth): a Cognito user pool
# with self sign-up by email and password, Google federation, and managed login
# (version 2) on a prefix domain. The browser app is a public client using the
# authorization code flow with PKCE.
#
# Costs nothing at this size: Essentials is free up to 10,000 monthly users.
# The pool holds every account that signed up - nothing here may force its
# replacement (see ADR 0008).

locals {
  cognito_issuer = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-users"

  # Managed login needs Essentials.
  user_pool_tier = "ESSENTIALS"

  # People sign in with their email, not a separate username.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Self sign-up stays on, so anyone can create an account.
  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  deletion_protection = "INACTIVE"

  tags = local.tags
}

# Cognito federates to Google; the site itself never talks to Google.
resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  # The last six are Google's endpoints, which Cognito fills in by itself and
  # reports back; listing them keeps the plan quiet.
  provider_details = {
    client_id                     = var.google_client_id
    client_secret                 = var.google_client_secret
    authorize_scopes              = "openid email profile"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    authorize_url                 = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer                   = "https://accounts.google.com"
    token_request_method          = "POST"
    token_url                     = "https://www.googleapis.com/oauth2/v4/token"
  }

  # Without email a Google user arrives with nothing for the header to show.
  # username = sub is Cognito's own mapping for every federated provider.
  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    username       = "sub"
  }
}

# A browser cannot keep a secret, so the client has none; PKCE (done by
# oidc-client-ts) protects the code exchange instead.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.project_name}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  # No generate_secret: a client gets no secret unless asked for one, and
  # setting the argument on an imported client forces its replacement.
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  # Naming the provider by reference orders the client after it.
  supported_identity_providers = ["COGNITO", aws_cognito_identity_provider.google.provider_name]

  # Exact matches, trailing slash included: the app sends exactly these.
  callback_urls = ["${var.site_url}/login/", "${var.local_url}/login/"]
  logout_urls   = ["${var.site_url}/", "${var.local_url}/"]

  explicit_auth_flows           = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
}

# Without a branding style a version 2 domain has no sign-in page to show.
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.main.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true

  depends_on = [aws_cognito_user_pool_domain.main]
}
