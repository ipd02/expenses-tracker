# Scheduled pull: EventBridge -> Lambda. The role and log group come from module.pull (iam.tf).
# Code is built into build/pull by scripts/build_lambda.sh (run by scripts/tf.sh).
data "archive_file" "pull" {
  type        = "zip"
  source_dir  = "${path.module}/../build/pull"
  output_path = "${path.module}/../build/pull.zip"
}

resource "aws_lambda_function" "pull" {
  function_name = "expenses-tracker-pull"
  role          = module.pull.role_arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "adapters.handlers.pull.handler"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.pull.output_path
  source_code_hash = data.archive_file.pull.output_base64sha256

  environment {
    variables = {
      TABLE_NAME      = aws_dynamodb_table.main.name
      SSM_APP_ID      = local.secret_parameters.app_id
      SSM_PRIVATE_KEY = local.secret_parameters.privkey
      SSM_SESSION     = local.secret_parameters.session
      LOOKBACK_DAYS   = tostring(var.pull_lookback_days)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = module.pull.log_group_name
  }

  # The role's inline policy must exist before the first invocation.
  depends_on = [module.pull]
}

# A failed run is never retried by Lambda: the next schedule tick is the retry.
resource "aws_lambda_function_event_invoke_config" "pull" {
  function_name          = aws_lambda_function.pull.function_name
  maximum_retry_attempts = 0
}

resource "aws_cloudwatch_event_rule" "pull" {
  name                = "expenses-tracker-pull"
  schedule_expression = var.pull_schedule_expression
  state               = var.pull_schedule_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "pull" {
  rule = aws_cloudwatch_event_rule.pull.name
  arn  = aws_lambda_function.pull.arn
}

resource "aws_lambda_permission" "pull_schedule" {
  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pull.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pull.arn
}
