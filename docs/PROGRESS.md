# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | Merged | #1 |
| 1 Core domain | Merged | #3 |
| 2 Infra foundation | Applied (8 added); budget subscription email pending owner confirmation | #2 |
| 3 Pull pipeline | Code and Terraform ready, tests green; not applied (needs owner OK, then SSM values) | |
| 4 to 10 | Not started | |

## Phase 2: applied
Applied 2026-11-04 to eu-west-1: budget, DynamoDB table, 3 SSM SecureString placeholders, pull role, role policy, log group (8 added, 0 changed, 0 destroyed).
Open: owner must confirm the budget SNS/email subscription; SSM placeholders still need real values before Phase 3 hits the bank API.

## Phase 3: ready, not applied
Built on branch `phase-3-pull` (stacked on `phase-2-infra`; rebase onto `main` once PR #2 merges).
- `core/pull.py`: one pull run. Pending rows filtered, consent errors mark the stored session and stop, rate limits and bank outages stop the run. Nothing retries in-run.
- `adapters/`: Enable Banking client (RS256 JWT, paging, error-code mapping), DynamoDB expense repository (writes and key lookup only; list, categorize and aggregate come in phase 5) and session store, SSM reader, allowlist logger, Lambda handler.
- Infra (read-only plan: 10 to add, 0 to change, 0 to destroy): Lambda (arm64, no retries), EventBridge rule at `rate(8 hours)` (3 runs a day, under the ~4 unattended refreshes), SNS alerts topic with email subscription, metric filter plus alarm for `consent_invalid`, Lambda errors alarm.
- The schedule is created DISABLED (`pull_schedule_enabled = false`).
Owner steps before enabling it:
1. Approve the apply, then confirm the second SNS subscription email.
2. Load the real values: `aws ssm put-parameter --overwrite` for app-id, private-key and session (profile `expenses`).
3. Invoke once by hand, check the second run inserts nothing, then set `pull_schedule_enabled = true` and apply.
Not yet done: the "simulated expired consent produces an alert" check against real AWS.

## Notes
- Ingest raises on non-BOOK transactions; the pull filters them out first (done in phase 3).
- `adapters/lambda/` became `adapters/handlers/` (`lambda` is a Python keyword and cannot be imported normally).
- Remittance with fewer than 3 segments gives counterparty "Unknown"; revisit with real data.
- GitHub Actions pinned by major tag, not SHA (DECISIONS.md).
