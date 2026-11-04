"""DynamoDB implementations (single-table design, see infra/dynamodb.tf).

Phase 3 needs only writes and key lookups for expenses. The listing, categorizing and
aggregating methods of ExpenseRepository arrive with the read API in phase 5.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from core.errors import NotFoundError
from core.models import ConsentSession, Expense, SessionStatus

EXPENSE_PK = "ACCOUNT#"
SESSION_PK = "SESSION#"


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


def _session_item(session: ConsentSession) -> dict[str, Any]:
    return {
        "pk": f"{SESSION_PK}{session.session_id}",
        "sk": "META",
        "provider": session.provider,
        "account_ids": list(session.account_ids),
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "status": session.status.value,
    }


def _session_from_item(item: dict[str, Any]) -> ConsentSession:
    return ConsentSession(
        session_id=item["pk"][len(SESSION_PK) :],
        provider=item["provider"],
        account_ids=tuple(item["account_ids"]),
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
        kwargs: dict[str, Any] = {
            "FilterExpression": "begins_with(pk, :p) AND sk = :m",
            "ExpressionAttributeValues": {":p": SESSION_PK, ":m": "META"},
        }
        out: list[ConsentSession] = []
        while True:  # a handful of sessions: a filtered scan is fine
            resp = self._table.scan(**kwargs)
            out.extend(_session_from_item(i) for i in resp["Items"])
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        out = [s for s in out if provider is None or s.provider == provider]
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
