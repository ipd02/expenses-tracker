"""In-memory implementations, used by tests and local runs."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime

from core.errors import NotFoundError
from core.models import (
    ConsentSession,
    Direction,
    Expense,
    MonthlyAggregate,
    SessionStatus,
    validate_category,
)
from core.query import aggregate_monthly, filter_expenses


class InMemoryExpenseRepository:
    def __init__(self) -> None:
        self._items: dict[str, Expense] = {}

    def put_if_absent(self, expense: Expense) -> bool:
        if expense.source_key in self._items:
            return False
        self._items[expense.source_key] = expense
        return True

    def put_many_if_absent(self, expenses: Iterable[Expense]) -> int:
        return sum(1 for e in expenses if self.put_if_absent(e))

    def get(self, source_key: str) -> Expense | None:
        return self._items.get(source_key)

    def list(
        self,
        *,
        account_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        category: str | None = None,
        uncategorized: bool = False,
        direction: Direction | None = None,
    ) -> Sequence[Expense]:
        return filter_expenses(
            self._items.values(),
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            category=category,
            uncategorized=uncategorized,
            direction=direction,
        )

    def set_category(self, source_key: str, category: str | None) -> Expense:
        current = self._items.get(source_key)
        if current is None:
            raise NotFoundError("expense not found")
        updated = replace(current, category=validate_category(category))
        self._items[source_key] = updated
        return updated

    def monthly_aggregates(
        self, *, year: int | None = None, account_id: str | None = None
    ) -> Sequence[MonthlyAggregate]:
        return aggregate_monthly(self._items.values(), year=year, account_id=account_id)


class InMemorySessionStore:
    def __init__(self) -> None:
        self._items: dict[str, ConsentSession] = {}

    def save(self, session: ConsentSession) -> None:
        self._items[session.session_id] = session

    def get(self, session_id: str) -> ConsentSession | None:
        return self._items.get(session_id)

    def get_active(self, provider: str, now: datetime) -> ConsentSession | None:
        active = [s for s in self._items.values() if s.provider == provider and s.is_active(now)]
        return max(active, key=lambda s: s.expires_at, default=None)

    def _mark(self, session_id: str, status: SessionStatus) -> ConsentSession:
        current = self._items.get(session_id)
        if current is None:
            raise NotFoundError("session not found")
        updated = replace(current, status=status)
        self._items[session_id] = updated
        return updated

    def mark_expired(self, session_id: str) -> ConsentSession:
        return self._mark(session_id, SessionStatus.EXPIRED)

    def mark_revoked(self, session_id: str) -> ConsentSession:
        return self._mark(session_id, SessionStatus.REVOKED)

    def list(self, provider: str | None = None) -> Sequence[ConsentSession]:
        out = [s for s in self._items.values() if provider is None or s.provider == provider]
        return sorted(out, key=lambda s: s.created_at)


class InMemoryConsentAttemptStore:
    def __init__(self) -> None:
        self._items: dict[str, datetime] = {}

    def create(self, state: str, expires_at: datetime) -> None:
        self._items[state] = expires_at

    def consume(self, state: str, now: datetime) -> bool:
        expires_at = self._items.pop(state, None)
        return expires_at is not None and now < expires_at
