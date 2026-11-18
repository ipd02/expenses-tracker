variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "alert_email" {
  type        = string
  description = "Address that receives budget alerts"
}

variable "monthly_budget_usd" {
  type    = number
  default = 5
}

variable "pull_schedule_enabled" {
  type        = bool
  default     = true
  description = "Set false to pause scheduled pulls (for example while rotating the SSM secrets)"
}

variable "pull_schedule_expression" {
  type        = string
  default     = "rate(8 hours)"
  description = "3 runs a day stays under the ~4 unattended refreshes per account per day"
}

variable "pull_lookback_days" {
  type    = number
  default = 14
}

variable "owner_email" {
  type        = string
  description = "Email of the single Cognito user (receives the invitation with a temporary password)"
}

variable "cors_origins" {
  type        = list(string)
  default     = ["http://localhost:5173"]
  description = "Browser origins allowed to call the API; phase 6 adds the CloudFront URL"
}

variable "passkey_enabled" {
  type        = bool
  default     = true
  description = "Passkey sign-in. On a brand-new pool it can only be enabled after the Cognito domain exists (see cognito.tf)"
}

variable "mfa_required" {
  type        = bool
  default     = true
  description = "Require MFA. On a brand-new pool see the bootstrap notes in cognito.tf"
}
