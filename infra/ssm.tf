# Placeholders only. Terraform creates the parameters; the owner loads the real
# values with `aws ssm put-parameter --overwrite` and Terraform never sees them.
locals {
  secret_parameters = {
    app_id  = "/expenses-tracker/enablebanking/app-id"
    privkey = "/expenses-tracker/enablebanking/private-key"
    session = "/expenses-tracker/enablebanking/session"
  }
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secret_parameters

  name  = each.value
  type  = "SecureString"
  tier  = "Standard"
  value = "placeholder-load-real-value-with-cli"

  lifecycle {
    ignore_changes = [value]
  }
}
