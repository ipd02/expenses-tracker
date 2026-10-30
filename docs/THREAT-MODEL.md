# Threat model (draft)

Initial draft from phase 0. Revisited and tested in phase 9; every threat is `untested` until then.

## Assets

| Asset | Why it matters |
|---|---|
| Enable Banking app private key | Lets whoever holds it act as the application towards the bank API |
| Bank session / consent | Read access to the owner's account data until it expires or is revoked |
| Expense data | Personal financial history |
| Cognito identities and tokens | Gate all access to the app |
| AWS account | Cost, data and every other asset live here |

## Actors

- Anonymous internet user
- Authenticated user with a stolen token
- Malicious counterparty text coming from bank data (attacker-controlled strings in transactions)
- Compromised dependency (Python, npm, Terraform provider, GitHub Action)
- Owner mistake (committed secret, over-broad IAM, public resource)

## Trust boundaries

1. Browser <-> CloudFront (S3 static site)
2. Browser <-> API Gateway (JWT authorizer, Cognito)
3. API Gateway <-> Lambda
4. Lambda <-> DynamoDB
5. Lambda <-> SSM Parameter Store (app key, session)
6. Lambda <-> Enable Banking (external; consent redirect and transaction data are untrusted input)
7. Lambda <-> Textract (receipt images, processed in memory)

## Threats

| ID | Threat | Mitigation planned (phase) | Status |
|---|---|---|---|
| T1 | Forged or replayed consent callback (missing or reused `state`, callback hitting another user's session) | Unguessable single-use `state` bound to the session with expiry (phases 3-4) | untested |
| T2 | JWT tampering, `alg` confusion, expired or wrong-audience tokens accepted | API Gateway JWT authorizer pinned to the Cognito issuer and audience; no custom token parsing (phases 4-5) | untested |
| T3 | Authorization bypass (access without a token, access to data of another identity, unauthenticated routes) | All routes behind the authorizer; owner-scoped data access; deny by default (phases 4-5) | untested |
| T4 | XSS through counterparty or remittance text rendered in the UI | Render as text only (React escaping, no raw HTML sinks), strict CSP on CloudFront (phases 6, 8) | untested |
| T5 | Receipt upload abuse (oversized or malformed files, wrong type, cost abuse, parser exploits) | Size and type limits, in-memory processing only, throttling, nothing persisted (phase 7) | untested |
| T6 | Theft of app key or bank session from SSM (over-broad IAM, key in logs or state) | SecureString parameters, per-function IAM scoped to exact parameters, no values in logs or Terraform state (phase 2) | untested |
| T7 | IAM privilege escalation (wildcards, `iam:PassRole`, shared roles) | One role per function, no wildcard resources or actions, review with a scanner (phases 2, 9) | untested |
| T8 | Exposed resources (public S3 bucket, open API route, unauthenticated Lambda URL, leaked outputs) | Block public access, CloudFront OAC, no Lambda URLs, config scan with Prowler or ScoutSuite (phases 2, 8, 9) | untested |
| T9 | Supply chain (malicious or vulnerable dependency, hijacked GitHub Action or provider) | Pinned dependencies and lock files, provider lock file, minimal CI permissions, consider SHA-pinned actions and dependency scanning (phase 0 onward) | untested |
| T10 | Log leakage (tokens, keys, account identifiers or remittance text written to CloudWatch or CI logs) | No sensitive values logged, redaction helpers, short retention, secret scanning (phases 1-2, 8) | untested |
