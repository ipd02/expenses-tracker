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

output "pull_function_name" {
  value = aws_lambda_function.pull.function_name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "consent_redirect_url" {
  value       = "${aws_apigatewayv2_api.consent.api_endpoint}/callback"
  description = "Register this exact URL as the redirect URL in the Enable Banking Control Panel"
}

output "consent_function_name" {
  value = aws_lambda_function.consent.function_name
}
