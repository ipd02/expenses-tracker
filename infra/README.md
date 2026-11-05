# Infrastructure

Terraform, local state, region eu-west-1, AWS profile `expenses`. Contents: DynamoDB table, SSM SecureString placeholders (app ID, private key, bank session), per-function IAM roles and log groups (14 days retention), monthly budget alert.

## Apply / destroy

```
cp infra/terraform.tfvars.example infra/terraform.tfvars   # set alert_email (gitignored)
make tf-plan
make tf-apply
make tf-destroy
```

The targets call `scripts/tf.sh`, which exits unless `AWS_PROFILE=expenses` and `AWS_REGION=eu-west-1` (the Makefile sets both). Confirm the budget subscription email after the first apply.

## Loading secrets

Terraform creates the three parameters with a placeholder and ignores later value changes, so real values never enter state. Load them by CLI:

```
aws ssm put-parameter --profile expenses --region eu-west-1 --overwrite --type SecureString \
  --name /expenses-tracker/enablebanking/private-key --value file://key.pem
```

Same for `app-id` and `session`. Destroy removes the parameters, so reload them after a re-apply.
