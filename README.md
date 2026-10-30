# expenses-tracker

A personal expenses app built to practise cloud security on AWS: deploy it, harden it, then attack it.

Card payments made with Apple Pay are captured by an iOS Shortcut that POSTs to an API on AWS. A small web app shows aggregated expenses, and receipts can be photographed and parsed into expenses (the image is never stored).

See [docs/PLAN.md](docs/PLAN.md) for goals, decisions and phases.

## Architecture (planned)

- **Ingest**: API Gateway (HTTP API) + Lambda + DynamoDB, bearer token (only its hash is stored)
- **Web**: React + Vite + TypeScript, served from S3 + CloudFront, Cognito for login
- **Receipts**: parsed with Amazon Textract in memory, nothing persisted
- **Infra**: Terraform
- **Portability**: `core/` is plain Python with no AWS imports; `adapters/` hold the AWS-specific glue

## Layout

```
core/            business logic (no cloud imports)
adapters/lambda/ thin Lambda handlers
infra/           Terraform
web/             React frontend
tests/
docs/
```

## Security notes

This repository must never contain secrets, tokens, account credentials, Terraform state or real transaction data. Use synthetic fixtures only. A pre-commit hook in `.githooks/` blocks common secret patterns:

```
git config core.hooksPath .githooks
```

## License

MIT
