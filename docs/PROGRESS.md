# Progress

Orchestrator log. No secrets, account IDs or real data here.

| Phase | Status | PR |
|---|---|---|
| 0 Tooling and guardrails | In review | see below |
| 1 Core domain | Not started | |
| 2 Infra foundation | Not started | |
| 3 to 10 | Not started | |

## Phase 0
- Done: Python tooling (ruff, mypy, pytest), Makefile, CI (lint/tests, terraform fmt/validate, gitleaks), threat model draft, decisions record.
- Verified locally: `make check` green; pre-commit hook and gitleaks both block a fake AWS key.
- Open: CI-side block of a fake key is confirmed by the same gitleaks config locally; not re-proven on GitHub to avoid committing key-shaped strings to a public repo.
- Open: GitHub Actions pinned by major tag, not SHA (noted in DECISIONS.md).
