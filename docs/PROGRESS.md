# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Code ready, CI green; blocked on owner (alert email, then first apply) | #2 |
| 3 to 10 | Not started | |

## Phase 2: waiting on owner
Read-only plan: 8 to add, 0 to change, 0 to destroy (budget, DynamoDB table, 3 SSM SecureString placeholders, pull role, role policy, log group).
Needs: real alert email in `infra/terraform.tfvars`, then apply, then confirm the budget subscription email.

## Notes
- Ingest raises on non-BOOK transactions; phase 3 filters pending ones first.
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
