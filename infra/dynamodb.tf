# Single-table design. Items:
#   pk = ACCOUNT#<account_id>   sk = TXN#<booking_date>#<entry_reference>   expenses
#   pk = SESSION#<id>           sk = META                                    bank sessions
#   pk = CONSENT#<state>        sk = META                                    consent attempts (single use, expire via ttl)
# gsi1 lists expenses across accounts by month: gsi1pk = MONTH#<yyyy-mm>, gsi1sk = <booking_date>#<entry_reference>.
resource "aws_dynamodb_table" "main" {
  name         = "expenses-tracker"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "gsi1pk"
    type = "S"
  }

  attribute {
    name = "gsi1sk"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi1"
    projection_type = "ALL"

    key_schema {
      attribute_name = "gsi1pk"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "gsi1sk"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  # Encrypted at rest with the AWS-owned key (default); a customer-managed key arrives in phase 8.

  deletion_protection_enabled = false
}
