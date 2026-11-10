from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from adapters.handlers.pull import Deps, _lookback_days, handler
from core.errors import BankError, ConsentInvalidError
from core.memory import InMemoryExpenseRepository, InMemorySessionStore
from core.models import BankSessionInfo


class Secrets:
    def get(self, name: str) -> str:
        return "sess-1"


class Bank:
    def __init__(self, txs: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.txs, self.error = txs, error

    def get_session(self, session_id: str) -> BankSessionInfo:
        if self.error:
            raise self.error
        now = datetime.now(UTC)
        return BankSessionInfo(("uid-1",), now - timedelta(days=1), now + timedelta(days=60))

    def iter_transactions(self, account_uid: str, date_from: Any) -> Any:
        yield from self.txs


def deps(bank: Bank) -> Deps:
    return Deps(bank, InMemoryExpenseRepository(), InMemorySessionStore(), Secrets(), "/p")  # type: ignore[arg-type]


def test_scheduled_run_ingests_then_second_run_inserts_nothing(txs: dict[str, Any]) -> None:
    d = deps(Bank(list(txs.values())))
    first = handler({}, None, deps=d)
    assert {k: first[k] for k in ("status", "accounts", "inserted", "skipped")} == {
        "status": "ok",
        "accounts": 1,
        "inserted": 6,
        "skipped": 0,
    }
    second = handler({}, None, deps=d)
    assert (second["inserted"], second["skipped"]) == (0, 6)


def test_consent_failure_logs_error_and_returns_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    d = deps(Bank([], ConsentInvalidError("revoked")))
    with caplog.at_level(logging.INFO, logger="expenses"):
        out = handler({}, None, deps=d)
    assert out["status"] == "consent_invalid"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and '"event": "consent_invalid"' in errors[0].getMessage()


def test_unexpected_failure_is_logged_without_details_and_reraised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    d = deps(Bank([], BankError("IBAN ES00 secret", "WRONG_TRANSACTIONS_PERIOD")))
    with caplog.at_level(logging.INFO, logger="expenses"), pytest.raises(BankError):
        handler({}, None, deps=d)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "pull_failed" in text and "IBAN" not in text and "secret" not in text
    assert '"reason": "WRONG_TRANSACTIONS_PERIOD"' in text


def test_logs_never_contain_transaction_content(
    txs: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        handler({}, None, deps=deps(Bank(list(txs.values()))))
    text = " ".join(r.getMessage() for r in caplog.records)
    for needle in ("EXAMPLE SHOP", "12.50", "sess-1", "REF-0001"):
        assert needle not in text


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({}, 14),
        ({"backfill_days": 60}, 60),
        ({"backfill_days": 365}, 89),
        ({"backfill_days": 99999}, 89),
        ({"backfill_days": 0}, 1),
        ({"backfill_days": "x"}, 14),
        ({"backfill_days": None}, 14),
    ],
)
def test_lookback_days(event: dict[str, Any], expected: int) -> None:
    assert _lookback_days(event) == expected


@pytest.mark.parametrize(
    ("days", "event"),
    [
        (60, None),
        (14, "consent_expiring"),
        (4, "consent_expiring"),
        (3, "consent_expiring_soon"),
        (0, "consent_expiring_soon"),
    ],
)
def test_expiry_reminders(
    txs: dict[str, Any], caplog: pytest.LogCaptureFixture, days: int, event: str | None
) -> None:
    class ShortBank(Bank):
        def get_session(self, session_id: str) -> BankSessionInfo:
            now = datetime.now(UTC)
            return BankSessionInfo(
                ("uid-1",), now - timedelta(days=30), now + timedelta(days=days, hours=2)
            )

    with caplog.at_level(logging.INFO, logger="expenses"):
        out = handler({}, None, deps=deps(ShortBank(list(txs.values()))))
    assert out["days_left"] == days
    names = [r.getMessage() for r in caplog.records]
    reminders = [m for m in names if "consent_expiring" in m]
    if event is None:
        assert reminders == []
    else:
        assert len(reminders) == 1 and f'"event": "{event}"' in reminders[0]
        assert f'"days_left": {days}' in reminders[0]
