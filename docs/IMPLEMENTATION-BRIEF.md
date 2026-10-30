# Implementation brief

For whoever builds this. Read [PLAN.md](PLAN.md) and [DATA-MODEL.md](DATA-MODEL.md) first.

## Constraints
- AWS account: dedicated member account, region `eu-west-1`, profile `expenses` (IAM user with MFA). Never use root.
- Cost: stay inside the free tier. Avoid NAT gateways, WAF, customer-managed KMS keys (until the hardening phase) and Secrets Manager.
- Public repository: no secrets, account IDs, real transactions, Terraform state or `*.tfvars`. Synthetic fixtures only. Activate the hook with `git config core.hooksPath .githooks`.
- `core/` has no AWS imports; AWS-specific code lives in `adapters/`.

## Enable Banking
- Base URL `https://api.enablebanking.com`. Requests carry a JWT signed RS256 with the application's private key; header `kid` is the application ID, claims `iss=enablebanking.com`, `aud=api.enablebanking.com`, `iat`, `exp` (at most 24 h).
- Consent: `POST /auth` returns a bank URL; the user approves; the redirect carries `code` and `state`; `POST /sessions` exchanges it for a session and account list. Validity is capped per bank by `maximum_consent_validity` (`GET /aspsps?country=ES`).
- Data: `GET /accounts/{uid}/transactions` (date range, `continuation_key` paging) and `/balances`.
- The private key and application ID go in SSM Parameter Store as SecureString; session IDs are stored with their expiry.
- The redirect URL must match the registered one exactly and be `https`. A production callback will need a real HTTPS URL (the API Gateway or CloudFront domain).

## Build order
1. Terraform base (exists: provider and budget alert). Add DynamoDB, SSM parameters, IAM.
2. `core/`: models, normalization, deduplication, repository interface. Tests with synthetic fixtures.
3. Pull Lambda on an EventBridge schedule; idempotent writes.
4. Consent flow: start, callback, status and expiry.
5. Cognito, read API (JWT authorizer), React frontend.
6. Receipt upload parsed with Textract in memory (not stored).
7. Hardening, then the attack and write-up phase.

## Open questions
- Whether the raw BBVA rows confirm the creditor/debtor rule in DATA-MODEL.md.
- Scope is BBVA only. Trade Republic was dropped (no merchant text, no transaction ID). Bankinter may be added later as another provider session.
