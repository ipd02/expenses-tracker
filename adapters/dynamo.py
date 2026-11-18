"""DynamoDB implementations (single-table design, see infra/dynamodb.tf).

Phase 3 needs only writes and key lookups for expenses. The listing, categorizing and
aggregating methods of ExpenseRepository arrive with the read API in phase 5.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from botocore.exceptions import ClientError

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

EXPENSE_PK = "ACCOUNT#"
SESSION_PK = "SESSION#"
CONSENT_PK = "CONSENT#"


def _split_source_key(source_key: str) -> tuple[str, str] | None:
    """source_key is `provider:uid:` + entry_reference; neither provider nor uid has a colon."""
    parts = source_key.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return f"{parts[0]}:{parts[1]}:", parts[2]


def _expense_item(expense: Expense) -> dict[str, Any]:
    ref = expense.source_key[len(expense.account_id) :]
    data = expense.to_dict()
    data["raw"] = json.dumps(data["raw"], sort_keys=True, default=str)
    item = {k: v for k, v in data.items() if v is not None}
    iso = expense.booking_date.isoformat()
    item.update(
        pk=f"{EXPENSE_PK}{expense.account_id}",
        sk=f"TXN#{iso}#{ref}",
        gsi1pk=f"MONTH#{expense.month}",
        gsi1sk=f"{iso}#{ref}",
    )
    return item


def _expense_from_item(item: dict[str, Any]) -> Expense:
    data = {k: v for k, v in item.items() if k not in {"pk", "sk", "gsi1pk", "gsi1sk"}}
    data["raw"] = json.loads(data.get("raw", "{}"))
    return Expense.from_dict(data)


class DynamoExpenseRepository:
    def __init__(self, table: Any) -> None:
        self._table = table

    def put_if_absent(self, expense: Expense) -> bool:
        try:
            self._table.put_item(
                Item=_expense_item(expense),
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def put_many_if_absent(self, expenses: Iterable[Expense]) -> int:
        # Conditional puts cannot be batched; volumes here are tens of rows per run.
        return sum(1 for e in expenses if self.put_if_absent(e))

    def get(self, source_key: str) -> Expense | None:
        split = _split_source_key(source_key)
        if split is None:
            return None
        account_id, _ = split
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :p)",
            "FilterExpression": "source_key = :k",
            "ExpressionAttributeValues": {
                ":pk": f"{EXPENSE_PK}{account_id}",
                ":p": "TXN#",
                ":k": source_key,
            },
        }
        while True:
            resp = self._table.query(**kwargs)
            if resp["Items"]:
                return _expense_from_item(resp["Items"][0])
            if "LastEvaluatedKey" not in resp:
                return None
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp["Items"])
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def _candidates(
        self, account_id: str | None, date_from: date | None, date_to: date | None
    ) -> list[Expense]:
        """Narrow the read as far as the key schema allows; callers still filter exactly."""
        lo = date_from.isoformat() if date_from else None
        hi = date_to.isoformat() if date_to else None
        if account_id is not None:
            items = self._query_all(
                KeyConditionExpression="pk = :pk AND sk BETWEEN :lo AND :hi",
                ExpressionAttributeValues={
                    ":pk": f"{EXPENSE_PK}{account_id}",
                    ":lo": f"TXN#{lo or ''}",
                    ":hi": f"TXN#{hi or '9999-99-99'}~",  # '~' sorts after any reference
                },
            )
        elif date_from is not None and date_to is not None:
            items = []
            for month in _months(date_from, date_to):
                items += self._query_all(
                    IndexName="gsi1",
                    KeyConditionExpression="gsi1pk = :m AND gsi1sk BETWEEN :lo AND :hi",
                    ExpressionAttributeValues={
                        ":m": f"MONTH#{month}",
                        ":lo": lo,
                        ":hi": f"{hi}~",
                    },
                )
        else:  # unbounded: a filtered scan is acceptable at personal-finance volumes
            items = []
            kwargs: dict[str, Any] = {
                "FilterExpression": "begins_with(pk, :p) AND begins_with(sk, :t)",
                "ExpressionAttributeValues": {":p": EXPENSE_PK, ":t": "TXN#"},
            }
            while True:
                resp = self._table.scan(**kwargs)
                items.extend(resp["Items"])
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [_expense_from_item(i) for i in items]

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
            self._candidates(account_id, date_from, date_to),
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            category=category,
            uncategorized=uncategorized,
            direction=direction,
        )

    def set_category(self, source_key: str, category: str | None) -> Expense:
        cleaned = validate_category(category)
        current = self.get(source_key)
        if current is None:
            raise NotFoundError("expense not found")
        ref = source_key[len(current.account_id) :]
        key = {
            "pk": f"{EXPENSE_PK}{current.account_id}",
            "sk": f"TXN#{current.booking_date.isoformat()}#{ref}",
        }
        names = {"#c": "category"}
        try:
            if cleaned is None:
                self._table.update_item(
                    Key=key,
                    UpdateExpression="REMOVE #c",
                    ConditionExpression="attribute_exists(pk)",
                    ExpressionAttributeNames=names,
                )
            else:
                self._table.update_item(
                    Key=key,
                    UpdateExpression="SET #c = :v",
                    ConditionExpression="attribute_exists(pk)",
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues={":v": cleaned},
                )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError("expense not found") from exc
            raise
        return replace(current, category=cleaned)

    def monthly_aggregates(
        self, *, year: int | None = None, account_id: str | None = None
    ) -> Sequence[MonthlyAggregate]:
        lo = date(year, 1, 1) if year else None
        hi = date(year, 12, 31) if year else None
        return aggregate_monthly(
            self._candidates(account_id, lo, hi), year=year, account_id=account_id
        )


def _months(start: date, end: date) -> list[str]:
    out: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _session_item(session: ConsentSession) -> dict[str, Any]:
    return {
        "pk": f"{SESSION_PK}{session.session_id}",
        "sk": "META",
        "provider": session.provider,
        "account_ids": list(session.account_ids),
        "account_keys": list(session.account_keys),
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "status": session.status.value,
        # gsi1 lists a provider's sessions (oldest first) without a table scan.
        "gsi1pk": f"SESSIONS#{session.provider}",
        "gsi1sk": session.created_at.isoformat(),
    }


def _session_from_item(item: dict[str, Any]) -> ConsentSession:
    return ConsentSession(
        session_id=item["pk"][len(SESSION_PK) :],
        provider=item["provider"],
        account_ids=tuple(item["account_ids"]),
        account_keys=tuple(item.get("account_keys", ())),
        created_at=datetime.fromisoformat(item["created_at"]),
        expires_at=datetime.fromisoformat(item["expires_at"]),
        status=SessionStatus(item["status"]),
    )


class DynamoSessionStore:
    def __init__(self, table: Any) -> None:
        self._table = table

    def save(self, session: ConsentSession) -> None:
        self._table.put_item(Item=_session_item(session))

    def get(self, session_id: str) -> ConsentSession | None:
        resp = self._table.get_item(Key={"pk": f"{SESSION_PK}{session_id}", "sk": "META"})
        item = resp.get("Item")
        return _session_from_item(item) if item else None

    def list(self, provider: str | None = None) -> Sequence[ConsentSession]:
        out: list[ConsentSession] = []
        if provider is not None:
            kwargs: dict[str, Any] = {
                "IndexName": "gsi1",
                "KeyConditionExpression": "gsi1pk = :p",
                "ExpressionAttributeValues": {":p": f"SESSIONS#{provider}"},
            }
            run = self._table.query
        else:  # only used by tooling: a handful of sessions, so a filtered scan is fine
            kwargs = {
                "FilterExpression": "begins_with(pk, :p) AND sk = :m",
                "ExpressionAttributeValues": {":p": SESSION_PK, ":m": "META"},
            }
            run = self._table.scan
        while True:
            resp = run(**kwargs)
            out.extend(_session_from_item(i) for i in resp["Items"])
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return sorted(out, key=lambda s: s.created_at)

    def get_active(self, provider: str, now: datetime) -> ConsentSession | None:
        active = [s for s in self.list(provider) if s.is_active(now)]
        return max(active, key=lambda s: s.expires_at, default=None)

    def _mark(self, session_id: str, status: SessionStatus) -> ConsentSession:
        try:
            self._table.update_item(
                Key={"pk": f"{SESSION_PK}{session_id}", "sk": "META"},
                UpdateExpression="SET #s = :s",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": status.value},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError("session not found") from exc
            raise
        session = self.get(session_id)
        if session is None:  # pragma: no cover - deleted between update and read
            raise NotFoundError("session not found")
        return session

    def mark_expired(self, session_id: str) -> ConsentSession:
        return self._mark(session_id, SessionStatus.EXPIRED)

    def mark_revoked(self, session_id: str) -> ConsentSession:
        return self._mark(session_id, SessionStatus.REVOKED)


class DynamoConsentAttemptStore:
    """In-flight consent `state` values. Single use is enforced by a conditional delete."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def create(self, state: str, expires_at: datetime) -> None:
        self._table.put_item(
            Item={
                "pk": f"{CONSENT_PK}{state}",
                "sk": "META",
                "expires_at": expires_at.isoformat(),
                "ttl": int(expires_at.timestamp()) + 3600,  # DynamoDB cleans up lazily
            }
        )

    def consume(self, state: str, now: datetime) -> bool:
        try:
            resp = self._table.delete_item(
                Key={"pk": f"{CONSENT_PK}{state}", "sk": "META"},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return now < datetime.fromisoformat(resp["Attributes"]["expires_at"])


class DynamoRefreshLimiter:
    """At most one manual refresh per interval, enforced with a conditional write."""

    def __init__(self, table: Any, interval_seconds: int = 900) -> None:
        self._table = table
        self._interval = interval_seconds
        self._key = {"pk": "RATE#refresh", "sk": "META"}

    def try_acquire(self, now: datetime) -> int | None:
        """None if granted, else the seconds to wait."""
        epoch = int(now.timestamp())
        try:
            self._table.put_item(
                Item={
                    **self._key,
                    "next_allowed": epoch + self._interval,
                    "ttl": epoch + self._interval + 3600,
                },
                ConditionExpression="attribute_not_exists(pk) OR next_allowed <= :now",
                ExpressionAttributeValues={":now": epoch},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            item = self._table.get_item(Key=self._key).get("Item") or {}
            return max(1, int(item.get("next_allowed", epoch + self._interval)) - epoch)
        return None
