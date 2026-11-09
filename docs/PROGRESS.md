# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Merged and applied | #2 |
| 3 Pull pipeline | Merged and applied; live test and alarm test passed; schedule enabled | #4, #6 |
| 4 to 10 | Not started | |

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

## Notes
- Ingest raises on non-BOOK transactions; the pull filters them out first (done in phase 3).
- `adapters/lambda/` became `adapters/handlers/` (`lambda` is a Python keyword and cannot be imported normally).
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
