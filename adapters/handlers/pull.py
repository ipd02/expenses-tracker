"""Lambda entry point for the scheduled pull. Thin: wiring, logging and a result summary."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from adapters.dynamo import DynamoExpenseRepository, DynamoSessionStore
from adapters.handlers import wiring
from adapters.safe_log import log
from adapters.ssm import SsmSecrets
from core.errors import BankError
from core.pull import PullStatus, pull

PROVIDER = wiring.PROVIDER
EXPIRING_DAYS = 14
EXPIRING_SOON_DAYS = 3
# The bank only serves history older than ~90 days right after the user authenticates;
# unattended requests beyond that fail with WRONG_TRANSACTIONS_PERIOD.
MAX_BACKFILL_DAYS = 89


@dataclass
class Deps:
    bank: Any
    repo: Any
    sessions: Any
    secrets: SsmSecrets
    session_param: str


def _default_deps() -> Deps:
    table = wiring.table()
    return Deps(
        wiring.bank(),
        DynamoExpenseRepository(table),
        DynamoSessionStore(table),
        wiring.secrets(),
        os.environ["SSM_SESSION"],
    )


def _lookback_days(event: dict[str, Any]) -> int:
    """Manual runs may pass {"backfill_days": N}; the schedule uses LOOKBACK_DAYS."""
    raw = event.get("backfill_days", os.environ.get("LOOKBACK_DAYS", "14"))
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 14
    return max(1, min(days, MAX_BACKFILL_DAYS))


def handler(
    event: dict[str, Any], context: object, *, deps: Deps | None = None
) -> dict[str, int | str]:
    d = deps or _default_deps()
    now = datetime.now(UTC)
    try:
        result = pull(
            provider=PROVIDER,
            load_session_id=lambda: d.secrets.get(d.session_param),
            bank=d.bank,
            repo=d.repo,
            sessions=d.sessions,
            now=now,
            date_from=(now - timedelta(days=_lookback_days(event or {}))).date(),
        )
    except Exception as exc:
        extra = {"reason": exc.code} if isinstance(exc, BankError) and exc.code else {}
        log("pull_failed", logging.ERROR, error_code=type(exc).__name__, **extra)
        raise
    counters = {
        "accounts": result.accounts,
        "inserted": result.inserted,
        "skipped": result.skipped,
    }
    if result.status is PullStatus.OK:
        log("pull_finished", status=result.status.value, **counters)
        days = result.days_left
        if days is not None and days <= EXPIRING_SOON_DAYS:
            # Matched by a CloudWatch metric filter that emails the owner to reconnect.
            log("consent_expiring_soon", logging.WARNING, days_left=days)
        elif days is not None and days <= EXPIRING_DAYS:
            log("consent_expiring", logging.WARNING, days_left=days)
    elif result.status is PullStatus.CONSENT_INVALID:
        # Matched by the CloudWatch metric filter that raises the reconnect alert.
        log("consent_invalid", logging.ERROR, status=result.status.value, **counters)
    else:
        log("pull_incomplete", logging.WARNING, status=result.status.value, **counters)
    summary: dict[str, int | str] = {"status": result.status.value, **counters}
    if result.days_left is not None:
        summary["days_left"] = result.days_left
    return summary
