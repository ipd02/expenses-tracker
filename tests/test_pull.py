from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from core.errors import BankUnavailableError, ConsentInvalidError, RateLimitedError
from core.memory import InMemoryExpenseRepository, InMemorySessionStore
from core.models import BankSessionInfo, SessionStatus
from core.pull import PullStatus, account_id_for, pull

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
SID = "session-0001"
UID = "00000000-0000-0000-0000-000000000000"


class FakeBank:
    def __init__(
        self,
        txs: Sequence[Mapping[str, Any]],
        *,
        valid_until: datetime = NOW + timedelta(days=60),
        error: Exception | None = None,
        fail_after: int | None = None,
    ) -> None:
        self.txs = list(txs)
        self.valid_until = valid_until
        self.error = error
        self.fail_after = fail_after
        self.session_calls = 0
        self.tx_calls = 0

    def get_session(self, session_id: str) -> BankSessionInfo:
        self.session_calls += 1
        if self.error and self.fail_after is None:
            raise self.error
        return BankSessionInfo((UID,), NOW - timedelta(days=30), self.valid_until)

    def iter_transactions(self, account_uid: str, date_from: date) -> Iterator[Mapping[str, Any]]:
        self.tx_calls += 1
        for i, tx in enumerate(self.txs):
            if self.error and self.fail_after is not None and i >= self.fail_after:
                raise self.error
            yield tx

    def get_balances(self, account_uid: str) -> Sequence[Mapping[str, Any]]:
        return []


def run(bank: FakeBank, repo: InMemoryExpenseRepository, sessions: InMemorySessionStore) -> Any:
    return pull(
        provider="enablebanking",
        load_session_id=lambda: SID,
        bank=bank,
        repo=repo,
        sessions=sessions,
        now=NOW,
        date_from=date(2026, 10, 1),
    )


def test_first_run_registers_session_and_ingests(txs: dict[str, Any]) -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    result = run(FakeBank(list(txs.values())), repo, sessions)
    assert result.status is PullStatus.OK
    assert (result.accounts, result.inserted, result.skipped) == (1, 6, 0)
    saved = sessions.get(SID)
    assert saved is not None and saved.account_ids == (UID,)
    assert all(e.account_id == account_id_for("enablebanking", UID) for e in repo.list())


def test_second_run_inserts_nothing_and_reuses_session(txs: dict[str, Any]) -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    bank = FakeBank(list(txs.values()))
    run(bank, repo, sessions)
    again = run(bank, repo, sessions)
    assert (again.inserted, again.skipped) == (0, 6)
    assert bank.session_calls == 1


def test_pending_transactions_are_filtered(txs: dict[str, Any]) -> None:
    pending = copy.deepcopy(txs["card_payment"])
    pending["status"] = "PDNG"
    pending["entry_reference"] = "REF-PENDING"
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    result = run(FakeBank([*txs.values(), pending]), repo, sessions)
    assert (result.inserted, result.skipped) == (6, 1)
    assert repo.get(account_id_for("enablebanking", UID) + "REF-PENDING") is None


@pytest.mark.parametrize(
    ("reason", "expected"), [("expired", SessionStatus.EXPIRED), ("revoked", SessionStatus.REVOKED)]
)
def test_consent_error_marks_session_and_stops(
    txs: dict[str, Any], reason: str, expected: SessionStatus
) -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    run(FakeBank(list(txs.values())), repo, sessions)  # register session
    bank = FakeBank([txs["card_payment"]], error=ConsentInvalidError(reason), fail_after=0)
    result = run(bank, repo, sessions)
    assert result.status is PullStatus.CONSENT_INVALID
    stored = sessions.get(SID)
    assert stored is not None and stored.status is expected
    # a later run must not call the bank again
    calls = bank.tx_calls
    assert run(bank, repo, sessions).status is PullStatus.CONSENT_INVALID
    assert bank.tx_calls == calls


def test_consent_error_on_first_sight_saves_nothing() -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    result = run(FakeBank([], error=ConsentInvalidError("revoked")), repo, sessions)
    assert result.status is PullStatus.CONSENT_INVALID
    assert sessions.get(SID) is None


def test_session_past_expiry_does_not_call_the_bank() -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    bank = FakeBank([], valid_until=NOW - timedelta(seconds=1))
    result = run(bank, repo, sessions)
    assert result.status is PullStatus.CONSENT_INVALID
    assert bank.tx_calls == 0
    stored = sessions.get(SID)
    assert stored is not None and stored.status is SessionStatus.EXPIRED


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RateLimitedError("x"), PullStatus.RATE_LIMITED),
        (BankUnavailableError("x"), PullStatus.BANK_UNAVAILABLE),
    ],
)
def test_transient_errors_stop_without_marking_session(
    txs: dict[str, Any], error: Exception, status: PullStatus
) -> None:
    repo, sessions = InMemoryExpenseRepository(), InMemorySessionStore()
    run(FakeBank(list(txs.values())), repo, sessions)
    result = run(FakeBank(list(txs.values()), error=error, fail_after=0), repo, sessions)
    assert result.status is status
    stored = sessions.get(SID)
    assert stored is not None and stored.status is SessionStatus.ACTIVE
