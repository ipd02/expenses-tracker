"""Ingest a batch of provider transactions into a repository."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from core.normalize import normalize_transaction
from core.ports import ExpenseRepository


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int


def ingest(
    transactions: Iterable[Mapping[str, Any]], account_id: str, repo: ExpenseRepository
) -> IngestResult:
    """Normalize then store. Idempotent: existing source_keys are skipped.

    The whole batch is normalized before anything is written, so a malformed
    transaction raises NormalizationError and leaves the repository untouched.
    """
    expenses = [normalize_transaction(tx, account_id) for tx in transactions]
    inserted = repo.put_many_if_absent(expenses)
    return IngestResult(inserted=inserted, skipped=len(expenses) - inserted)
