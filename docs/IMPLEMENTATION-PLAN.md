# Implementation plan

Approved plan. Scope: BBVA only, AWS first, single user. Read [PLAN.md](PLAN.md), [DATA-MODEL.md](DATA-MODEL.md) and [IMPLEMENTATION-BRIEF.md](IMPLEMENTATION-BRIEF.md) first.

Each phase lists its goal, deliverables, and an acceptance check ("done when"). Phases marked **user** need something only the owner can do (bank approvals, MFA, email confirmations).

## Overview

```
0 Tooling ─┬─ 1 Core domain ──┐
           └─ 2 Infra base ───┴─ 3 Pull pipeline ─ 4 Consent flow ─┬─ 5 Auth + read API ─┬─ 6 Frontend ─ 7 Receipts
                                                                   └─────────────────────┘
                                                      8 Hardening ─ 9 Attack and write-up ─ 10 Teardown
```

Phases 1 and 2 can run in parallel. Phases 5 and 6 can run in parallel once the API contract is fixed. Everything from 8 on needs the earlier phases deployed.

---

## Phase 0: Tooling and guardrails
**Goal:** a repo that tests itself and cannot leak secrets.
- Python project (`pyproject.toml`, `pytest`, `ruff`, type checking), pinned dependencies.
- GitHub Actions: lint, tests, `terraform fmt` and `validate`, and a secret scan (gitleaks).
- A short threat-model draft (`docs/THREAT-MODEL.md`): assets, actors, trust boundaries, and the first list of threats. It is revisited in phase 9.
- Decision recorded: Terraform state stays local and gitignored (the repo is public).

**Done when:** CI is green on an empty commit, and a commit containing a fake AWS key is blocked by the hook and by CI.

## Phase 1: Core domain (no AWS)
**Goal:** all business logic, testable offline.
- Models: expense, account, consent session.
- Normalization from the Enable Banking transaction shape, including the counterparty rule and `payment_type` split from [DATA-MODEL.md](DATA-MODEL.md).
- Dedupe by `account_id + entry_reference`.
- `ExpenseRepository` and `SessionStore` interfaces, with in-memory implementations.
- `TicketReader` interface (implemented in phase 7).
- Synthetic fixtures built from the structural findings, with no real data.

**Done when:** tests cover card payments, direct debits (null creditor), credits, same-day duplicates, and re-ingesting the same batch (idempotent). No AWS imports anywhere in `core/`.

## Phase 2: Infrastructure foundation
**Goal:** the AWS resources every later phase needs, all in Terraform.
- Modules: DynamoDB table (on-demand), SSM parameters for the app key and ID, IAM roles per function (least privilege from the start), log groups with short retention, the budget alert (exists).
- Remote-less state, a `Makefile` or script for `plan` and `apply` with `AWS_PROFILE=expenses`.
- Tagging, and a destroy path tested once.

**Done when:** `terraform apply` creates everything from a clean checkout, `destroy` removes it, and the budget alert arrives by email. **User:** supply the alert email in `terraform.tfvars` and confirm the SNS/Budgets subscription email.

## Phase 3: Pull pipeline
**Goal:** BBVA transactions flow into DynamoDB on a schedule.
- Enable Banking client (JWT signing, accounts, balances, transactions with paging), behind an interface so it can be mocked.
- DynamoDB implementations of the repository and session store.
- Lambda handler (thin) and an EventBridge schedule that stays under the per-account unattended refresh limit.
- Logging with redaction: no amounts, counterparties, tokens or keys in logs.
- Bootstrap: the existing local session is loaded into SSM once by hand (**user**), so this phase does not depend on phase 4.
- Error handling for an expired or revoked consent: mark the session, stop retrying, raise an alert.

**Done when:** a manual invocation and a scheduled run both ingest the account; a second run inserts nothing; a simulated expired consent produces an alert and no retry storm.

## Phase 4: Consent flow
**Goal:** connect and reconnect BBVA from the app, not from a script.
- API Gateway endpoints to start consent and receive the callback, with `state` generated, stored and verified (CSRF protection), and single-use.
- Session storage, with its expiry date tracked.
- An alert before consent expires, and a "reconnect" path.
- **Redirect URL:** the production callback must be an `https` URL, so the existing Enable Banking application's redirect URL must be edited once the API Gateway domain exists (editing is possible in the Control Panel). **User:** update it in the Control Panel.

**Done when:** a fresh consent can be created end to end in the browser, the session lands in the store, and an expiring session triggers the reminder.

## Phase 5: Auth and read API
**Goal:** the owner can query their data securely.
- Cognito user pool: one user, self-signup disabled, passkey (WebAuthn) as the second factor.
- HTTP API with the native JWT authorizer.
- Endpoints: list and filter expenses, monthly aggregates by category, set a category, connection status, a rate-limited manual refresh.
- Request validation and throttling on every route.
- An OpenAPI contract, written first so phase 6 can start in parallel.

**Done when:** unauthenticated and wrongly scoped requests return 401 or 403, and an integration test covers each route. **User:** create the Cognito user and register the passkey.

## Phase 6: Frontend
**Goal:** a minimal, clean React app.
- Vite, React and TypeScript. Login through Cognito, expense list with filters, monthly totals by category, category editing, bank connection status with a reconnect button.
- Static build deployed to S3 behind CloudFront (origin access control, no public bucket), with security headers and a strict content security policy.
- Few dependencies; one small charting library.

**Done when:** the app is served over HTTPS from CloudFront, login works with the passkey, and the bucket is not reachable directly.

## Phase 7: Receipt upload
**Goal:** turn a receipt photo into an expense, without storing the photo.
- Client-side downscale before upload, then an authenticated endpoint that holds the image in memory only.
- Amazon Textract AnalyzeExpense through the `TicketReader` interface.
- A confirmation screen, then only the resulting expense is saved. A receipt expense is independent: it is not matched to any bank transaction.
- Size and type limits, and a timeout.

**Done when:** a sample receipt produces an editable expense, and no object is written to S3 or disk at any point (checked in tests and in CloudTrail).

## Phase 8: Hardening
**Goal:** apply what the security-oriented phases need, then measure it.
- Customer-managed KMS key for DynamoDB and SSM (the first deliberate cost, about $1 per month).
- IAM review with Access Analyzer and the policy simulator; remove any wildcards.
- CloudTrail trail, plus alarms for root use, IAM changes and failed consent callbacks.
- API throttling and quotas, log retention review, dependency scanning in CI, and headers and CSP review.
- Cognito advanced security options evaluated against their cost.

**Done when:** a written checklist shows each control, how it was verified, and its cost.

## Phase 9: Attack and write-up
**Goal:** break my own system and document it.
- Revisit `THREAT-MODEL.md` and test each threat: forged or replayed consent callback, JWT tampering and algorithm confusion, authorization bypass, injection through counterparty text rendered in the UI (XSS), upload abuse on the receipt endpoint, theft of the app key or session from SSM, IAM privilege escalation, and exposed resources.
- Tools: a cloud configuration scanner (Prowler or ScoutSuite) and manual API testing.
- Each finding: description, impact, reproduction, fix, and a regression test where possible.

**Done when:** `docs/FINDINGS.md` lists every finding with its fix and retest result.

## Phase 10: Teardown and migration prep
**Goal:** leave no cost behind and keep the home-server option open.
- A tested `terraform destroy` runbook, a final cost check, and revoking the bank consent and the Enable Banking application if the project ends.
- A container adapter (FastAPI) and a second repository implementation (SQLite) to prove `core/` is portable.

**Done when:** the AWS account is empty of project resources and the container build runs the same tests.

---

## Cross-cutting rules
- **Public repo:** no secrets, state files, account IDs, real transactions, or personal remittance text, in code, tests, docs, logs or screenshots.
- **Cost:** free tier only until phase 8; no NAT gateway, WAF or Secrets Manager.
- **Least privilege:** each Lambda has its own role scoped to its table, parameter, and actions.
- **Branching:** one pull request per phase, with tests and a passing CI before merge.

## Decisions
1. The existing Enable Banking application's redirect URL is edited for the AWS callback; no second application.
2. Receipts become plain, independent expenses. No bank matching.
3. Cognito second factor: passkey.
4. CI: GitHub Actions on the public repo, including the secret scan.
5. Order: all implementation phases (0 to 8) come first, with security by default throughout. Offensive testing (phase 9) happens only at the end.
