from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from adapters.dynamo import DynamoExpenseRepository, DynamoSessionStore
from core.errors import NotFoundError
from core.models import ConsentSession, SessionStatus
from core.normalize import normalize_transaction

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def table() -> Iterator[Any]:
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        yield ddb.create_table(
            TableName="expenses-tracker",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "gsi1pk", "AttributeType": "S"},
                {"AttributeName": "gsi1sk", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi1",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )


def test_put_if_absent_is_idempotent_and_round_trips(
    table: Any, txs: dict[str, Any], account_id: str
) -> None:
    repo = DynamoExpenseRepository(table)
    expense = normalize_transaction(txs["card_payment"], account_id)
    assert repo.put_if_absent(expense) is True
    assert repo.put_if_absent(expense) is False
    assert repo.get(expense.source_key) == expense
    got = repo.get(expense.source_key)
    assert got is not None and dict(got.raw) == dict(expense.raw)


def test_put_many_counts_only_new(table: Any, txs: dict[str, Any], account_id: str) -> None:
    repo = DynamoExpenseRepository(table)
    items = [normalize_transaction(t, account_id) for t in txs.values()]
    assert repo.put_many_if_absent(items) == len(items)
    assert repo.put_many_if_absent(items) == 0


def test_get_missing_or_malformed_key(table: Any, account_id: str) -> None:
    repo = DynamoExpenseRepository(table)
    assert repo.get(account_id + "NOPE") is None
    assert repo.get("garbage") is None


def test_gsi_key_uses_month(table: Any, txs: dict[str, Any], account_id: str) -> None:
    repo = DynamoExpenseRepository(table)
    expense = normalize_transaction(txs["card_payment"], account_id)
    repo.put_if_absent(expense)
    resp = table.query(
        IndexName="gsi1",
        KeyConditionExpression="gsi1pk = :m",
        ExpressionAttributeValues={":m": f"MONTH#{expense.month}"},
    )
    assert len(resp["Items"]) == 1


def make_session(
    sid: str = "s1", days: int = 60, status: SessionStatus = SessionStatus.ACTIVE
) -> ConsentSession:
    return ConsentSession(
        session_id=sid,
        provider="enablebanking",
        account_ids=("acc-1", "acc-2"),
        created_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=days),
        status=status,
    )


def test_session_store_round_trip_and_active(table: Any) -> None:
    store = DynamoSessionStore(table)
    store.save(make_session("s1", 30))
    store.save(make_session("s2", 60))
    store.save(make_session("s3", 90, SessionStatus.REVOKED))
    assert store.get("s1") == make_session("s1", 30)
    assert store.get("missing") is None
    active = store.get_active("enablebanking", NOW)
    assert active is not None and active.session_id == "s2"
    assert store.get_active("other", NOW) is None
    assert [s.session_id for s in store.list("enablebanking")] == ["s1", "s2", "s3"]
    assert store.list("other") == []


def test_mark_expired_and_revoked(table: Any) -> None:
    store = DynamoSessionStore(table)
    store.save(make_session("s1"))
    assert store.mark_expired("s1").status is SessionStatus.EXPIRED
    assert store.mark_revoked("s1").status is SessionStatus.REVOKED
    assert store.get_active("enablebanking", NOW) is None
    with pytest.raises(NotFoundError):
        store.mark_expired("nope")
