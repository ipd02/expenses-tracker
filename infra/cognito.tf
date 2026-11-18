# Authentication for the owner's API. One user, no self-signup, passkey or password plus TOTP.
# The sign-in page is Cognito's managed login on a free prefix domain, which is also the
# passkey's relying party ID (DECISIONS 17). Essentials tier: free for the first 10,000 users.
data "aws_caller_identity" "current" {}

locals {
  # Globally unique, derived from the account id without committing the id.
  cognito_domain_prefix = "expenses-tracker-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
  cognito_domain        = "${local.cognito_domain_prefix}.auth.${var.region}.amazoncognito.com"
}

# Bootstrap notes (learned on the first apply, 2026-11-18), only needed to recreate the pool:
#   1. apply with -var passkey_enabled=false -var mfa_required=false (domain must exist first);
#   2. apply with -var mfa_required=false to configure the passkey;
#   3. the provider cannot set the passkey FactorConfiguration, and Cognito refuses required MFA
#      while it is SINGLE_FACTOR, so run once:
#        aws cognito-idp set-user-pool-mfa-config --user-pool-id <id> --mfa-configuration ON \
#          --software-token-mfa-configuration Enabled=true \
#          --web-authn-configuration RelyingPartyId=<domain>,UserVerification=required,FactorConfiguration=MULTI_FACTOR_WITH_USER_VERIFICATION
#   4. apply with the defaults. A later change to the MFA or passkey settings in Terraform would
#      reset FactorConfiguration; repeat step 3 if that happens.
resource "aws_cognito_user_pool" "main" {
  name                = "expenses-tracker"
  deletion_protection = "ACTIVE"
  user_pool_tier      = "ESSENTIALS"
  mfa_configuration   = var.mfa_required ? "ON" : "OPTIONAL" # ON must be applied after the passkey configuration exists

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  software_token_mfa_configuration {
    enabled = true
  }

  sign_in_policy {
    allowed_first_auth_factors = var.passkey_enabled ? ["PASSWORD", "WEB_AUTHN"] : ["PASSWORD"]
  }

  # Cognito only accepts the prefix domain as relying party ID once the domain exists, and the
  # domain needs the pool: create everything with passkey_enabled = false, then apply with true.
  dynamic "web_authn_configuration" {
    for_each = var.passkey_enabled ? [1] : []

    content {
      relying_party_id  = local.cognito_domain
      user_verification = "required"
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = local.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "expenses-tracker-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = false # public client: the browser app uses code + PKCE
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["http://localhost:5173/"]
  logout_urls                          = ["http://localhost:5173/"]

  explicit_auth_flows = ["ALLOW_USER_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 30
  id_token_validity      = 30
  refresh_token_validity = 7
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_managed_login_branding" "web" {
  client_id                   = aws_cognito_user_pool_client.web.id
  user_pool_id                = aws_cognito_user_pool.main.id
  use_cognito_provided_values = true

  depends_on = [aws_cognito_user_pool_domain.main]
}

# The single owner. Cognito emails an invitation with a temporary password (not stored in state).
resource "aws_cognito_user" "owner" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = var.owner_email

  attributes = {
    email          = var.owner_email
    email_verified = "true"
  }

  desired_delivery_mediums = ["EMAIL"]
}
