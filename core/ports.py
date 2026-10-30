"""Interfaces the core depends on. Implementations live in adapters/ (or memory.py)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Protocol

from core.models import (
    ConsentSession,
    Direction,
    Expense,
    MonthlyAggregate,
    ParsedReceipt,
)


class ExpenseRepository(Protocol):
    def put_if_absent(self, expense: Expense) -> bool:
        """Store the expense unless its source_key exists. True if inserted."""
        ...

    def put_many_if_absent(self, expenses: Iterable[Expense]) -> int:
        """Batch put_if_absent; returns the number actually inserted."""
        ...

    def get(self, source_key: str) -> Expense | None: ...

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
        """Filter (dates inclusive). Newest first, ties ordered by source_key."""
        ...

    def set_category(self, source_key: str, category: str | None) -> Expense:
        """Raises NotFoundError if absent, ValidationError for a bad category."""
        ...

    def monthly_aggregates(
        self, *, year: int | None = None, account_id: str | None = None
    ) -> Sequence[MonthlyAggregate]:
        """Totals grouped by month, category, direction and currency."""
        ...


class SessionStore(Protocol):
    def save(self, session: ConsentSession) -> None: ...

    def get(self, session_id: str) -> ConsentSession | None: ...

    def get_active(self, provider: str, now: datetime) -> ConsentSession | None:
        """Latest-expiring session that is active and not past expiry."""
        ...

    def mark_expired(self, session_id: str) -> ConsentSession: ...

    def mark_revoked(self, session_id: str) -> ConsentSession: ...

    def list(self, provider: str | None = None) -> Sequence[ConsentSession]: ...


class TicketReader(Protocol):
    """Reads a receipt image. Implemented in phase 7; image bytes must not be persisted."""

    def read(self, image_bytes: bytes, content_type: str) -> ParsedReceipt: ...
