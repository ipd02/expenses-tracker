# Decisions

Decisions that supplement [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md).

## Phase 0

1. **Terraform state is local and gitignored.** The repository is public and state files can contain sensitive values and account details. No remote backend; `*.tfstate*`, `.terraform/` and `*.tfvars` are ignored. CI never touches state: it runs `terraform init -backend=false` and `validate` only.
2. **CI is GitHub Actions** on the public repo, with `contents: read` permissions, running on pull requests and pushes to `main`. Three jobs: `python` (ruff check, ruff format --check, mypy, pytest), `terraform` (fmt -check, init -backend=false, validate) and `secrets` (gitleaks, full history via `fetch-depth: 0`).
3. **Actions are pinned by major tag** (`actions/checkout@v7`, `actions/setup-python@v7`, `hashicorp/setup-terraform@v4`, `gitleaks/gitleaks-action@v3`). Pinning to commit SHAs is a possible later hardening step (see supply chain in the threat model).
4. **Secret scanning in two layers:** the local `.githooks/pre-commit` hook (fast regex check) and gitleaks with default rules (`.gitleaks.toml`, allowlist only for the hook file, which contains the patterns).
5. **Python tooling:** setuptools, Python >= 3.12, ruff (lint and format, including the `S` security rules), mypy strict on `core/`, pytest with coverage. Dev dependencies are pinned exactly in `requirements-dev.txt`. A test statically enforces that `core/` has no AWS imports.
6. **`make check`** runs everything CI runs, plus the local gitleaks scan.

## Phase 3

7. **Unattended history is limited to under 90 days.** A 365-day request fails with `WRONG_TRANSACTIONS_PERIOD`; long history is only available right after the user authenticates. Manual backfill is capped at 89 days and the scheduled pull looks back 14 days.
8. **The pull runs 3 times a day** (`rate(8 hours)`), under the roughly 4 unattended refreshes per account per day. Lambda retries are off; the next tick is the retry.
9. **Bank error codes are logged as tokens, bodies never.** `BankError.code` holds only a plain `[A-Z_]` token.

## Phase 4

10. **Starting a consent is not a public endpoint.** Only `GET /callback` is public. Starting is a direct Lambda invoke (`make connect-bank`), authenticated by the owner's AWS credentials. An open start route would let anyone link their own bank account to this app and poison the data. Phase 5 adds an authenticated (Cognito) start route for the UI.
11. **`state` is stored in DynamoDB and consumed with a conditional delete**, so it is single use even under concurrent callbacks. It expires after 15 minutes (checked explicitly, because DynamoDB TTL cleanup is lazy). A malformed code does not burn the state; a failed bank exchange does.
12. **The callback never echoes input**, returns fixed pages with `no-store`, CSP `default-src 'none'`, `nosniff`, `no-referrer` and frame denial, is throttled to 2 requests per second (burst 5), and has no access logs because the query string carries the one-time code.
13. **The pull prefers the newest active session in the table.** The SSM `session` parameter is now only a bootstrap for the first session. Sessions carry `gsi1pk = SESSIONS#<provider>` so lookups are a query, not a scan.
14. **Expiry reminders come from the pull.** After a successful run it logs `consent_expiring` (14 days or fewer left) and `consent_expiring_soon` (3 days or fewer); one-day-period alarms email once per threshold. Expired or revoked consent still raises the existing `consent_invalid` alarm.
15. **Consent runs in its own Lambda and role.** It reads only the app id and key (not the session parameter) and writes only consent state and sessions.

## Account identity

16. **Accounts are keyed on a stable hash, not the bank's account uid.** Enable Banking issues new account uids with every consent session, so keying on the uid duplicated every transaction after the first reconnect (found on the first real reconnect, 2026-11-11). The key is `sha256(identification_hash)[:32]` from `GET /accounts/{uid}/details`, which was verified to be identical across two sessions. It is resolved once per session and stored on the session (`account_keys`). The bank's own hash is hashed again so it never appears in a partition key or a log. `scripts/migrate_account_ids.py` moved the existing rows; a previous session's uid can no longer be resolved by the bank, so the script takes the hash from the local spike file as a fallback.

