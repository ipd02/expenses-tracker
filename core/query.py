"""Filtering and aggregation shared by every ExpenseRepository, so they behave identically."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal

from core.models import Direction, Expense, MonthlyAggregate


def filter_expenses(
    expenses: Iterable[Expense],
    *,
    account_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    category: str | None = None,
    uncategorized: bool = False,
    direction: Direction | None = None,
) -> list[Expense]:
    """Dates inclusive. Newest first, ties ordered by source_key."""
    out = [
        e
        for e in expenses
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


def aggregate_monthly(
    expenses: Iterable[Expense], *, year: int | None = None, account_id: str | None = None
) -> Sequence[MonthlyAggregate]:
    totals: dict[tuple[str, str | None, Direction, str], Decimal] = defaultdict(Decimal)
    counts: dict[tuple[str, str | None, Direction, str], int] = defaultdict(int)
    for e in expenses:
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
