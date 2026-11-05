# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Merged and applied | #2 |
| 3 Pull pipeline | Merged and applied (schedule disabled); waiting on real SSM values and a live test | #4 |
| 4 to 10 | Not started | |

## Phase 2: applied
Applied 2026-11-04 to eu-west-1: budget, DynamoDB table, 3 SSM SecureString placeholders, pull role, role policy, log group (8 added, 0 changed, 0 destroyed).
Open: owner must confirm the budget SNS/email subscription; SSM placeholders still need real values before Phase 3 hits the bank API.

## Phase 3: applied, waiting on owner
Applied 2026-11-05 (10 added). Smoke invocation with placeholder secrets failed at key parsing as expected: the package imports on arm64, cryptography loads, and the log line carries only the error class.
- The EventBridge rule is DISABLED (`pull_schedule_enabled = false`).
- Terraform state is local and gitignored. Run applies from the main checkout (`infra/terraform.tfstate`); a copy also sits in the old agent worktree.
Owner steps:
1. Confirm the SNS alerts subscription email.
2. Load real values with `aws ssm put-parameter --overwrite` for app-id, private-key and session.
3. Invoke once, check a second run inserts nothing, then set `pull_schedule_enabled = true` and apply.
Still untested against real AWS and the real API: simulated expired consent producing the alert.

## Notes
- Ingest raises on non-BOOK transactions; the pull filters them out first (done in phase 3).
- `adapters/lambda/` became `adapters/handlers/` (`lambda` is a Python keyword and cannot be imported normally).
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
