from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.errors import NotFoundError, ValidationError
from core.ingest import IngestResult, ingest
from core.memory import InMemoryExpenseRepository, InMemorySessionStore
from core.models import ConsentSession, Direction, SessionStatus
from core.normalize import normalize_transaction
from core.ports import ExpenseRepository, SessionStore


def test_ingest_inserts_all(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    result = ingest(txs.values(), account_id, repo)
    assert result == IngestResult(inserted=6, skipped=0)
    assert len(repo.list()) == 6


def test_reingest_is_idempotent(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    ingest(txs.values(), account_id, repo)
    repo.set_category(account_id + "REF-0001", "Fuel")
    again = ingest(txs.values(), account_id, repo)
    assert again == IngestResult(inserted=0, skipped=6)
    assert len(repo.list()) == 6
    got = repo.get(account_id + "REF-0001")
    assert got is not None
    assert got.category == "Fuel"  # re-ingest must not clobber user categories


def test_same_day_duplicates_both_kept(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    ingest([txs["same_day_a"], txs["same_day_b"]], account_id, repo)
    assert len(repo.list(date_from=date(2025, 4, 1), date_to=date(2025, 4, 1))) == 2


def test_duplicate_within_batch_skipped(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    result = ingest([txs["card_payment"], txs["card_payment"]], account_id, repo)
    assert result == IngestResult(inserted=1, skipped=1)


def test_same_reference_other_account_is_distinct(txs: dict[str, Any]) -> None:
    repo = InMemoryExpenseRepository()
    ingest([txs["card_payment"]], "acct-A", repo)
    result = ingest([txs["card_payment"]], "acct-B", repo)
    assert result.inserted == 1


def test_malformed_batch_is_atomic(txs: dict[str, Any], account_id: str) -> None:
    from core.errors import NormalizationError

    repo = InMemoryExpenseRepository()
    bad = {"entry_reference": "", "credit_debit_indicator": "DBIT"}
    with pytest.raises(NormalizationError):
        ingest([txs["card_payment"], bad], account_id, repo)
    assert repo.list() == []


def test_protocol_conformance() -> None:
    repo: ExpenseRepository = InMemoryExpenseRepository()
    store: SessionStore = InMemorySessionStore()
    assert repo is not None and store is not None


def test_put_if_absent(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    e = normalize_transaction(txs["card_payment"], account_id)
    assert repo.put_if_absent(e) is True
    assert repo.put_if_absent(e) is False
    assert repo.get("missing") is None


def test_list_filters_and_order(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    ingest(txs.values(), account_id, repo)
    repo.set_category(account_id + "REF-0001", "Fuel")
    assert [e.source_key[-8:] for e in repo.list()][:2] == ["REF-0005", "REF-0006"]
    assert len(repo.list(direction=Direction.CREDIT)) == 1
    assert len(repo.list(direction=Direction.DEBIT)) == 5
    assert len(repo.list(category="Fuel")) == 1
    assert len(repo.list(uncategorized=True)) == 5
    assert len(repo.list(account_id="other")) == 0
    assert len(repo.list(date_from=date(2025, 3, 6), date_to=date(2025, 3, 6))) == 2
    assert len(repo.list(date_from=date(2025, 3, 7))) == 2


def test_set_category(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    ingest([txs["card_payment"]], account_id, repo)
    key = account_id + "REF-0001"
    assert repo.set_category(key, "  Fuel ").category == "Fuel"
    assert repo.set_category(key, None).category is None
    with pytest.raises(NotFoundError):
        repo.set_category("nope", "Fuel")
    for bad in ("", "   ", "x" * 100, "a\nb"):
        with pytest.raises(ValidationError):
            repo.set_category(key, bad)


def test_monthly_aggregates(txs: dict[str, Any], account_id: str) -> None:
    repo = InMemoryExpenseRepository()
    ingest(txs.values(), account_id, repo)
    repo.set_category(account_id + "REF-0005", "Food")
    repo.set_category(account_id + "REF-0006", "Food")
    rows = repo.monthly_aggregates()
    by_key = {(r.month, r.category, r.direction): r for r in rows}
    food = by_key[("2025-04", "Food", Direction.DEBIT)]
    assert (food.total, food.count) == (Decimal("6.40"), 2)
    march_debits = by_key[("2025-03", None, Direction.DEBIT)]
    assert (march_debits.total, march_debits.count) == (Decimal("97.50"), 3)
    assert by_key[("2025-03", None, Direction.CREDIT)].total == Decimal("1000.00")
    assert [r.month for r in rows] == sorted(r.month for r in rows)
    assert repo.monthly_aggregates(year=2024) == []
    assert repo.monthly_aggregates(account_id="other") == []
    assert len(repo.monthly_aggregates(year=2025, account_id=account_id)) == len(rows)


NOW = datetime(2025, 6, 1, tzinfo=UTC)


def _session(sid: str, days: int, provider: str = "enablebanking") -> ConsentSession:
    return ConsentSession(
        session_id=sid,
        provider=provider,
        account_ids=("a1",),
        created_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=days),
    )


def test_session_expiry_by_time() -> None:
    store = InMemorySessionStore()
    store.save(_session("s1", 10))
    assert store.get_active("enablebanking", NOW) is not None
    later = NOW + timedelta(days=10)
    assert store.get_active("enablebanking", later) is None
    assert store.get("s1") is not None


def test_get_active_prefers_latest_and_filters_provider() -> None:
    store = InMemorySessionStore()
    store.save(_session("s1", 10))
    store.save(_session("s2", 90))
    store.save(_session("s3", 200, provider="other"))
    active = store.get_active("enablebanking", NOW)
    assert active is not None and active.session_id == "s2"
    assert store.get_active("none", NOW) is None
    assert [s.session_id for s in store.list("other")] == ["s3"]
    assert len(store.list()) == 3


def test_mark_expired_and_revoked() -> None:
    store = InMemorySessionStore()
    store.save(_session("s1", 10))
    store.save(_session("s2", 10))
    assert store.mark_expired("s1").status is SessionStatus.EXPIRED
    assert store.mark_revoked("s2").status is SessionStatus.REVOKED
    assert store.get_active("enablebanking", NOW) is None
    for fn in (store.mark_expired, store.mark_revoked):
        with pytest.raises(NotFoundError):
            fn("missing")


def test_session_validation_and_repr() -> None:
    with pytest.raises(ValidationError):
        ConsentSession("s", "p", (), NOW, NOW)
    with pytest.raises(ValidationError):
        ConsentSession("s", "p", (), datetime(2025, 1, 1), datetime(2025, 2, 1))
    assert "super-secret-id" not in repr(_session("super-secret-id", 5))


def test_expense_rejects_non_positive_amount(txs: dict[str, Any], account_id: str) -> None:
    from dataclasses import replace

    e = normalize_transaction(txs["card_payment"], account_id)
    with pytest.raises(ValidationError):
        replace(e, amount=Decimal(0))
