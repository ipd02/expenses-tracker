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
