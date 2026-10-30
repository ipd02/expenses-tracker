output "table_name" {
  value = aws_dynamodb_table.main.name
}

output "parameter_names" {
  value = { for k, p in aws_ssm_parameter.secret : k => p.name }
}

output "role_arns" {
  value = {
    pull = module.pull.role_arn
  }
}

output "log_group_names" {
  value = {
    pull = module.pull.log_group_name
  }
}
