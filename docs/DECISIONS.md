# Decisions

Decisions that supplement [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md).

## Phase 0

1. **Terraform state is local and gitignored.** The repository is public and state files can contain sensitive values and account details. No remote backend; `*.tfstate*`, `.terraform/` and `*.tfvars` are ignored. CI never touches state: it runs `terraform init -backend=false` and `validate` only.
2. **CI is GitHub Actions** on the public repo, with `contents: read` permissions, running on pull requests and pushes to `main`. Three jobs: `python` (ruff check, ruff format --check, mypy, pytest), `terraform` (fmt -check, init -backend=false, validate) and `secrets` (gitleaks, full history via `fetch-depth: 0`).
3. **Actions are pinned by major tag** (`actions/checkout@v7`, `actions/setup-python@v7`, `hashicorp/setup-terraform@v4`, `gitleaks/gitleaks-action@v3`). Pinning to commit SHAs is a possible later hardening step (see supply chain in the threat model).
4. **Secret scanning in two layers:** the local `.githooks/pre-commit` hook (fast regex check) and gitleaks with default rules (`.gitleaks.toml`, allowlist only for the hook file, which contains the patterns).
5. **Python tooling:** setuptools, Python >= 3.12, ruff (lint and format, including the `S` security rules), mypy strict on `core/`, pytest with coverage. Dev dependencies are pinned exactly in `requirements-dev.txt`. A test statically enforces that `core/` has no AWS imports.
6. **`make check`** runs everything CI runs, plus the local gitleaks scan.
