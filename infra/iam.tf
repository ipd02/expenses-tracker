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

# api: the owner's read API. It never touches SSM (no bank key). Table access is narrowed by key
# prefix: reads of expenses, month index entries and sessions (for connection status); writes
# only to the refresh-limiter item and to the `category` attribute of expense items. It may start
# a consent and a pull by invoking those two functions, nothing else.
module "api" {
  source = "./modules/lambda_function_base"

  name = "api"

  policy_statements = [
    {
      sid       = "TableRead"
      actions   = ["dynamodb:GetItem", "dynamodb:Query"]
      resources = [aws_dynamodb_table.main.arn, "${aws_dynamodb_table.main.arn}/index/gsi1"]
      conditions = [{
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["ACCOUNT#*", "MONTH#*", "SESSIONS#*", "RATE#*"]
      }]
    },
    {
      sid       = "RefreshLimiter"
      actions   = ["dynamodb:PutItem"]
      resources = [aws_dynamodb_table.main.arn]
      conditions = [{
        test     = "ForAllValues:StringEquals"
        variable = "dynamodb:LeadingKeys"
        values   = ["RATE#refresh"]
      }]
    },
    {
      sid       = "SetCategory"
      actions   = ["dynamodb:UpdateItem"]
      resources = [aws_dynamodb_table.main.arn]
      conditions = [
        {
          test     = "ForAllValues:StringLike"
          variable = "dynamodb:LeadingKeys"
          values   = ["ACCOUNT#*"]
        },
        {
          test     = "ForAllValues:StringEquals"
          variable = "dynamodb:Attributes"
          # DynamoDB requires the table's and the index's key attributes in this list for writes.
          # Primary keys cannot be modified; the index keys can, so the worst case is moving a
          # row between months in gsi1. Amounts, counterparties and descriptions stay unwritable.
          values = ["category", "pk", "sk", "gsi1pk", "gsi1sk"]
        },
      ]
    },
    {
      sid       = "StartConsentAndPull"
      actions   = ["lambda:InvokeFunction"]
      resources = [aws_lambda_function.consent.arn, aws_lambda_function.pull.arn]
    },
  ]
}
