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
