# The owner's read API: HTTP API with the native JWT authorizer in front of one Lambda.
# Same code zip as the other functions (see pull.tf); its role has no bank-key access.
resource "aws_lambda_function" "api" {
  function_name = "expenses-tracker-api"
  role          = module.api.role_arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "adapters.handlers.api.handler"
  timeout       = 15
  memory_size   = 256

  filename         = data.archive_file.pull.output_path
  source_code_hash = data.archive_file.pull.output_base64sha256

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.main.name
      CONSENT_FUNCTION = aws_lambda_function.consent.function_name
      PULL_FUNCTION    = aws_lambda_function.pull.function_name
      OWNER_SUB        = aws_cognito_user.owner.sub
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = module.api.log_group_name
  }

  depends_on = [module.api]
}

resource "aws_apigatewayv2_api" "api" {
  name          = "expenses-tracker-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.cors_origins
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 600
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.api.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# Every route requires the authorizer; there is no $default route, so unknown paths get a 404
# from the gateway without invoking anything.
locals {
  api_routes = {
    "GET /expenses"               = { rate = 10, burst = 20 }
    "PUT /expenses/{id}/category" = { rate = 5, burst = 10 }
    "GET /summary/monthly"        = { rate = 5, burst = 10 }
    "GET /connection"             = { rate = 5, burst = 10 }
    "POST /connection"            = { rate = 1, burst = 2 }
    "POST /refresh"               = { rate = 1, burst = 2 }
  }
}

resource "aws_apigatewayv2_route" "api" {
  for_each = local.api_routes

  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/expenses-tracker-api"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "api" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 5
    throttling_burst_limit = 10
  }

  dynamic "route_settings" {
    for_each = local.api_routes

    content {
      route_key              = route_settings.key
      throttling_rate_limit  = route_settings.value.rate
      throttling_burst_limit = route_settings.value.burst
    }
  }

  # Route key and status only: no query strings, no tokens, no bodies.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      routeKey  = "$context.routeKey"
      status    = "$context.status"
      ip        = "$context.identity.sourceIp"
      authError = "$context.authorizer.error"
    })
  }

  depends_on = [aws_apigatewayv2_route.api]
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
