# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Applied (8 added); budget subscription email pending owner confirmation | #2 |
| 3 to 10 | Not started | |

## Phase 2: applied
Applied 2026-11-04 to eu-west-1: budget, DynamoDB table, 3 SSM SecureString placeholders, pull role, role policy, log group (8 added, 0 changed, 0 destroyed).
Open: owner must confirm the budget SNS/email subscription; SSM placeholders still need real values before Phase 3 hits the bank API.

## Notes
- Ingest raises on non-BOOK transactions; phase 3 filters pending ones first.
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
