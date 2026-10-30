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
