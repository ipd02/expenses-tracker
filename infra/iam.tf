# pull: reads the bank API secrets, reads and writes the table.
# Later phases add consent, api and receipt with the same module.
module "pull" {
  source = "./modules/lambda_function_base"

  name = "pull"

  policy_statements = [
    {
      sid       = "TableReadWrite"
      actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem"]
      resources = [aws_dynamodb_table.main.arn, "${aws_dynamodb_table.main.arn}/index/gsi1"]
    },
    {
      sid       = "ReadBankSecrets"
      actions   = ["ssm:GetParameter"]
      resources = [for p in aws_ssm_parameter.secret : p.arn]
    },
  ]
}

# consent: starts a consent (direct invoke by the owner) and completes it from the public
# callback. It can read only the app id and key, never the session parameter, and write only
# consent state and sessions to the table.
module "consent" {
  source = "./modules/lambda_function_base"

  name = "consent"

  policy_statements = [
    {
      sid       = "ConsentStateAndSessions"
      actions   = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
      resources = [aws_dynamodb_table.main.arn]
    },
    {
      sid       = "ReadAppCredentials"
      actions   = ["ssm:GetParameter"]
      resources = [aws_ssm_parameter.secret["app_id"].arn, aws_ssm_parameter.secret["privkey"].arn]
    },
  ]
}
