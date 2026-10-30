"""Normalize an Enable Banking transaction into an Expense.

All input text is untrusted: it is only parsed, never evaluated, control characters are
stripped, whitespace is collapsed and lengths are bounded (see MAX_* constants).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from core.errors import NormalizationError
from core.models import Direction, Expense, Source

MAX_COUNTERPARTY_LEN = 140
MAX_DESCRIPTION_LEN = 500
MAX_PAYMENT_TYPE_LEN = 140
MAX_ID_LEN = 128
MAX_AMOUNT_LEN = 24
SEGMENT_SEPARATOR = " // "
UNKNOWN_COUNTERPARTY = "Unknown"

_CURRENCY_RE = re.compile(r"[A-Z]{3}")
_AMOUNT_RE = re.compile(r"[0-9]{1,15}(\.[0-9]{1,6})?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WS_RE = re.compile(r"\s+")
_DIRECTIONS = {"DBIT": Direction.DEBIT, "CRDT": Direction.CREDIT}


def clean_text(value: object, max_len: int) -> str:
    """Strip control chars, collapse whitespace, trim and truncate."""
    if not isinstance(value, str):
        return ""
    text = _WS_RE.sub(" ", _CONTROL_RE.sub(" ", value)).strip()
    return text[:max_len].rstrip()


def _name(party: object) -> str:
    if isinstance(party, Mapping):
        return clean_text(party.get("name"), MAX_COUNTERPARTY_LEN)
    return ""


def _parse_amount(tx: Mapping[str, Any]) -> tuple[Decimal, str]:
    ta = tx.get("transaction_amount")
    if not isinstance(ta, Mapping):
        raise NormalizationError("missing transaction_amount")
    raw_amount = ta.get("amount")
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, str | int | Decimal):
        raise NormalizationError("invalid amount")
    text = str(raw_amount).strip()
    if text.startswith("-"):
        # Direction is carried by credit_debit_indicator; amounts must be unsigned.
        raise NormalizationError("negative amount")
    if len(text) > MAX_AMOUNT_LEN or not _AMOUNT_RE.fullmatch(text):
        raise NormalizationError("invalid amount")
    amount = Decimal(text)
    if amount <= 0:
        raise NormalizationError("amount must be positive")
    currency = ta.get("currency")
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
        raise NormalizationError("invalid currency")
    return amount, currency


def _parse_date(tx: Mapping[str, Any]) -> date:
    for key in ("booking_date", "value_date"):
        value = tx.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise NormalizationError(f"invalid {key}")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise NormalizationError(f"invalid {key}") from exc
    raise NormalizationError("missing booking_date")


def _remittance(tx: Mapping[str, Any]) -> str:
    info = tx.get("remittance_information")
    if info is None:
        return ""
    if isinstance(info, list) and all(isinstance(i, str) for i in info):
        return SEGMENT_SEPARATOR.join(info) if len(info) > 1 else "".join(info)
    raise NormalizationError("invalid remittance_information")


def split_remittance(text: str) -> tuple[str, str]:
    """Return (payment_type, details) from '<type> // <subtype> // <details>'.

    payment_type is the first segment; details the third. Missing parts are ''.
    Each segment is whitespace-collapsed, as the bank pads fields with spaces.
    """
    parts = text.split(SEGMENT_SEPARATOR)
    payment_type = clean_text(parts[0], MAX_PAYMENT_TYPE_LEN)
    details = clean_text(parts[2], MAX_COUNTERPARTY_LEN) if len(parts) >= 3 else ""
    return payment_type, details


def normalize_transaction(tx: Mapping[str, Any], account_id: str) -> Expense:
    """Convert one Enable Banking transaction to an Expense, or raise NormalizationError."""
    if not isinstance(tx, Mapping):
        raise NormalizationError("transaction must be an object")
    if not account_id or len(account_id) > MAX_ID_LEN:
        raise NormalizationError("invalid account_id")

    entry_reference = tx.get("entry_reference")
    if not isinstance(entry_reference, str) or not entry_reference.strip():
        raise NormalizationError("missing entry_reference")
    entry_reference = entry_reference.strip()
    if len(entry_reference) > MAX_ID_LEN or not entry_reference.isprintable():
        raise NormalizationError("invalid entry_reference")

    status = tx.get("status")
    if status is not None and status != "BOOK":
        raise NormalizationError("only booked transactions are supported")

    indicator = tx.get("credit_debit_indicator")
    direction = _DIRECTIONS.get(indicator) if isinstance(indicator, str) else None
    if direction is None:
        raise NormalizationError("unknown credit_debit_indicator")

    amount, currency = _parse_amount(tx)
    booking_date = _parse_date(tx)

    remittance = _remittance(tx)
    payment_type, details = split_remittance(remittance)

    creditor = _name(tx.get("creditor"))
    debtor = _name(tx.get("debtor"))
    if direction is Direction.DEBIT and creditor:
        counterparty = creditor
    elif direction is Direction.CREDIT and debtor:
        counterparty = debtor
    else:
        counterparty = details or UNKNOWN_COUNTERPARTY

    return Expense(
        account_id=account_id,
        source_key=f"{account_id}{entry_reference}",
        booking_date=booking_date,
        amount=amount,
        currency=currency,
        direction=direction,
        counterparty=counterparty,
        payment_type=payment_type,
        description=clean_text(remittance, MAX_DESCRIPTION_LEN),
        category=None,
        raw=copy.deepcopy(dict(tx)),
        source=Source.ENABLEBANKING,
    )
