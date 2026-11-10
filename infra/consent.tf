# Bank consent flow. The only public surface is GET /callback on an HTTP API; starting a consent
# is a direct Lambda invoke (IAM-authenticated, see scripts/connect_bank.sh).
# Code ships in the same zip as the pull function (see pull.tf).
resource "aws_lambda_function" "consent" {
  function_name = "expenses-tracker-consent"
  role          = module.consent.role_arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "adapters.handlers.consent.handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.pull.output_path
  source_code_hash = data.archive_file.pull.output_base64sha256

  environment {
    variables = {
      TABLE_NAME      = aws_dynamodb_table.main.name
      SSM_APP_ID      = local.secret_parameters.app_id
      SSM_PRIVATE_KEY = local.secret_parameters.privkey
      REDIRECT_URL    = "${aws_apigatewayv2_api.consent.api_endpoint}/callback"
      ASPSP_NAME      = "BBVA"
      ASPSP_COUNTRY   = "ES"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = module.consent.log_group_name
  }

  depends_on = [module.consent]
}

resource "aws_apigatewayv2_api" "consent" {
  name          = "expenses-tracker-consent"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "consent" {
  api_id                 = aws_apigatewayv2_api.consent.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.consent.invoke_arn
  payload_format_version = "2.0"
}

# One route only: anything else gets a 404 from API Gateway without invoking the Lambda.
resource "aws_apigatewayv2_route" "callback" {
  api_id    = aws_apigatewayv2_api.consent.id
  route_key = "GET /callback"
  target    = "integrations/${aws_apigatewayv2_integration.consent.id}"
}

# No access logs on purpose: the callback query string carries the one-time authorization code.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.consent.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 2
    throttling_burst_limit = 5
  }
}

resource "aws_lambda_permission" "consent_api" {
  statement_id  = "AllowApiGatewayCallback"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.consent.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.consent.execution_arn}/*/GET/callback"
}
