"""In-memory implementations, used by tests and local runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from core.errors import NotFoundError
from core.models import (
    ConsentSession,
    Direction,
    Expense,
    MonthlyAggregate,
    SessionStatus,
    validate_category,
)


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
        out = [
            e
            for e in self._items.values()
            if (account_id is None or e.account_id == account_id)
            and (date_from is None or e.booking_date >= date_from)
            and (date_to is None or e.booking_date <= date_to)
            and (category is None or e.category == category)
            and (not uncategorized or e.category is None)
            and (direction is None or e.direction is direction)
        ]
        out.sort(key=lambda e: e.source_key)
        out.sort(key=lambda e: e.booking_date, reverse=True)
        return out

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
        totals: dict[tuple[str, str | None, Direction, str], Decimal] = defaultdict(Decimal)
        counts: dict[tuple[str, str | None, Direction, str], int] = defaultdict(int)
        for e in self._items.values():
            if year is not None and e.booking_date.year != year:
                continue
            if account_id is not None and e.account_id != account_id:
                continue
            key = (e.month, e.category, e.direction, e.currency)
            totals[key] += e.amount
            counts[key] += 1
        rows = [
            MonthlyAggregate(m, c, d, cur, total, counts[(m, c, d, cur)])
            for (m, c, d, cur), total in totals.items()
        ]
        rows.sort(key=lambda r: (r.month, r.category or "", r.direction.value, r.currency))
        return rows


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
