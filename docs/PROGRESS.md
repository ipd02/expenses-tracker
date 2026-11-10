# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Merged and applied | #2 |
| 3 Pull pipeline | Merged and applied; live test and alarm test passed; schedule enabled | #4, #6 |
| 4 Consent flow | Code and Terraform written, tests green; not applied | |
| 5 to 10 | Not started | |

## Phase 2: applied
Applied 2026-11-04 to eu-west-1: budget, DynamoDB table, 3 SSM SecureString placeholders, pull role, role policy, log group (8 added, 0 changed, 0 destroyed).
Open: owner must confirm the budget SNS/email subscription; SSM placeholders still need real values before Phase 3 hits the bank API.

## Phase 3: done
Live checks on 2026-11-05 and 2026-11-09 against the real bank and AWS:
- Real values loaded in SSM; manual run ingested 8 booked transactions, a second run inserted 0.
- Unattended history is limited to under 90 days (`WRONG_TRANSACTIONS_PERIOD` at 365), so manual backfill is capped at 89 days (PR #6). Bank error codes now appear in errors and logs.
- Expired-consent test: a fake session ID made the pull return `consent_invalid` without error or retry; the alarm fired and the owner received the email. The real session was restored.
- Schedule: `rate(8 hours)` (3 runs a day, under the roughly 4 unattended refreshes per account), enabled by default now (`pull_schedule_enabled = true`).
- Terraform state is local and gitignored: run applies from the main checkout.
- BBVA consent expires 2027-01-28. Phase 4 adds the reconnect flow and the early reminder.

## Phase 4: written, not applied
Branch `feat/phase-4-consent`. Public surface is only `GET /callback` (HTTP API, throttled, no access logs); starting a consent is `make connect-bank` (direct Lambda invoke). See DECISIONS 10 to 15.
Plan before applying: new consent Lambda and role, HTTP API with one route, 3 alarms and 2 metric filters (expiring, expiring soon), plus the existing pull function code update.
Owner steps after the apply:
1. Register the `consent_redirect_url` output as the redirect URL in the Enable Banking Control Panel (exact match, https).
2. Run `make connect-bank`, approve at the bank, expect the "Connected" page.
3. Check that the next pull uses the new session, then test an expiry reminder if wanted.

## Notes
- Ingest raises on non-BOOK transactions; the pull filters them out first (done in phase 3).
- `adapters/lambda/` became `adapters/handlers/` (`lambda` is a Python keyword and cannot be imported normally).
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
