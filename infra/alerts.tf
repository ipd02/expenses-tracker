# Alerts by email. The subscription must be confirmed from the email AWS sends.
resource "aws_sns_topic" "alerts" {
  name = "expenses-tracker-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# The handler logs the event `consent_invalid` (at ERROR) when the bank consent needs renewing.
resource "aws_cloudwatch_log_metric_filter" "consent_invalid" {
  name           = "expenses-tracker-consent-invalid"
  log_group_name = module.pull.log_group_name
  pattern        = "\"consent_invalid\""

  metric_transformation {
    name          = "ConsentInvalid"
    namespace     = "ExpensesTracker"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "consent_invalid" {
  alarm_name          = "expenses-tracker-consent-invalid"
  alarm_description   = "Bank consent expired or revoked: reconnect the bank. Pulls stop until then."
  namespace           = "ExpensesTracker"
  metric_name         = "ConsentInvalid"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "pull_errors" {
  alarm_name          = "expenses-tracker-pull-errors"
  alarm_description   = "The pull Lambda raised an unexpected error"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.pull.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
