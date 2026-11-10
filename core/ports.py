"""Interfaces the core depends on. Implementations live in adapters/ (or memory.py)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from core.models import (
    BankSessionInfo,
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


class BankClient(Protocol):
    """Read-only bank access. Raises ConsentInvalidError, RateLimitedError, BankUnavailableError."""

    def get_session(self, session_id: str) -> BankSessionInfo: ...

    def iter_transactions(self, account_uid: str, date_from: date) -> Iterator[Mapping[str, Any]]:
        """All pages from date_from, lazily."""
        ...

    def get_balances(self, account_uid: str) -> Sequence[Mapping[str, Any]]: ...


class ConsentClient(Protocol):
    """Starts and completes a bank consent. Raises BankError subclasses."""

    def max_consent_validity(self, aspsp_name: str, country: str) -> timedelta: ...

    def start_auth(
        self, aspsp_name: str, country: str, state: str, redirect_url: str, valid_until: datetime
    ) -> str:
        """Returns the bank URL the user must open to approve the consent."""
        ...

    def create_session(self, code: str) -> tuple[str, BankSessionInfo]:
        """Exchange the callback code for (session_id, info)."""
        ...


class ConsentAttemptStore(Protocol):
    """Single-use `state` values for in-flight consents (CSRF protection)."""

    def create(self, state: str, expires_at: datetime) -> None: ...

    def consume(self, state: str, now: datetime) -> bool:
        """Atomically remove the state. True only if it existed and had not expired."""
        ...
