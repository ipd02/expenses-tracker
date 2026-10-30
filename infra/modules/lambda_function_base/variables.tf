variable "name" {
  type        = string
  description = "Function name suffix, e.g. pull"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "policy_statements" {
  type = list(object({
    sid       = string
    actions   = list(string)
    resources = list(string)
  }))
  description = "Extra least-privilege statements; logging to the function's own log group is added automatically"
}
