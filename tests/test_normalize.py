from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from core.errors import NormalizationError
from core.models import Direction, Expense, Source
from core.normalize import (
    MAX_COUNTERPARTY_LEN,
    MAX_DESCRIPTION_LEN,
    clean_text,
    normalize_transaction,
)


def test_card_payment(txs: dict[str, Any], account_id: str) -> None:
    e = normalize_transaction(txs["card_payment"], account_id)
    assert e.counterparty == "EXAMPLE SHOP"
    assert e.payment_type == "CARD PAYMENT FUEL"
    assert e.direction is Direction.DEBIT
    assert e.amount == Decimal("12.50")
    assert e.amount_str == "12.50"
    assert e.booking_date == date(2025, 3, 4)
    assert e.source is Source.ENABLEBANKING
    assert e.category is None
    assert e.source_key == account_id + "REF-0001"
    assert "  " not in e.description
    assert e.raw["entry_reference"] == "REF-0001"


def test_direct_debit_uses_details_segment(txs: dict[str, Any], account_id: str) -> None:
    e = normalize_transaction(txs["direct_debit"], account_id)
    assert e.counterparty == "0000000000 EXAMPLE UTILITY CONCEPT 1"
    assert e.payment_type == "DIRECT DEBIT UTILITY"
    assert e.amount == Decimal("80")


def test_credit_uses_debtor(txs: dict[str, Any], account_id: str) -> None:
    e = normalize_transaction(txs["credit"], account_id)
    assert e.counterparty == "EXAMPLE EMPLOYER"
    assert e.direction is Direction.CREDIT


def test_debit_with_both_names_creditor_wins(txs: dict[str, Any], account_id: str) -> None:
    assert normalize_transaction(txs["both_names"], account_id).counterparty == "EXAMPLE CREDITOR"


def test_credit_without_debtor_falls_back_to_details(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["credit"]
    tx["debtor"] = None
    assert normalize_transaction(tx, account_id).counterparty == "EXAMPLE EMPLOYER PAYROLL"


def test_no_separator_yields_unknown(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["direct_debit"]
    tx["remittance_information"] = ["JUST SOME TEXT"]
    e = normalize_transaction(tx, account_id)
    assert e.counterparty == "Unknown"
    assert e.payment_type == "JUST SOME TEXT"


def test_missing_remittance(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["direct_debit"]
    del tx["remittance_information"]
    e = normalize_transaction(tx, account_id)
    assert (e.description, e.payment_type, e.counterparty) == ("", "", "Unknown")


def test_multi_element_remittance_joined(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["direct_debit"]
    tx["remittance_information"] = ["A", "B", "C"]
    assert normalize_transaction(tx, account_id).counterparty == "C"


def test_value_date_fallback(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["card_payment"]
    tx["booking_date"] = None
    assert normalize_transaction(tx, account_id).booking_date == date(2025, 3, 3)


def test_same_day_duplicates_have_distinct_keys(txs: dict[str, Any], account_id: str) -> None:
    a = normalize_transaction(txs["same_day_a"], account_id)
    b = normalize_transaction(txs["same_day_b"], account_id)
    assert a.source_key != b.source_key


def test_text_is_bounded_and_cleaned(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["card_payment"]
    tx["creditor"] = {"name": "X" * 1000}
    tx["remittance_information"] = ["T\x00\x07 // S // " + "Y" * 2000]
    e = normalize_transaction(tx, account_id)
    assert len(e.counterparty) == MAX_COUNTERPARTY_LEN
    assert len(e.description) <= MAX_DESCRIPTION_LEN
    assert "\x00" not in e.description
    assert e.payment_type == "T"


def test_text_is_not_evaluated(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["card_payment"]
    tx["creditor"] = {"name": "__import__('os').system('x') {{7*7}}"}
    assert (
        normalize_transaction(tx, account_id).counterparty == "__import__('os').system('x') {{7*7}}"
    )


def test_clean_text_non_string() -> None:
    assert clean_text(123, 10) == ""


def test_raw_is_a_copy(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["card_payment"]
    e = normalize_transaction(tx, account_id)
    tx["creditor"]["name"] = "CHANGED"
    assert e.raw["creditor"]["name"] == "EXAMPLE SHOP"


def test_roundtrip_dict(txs: dict[str, Any], account_id: str) -> None:
    e = normalize_transaction(txs["card_payment"], account_id)
    assert Expense.from_dict(e.to_dict()) == e
    assert e.to_dict()["amount"] == "12.50"


def _mutate(txs: dict[str, Any], fn: Any) -> dict[str, Any]:
    tx = txs["card_payment"]
    fn(tx)
    return tx  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: t.pop("entry_reference"),
        lambda t: t.update(entry_reference="   "),
        lambda t: t.update(entry_reference=5),
        lambda t: t.update(entry_reference="A" * 500),
        lambda t: t.update(entry_reference="a\nb"),
        lambda t: t.update(credit_debit_indicator="XXXX"),
        lambda t: t.update(credit_debit_indicator=None),
        lambda t: t.update(status="PDNG"),
        lambda t: t.pop("transaction_amount"),
        lambda t: t["transaction_amount"].update(amount="abc"),
        lambda t: t["transaction_amount"].update(amount="-5.00"),
        lambda t: t["transaction_amount"].update(amount="0.00"),
        lambda t: t["transaction_amount"].update(amount="NaN"),
        lambda t: t["transaction_amount"].update(amount="1e5"),
        lambda t: t["transaction_amount"].update(amount="1" * 40),
        lambda t: t["transaction_amount"].update(amount=None),
        lambda t: t["transaction_amount"].update(amount=True),
        lambda t: t["transaction_amount"].update(amount=1.5),
        lambda t: t["transaction_amount"].update(currency="eur"),
        lambda t: t["transaction_amount"].update(currency=None),
        lambda t: t.update(booking_date="2025-13-45"),
        lambda t: t.update(booking_date=20250304),
        lambda t: t.update(booking_date=None, value_date=None),
        lambda t: t.update(remittance_information=[1, 2]),
        lambda t: t.update(remittance_information="plain string"),
    ],
)
def test_malformed_rejected(txs: dict[str, Any], account_id: str, mutation: Any) -> None:
    with pytest.raises(NormalizationError):
        normalize_transaction(_mutate(txs, mutation), account_id)


def test_non_mapping_rejected(account_id: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_transaction(["x"], account_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "a" * 200])
def test_bad_account_id(txs: dict[str, Any], bad: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_transaction(txs["card_payment"], bad)


def test_error_messages_do_not_leak_values(txs: dict[str, Any], account_id: str) -> None:
    tx = txs["card_payment"]
    tx["transaction_amount"]["amount"] = "SECRET-AMOUNT"
    with pytest.raises(NormalizationError) as exc:
        normalize_transaction(tx, account_id)
    assert "SECRET" not in str(exc.value)
