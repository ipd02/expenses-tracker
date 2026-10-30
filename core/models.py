"""Domain models. Plain frozen dataclasses; amounts are Decimal, never float."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from core.errors import ValidationError

MAX_CATEGORY_LEN = 64


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class Source(StrEnum):
    ENABLEBANKING = "enablebanking"
    RECEIPT = "receipt"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


def validate_category(category: str | None) -> str | None:
    """Return a trimmed category, or None. Reject empty/oversized/control-char values."""
    if category is None:
        return None
    cleaned = category.strip()
    if not cleaned or len(cleaned) > MAX_CATEGORY_LEN or not cleaned.isprintable():
        raise ValidationError("invalid category")
    return cleaned


@dataclass(frozen=True)
class Expense:
    account_id: str
    source_key: str
    booking_date: date
    amount: Decimal  # always positive; sign is carried by `direction`
    currency: str
    direction: Direction
    counterparty: str
    payment_type: str
    description: str
    source: Source
    category: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def __post_init__(self) -> None:
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValidationError("amount must be a positive finite decimal")

    @property
    def amount_str(self) -> str:
        """Serialised form: plain decimal string (no exponent)."""
        return format(self.amount, "f")

    @property
    def month(self) -> str:
        return self.booking_date.strftime("%Y-%m")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "source_key": self.source_key,
            "booking_date": self.booking_date.isoformat(),
            "amount": self.amount_str,
            "currency": self.currency,
            "direction": self.direction.value,
            "counterparty": self.counterparty,
            "payment_type": self.payment_type,
            "description": self.description,
            "category": self.category,
            "raw": dict(self.raw),
            "source": self.source.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Expense:
        return cls(
            account_id=data["account_id"],
            source_key=data["source_key"],
            booking_date=date.fromisoformat(data["booking_date"]),
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            direction=Direction(data["direction"]),
            counterparty=data["counterparty"],
            payment_type=data["payment_type"],
            description=data["description"],
            category=data.get("category"),
            raw=data.get("raw") or {},
            source=Source(data["source"]),
        )


@dataclass(frozen=True)
class Account:
    account_id: str  # provider + account uid
    provider: str
    name: str = ""
    currency: str = "EUR"


@dataclass(frozen=True)
class ConsentSession:
    session_id: str  # sensitive: never log
    provider: str
    account_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    status: SessionStatus = SessionStatus.ACTIVE

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValidationError("session timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValidationError("expires_at must be after created_at")

    def is_active(self, now: datetime) -> bool:
        return self.status is SessionStatus.ACTIVE and now < self.expires_at

    def __repr__(self) -> str:
        # Deliberately omits session_id.
        return (
            f"ConsentSession(provider={self.provider!r}, status={self.status.value!r}, "
            f"expires_at={self.expires_at.isoformat()!r})"
        )


@dataclass(frozen=True)
class ReceiptLine:
    description: str
    amount: Decimal


@dataclass(frozen=True)
class ParsedReceipt:
    merchant: str
    booking_date: date | None
    total: Decimal
    currency: str
    lines: tuple[ReceiptLine, ...] = ()


@dataclass(frozen=True)
class MonthlyAggregate:
    month: str  # YYYY-MM
    category: str | None
    direction: Direction
    currency: str
    total: Decimal
    count: int
