from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from adapters.dynamo import DynamoRefreshLimiter
from adapters.handlers.api import Deps, decode_id, encode_id, handler
from core.errors import BankError, ValidationError
from core.ingest import ingest
from core.memory import InMemoryExpenseRepository, InMemorySessionStore
from core.models import ConsentSession, SessionStatus

OWNER = "owner-sub-1"
ACCOUNT = "enablebanking:" + "a" * 32 + ":"
NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


class FakeInvoker:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.pulls, self.starts = error, 0, 0

    def start_consent(self) -> str:
        self.starts += 1
        if self.error:
            raise self.error
        return "https://bank.example/approve"

    def start_pull(self) -> None:
        self.pulls += 1


class FakeLimiter:
    def __init__(self, wait: int | None = None) -> None:
        self.wait = wait

    def try_acquire(self, now: datetime) -> int | None:
        return self.wait


def make_deps(txs: dict[str, Any], **kw: Any) -> Deps:
    repo = InMemoryExpenseRepository()
    ingest(txs.values(), ACCOUNT, repo)
    return Deps(
        repo=repo,
        sessions=kw.get("sessions", InMemorySessionStore()),
        limiter=kw.get("limiter", FakeLimiter()),
        invoker=kw.get("invoker", FakeInvoker()),
        owner_sub=kw.get("owner", OWNER),
        clock=lambda: datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
    )


def event(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: Any = None,
    sub: str | None = OWNER,
    kind: str = "access",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {"http": {"method": method}}
    if sub is not None:
        ctx["authorizer"] = {"jwt": {"claims": {"sub": sub, "token_use": kind}}}
    ev: dict[str, Any] = {"rawPath": path, "requestContext": ctx}
    if query is not None:
        ev["queryStringParameters"] = query
    if body is not None:
        ev["body"] = body if isinstance(body, str) else json.dumps(body)
    return ev


def call(d: Deps, *args: Any, **kw: Any) -> tuple[int, Any]:
    out = handler(event(*args, **kw), None, deps=d)
    return out["statusCode"], json.loads(out["body"]) if out["body"] else None


ROUTES = [
    ("GET", "/expenses"),
    ("GET", "/summary/monthly"),
    ("GET", "/connection"),
    ("POST", "/connection"),
    ("POST", "/refresh"),
    ("PUT", "/expenses/abc/category"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_rejects_missing_or_wrong_identity(
    txs: dict[str, Any], method: str, path: str
) -> None:
    d = make_deps(txs)
    assert call(d, method, path, sub=None)[0] == 401
    assert call(d, method, path, kind="id")[0] == 401
    status, body = call(d, method, path, sub="someone-else")
    assert (status, body) == (403, {"error": "forbidden"})
    assert d.invoker.pulls == 0 and d.invoker.starts == 0  # nothing ran for the intruder


def test_malformed_authorizer_context_is_unauthorized(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    for ctx in (
        {},
        {"authorizer": {}},
        {"authorizer": {"jwt": {"claims": "x"}}},
        {"authorizer": {"jwt": {"claims": {"sub": 5, "token_use": "access"}}}},
    ):
        out = handler(
            {"rawPath": "/expenses", "requestContext": {"http": {"method": "GET"}, **ctx}},
            None,
            deps=d,
        )
        assert out["statusCode"] == 401


def test_unknown_route_and_method(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    assert call(d, "GET", "/nope")[0] == 404
    assert call(d, "DELETE", "/expenses")[0] == 404
    assert (
        handler(
            {
                "requestContext": {
                    "http": {"method": "GET"},
                    "authorizer": {"jwt": {"claims": {"sub": OWNER, "token_use": "access"}}},
                }
            },
            None,
            deps=d,
        )["statusCode"]
        == 404
    )


def test_list_returns_safe_fields_and_total(txs: dict[str, Any]) -> None:
    status, body = call(make_deps(txs), "GET", "/expenses")
    assert status == 200 and body["total"] == 6 and len(body["items"]) == 6
    first = body["items"][0]
    assert set(first) == {
        "id",
        "booking_date",
        "amount",
        "currency",
        "direction",
        "counterparty",
        "payment_type",
        "description",
        "category",
        "source",
    }
    assert isinstance(first["amount"], str)
    assert ACCOUNT not in json.dumps(body)  # no account identifier leaves the API


def test_list_filters_and_pagination(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    _, all_rows = call(d, "GET", "/expenses")
    s, page = call(d, "GET", "/expenses", query={"limit": "2", "offset": "1"})
    assert s == 200 and page["total"] == 6
    assert [i["id"] for i in page["items"]] == [i["id"] for i in all_rows["items"][1:3]]
    _, credit = call(d, "GET", "/expenses", query={"direction": "credit"})
    assert credit["total"] == 1
    _, march = call(d, "GET", "/expenses", query={"from": "2025-03-06", "to": "2025-03-06"})
    assert march["total"] == 2
    _, unc = call(d, "GET", "/expenses", query={"uncategorized": "true"})
    assert unc["total"] == 6


@pytest.mark.parametrize(
    "query",
    [
        {"from": "2025-3-1"},
        {"from": "nope"},
        {"to": "2025-13-01"},
        {"from": "2025-04-01", "to": "2025-03-01"},
        {"limit": "0"},
        {"limit": "501"},
        {"limit": "-1"},
        {"limit": "1e2"},
        {"offset": "x"},
        {"direction": "sideways"},
        {"uncategorized": "yes"},
        {"category": "a\nb"},
        {"category": "x" * 65},
        {"category": "Fuel", "uncategorized": "true"},
        {"surprise": "1"},
        {"limit": "9" * 12},
    ],
)
def test_list_rejects_bad_input(txs: dict[str, Any], query: dict[str, str]) -> None:
    assert call(make_deps(txs), "GET", "/expenses", query=query) == (
        400,
        {"error": "invalid_request"},
    )


def test_set_category_round_trip(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    _, rows = call(d, "GET", "/expenses")
    item_id = rows["items"][0]["id"]
    s, updated = call(d, "PUT", f"/expenses/{item_id}/category", body={"category": " Fuel "})
    assert s == 200 and updated["category"] == "Fuel"
    _, fuel = call(d, "GET", "/expenses", query={"category": "Fuel"})
    assert [i["id"] for i in fuel["items"]] == [item_id]
    s, cleared = call(d, "PUT", f"/expenses/{item_id}/category", body={"category": None})
    assert s == 200 and cleared["category"] is None


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json",
        "[]",
        "{}",
        '{"category": 5}',
        '{"category": "x", "extra": 1}',
        '{"category": ""}',
        '{"category": "a\\nb"}',
        "x" * 2000,
    ],
)
def test_set_category_rejects_bad_bodies(txs: dict[str, Any], body: str) -> None:
    d = make_deps(txs)
    item_id = call(d, "GET", "/expenses")[1]["items"][0]["id"]
    assert call(d, "PUT", f"/expenses/{item_id}/category", body=body)[0] == 400


def test_set_category_rejects_base64_body_flag(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    item_id = call(d, "GET", "/expenses")[1]["items"][0]["id"]
    ev = event("PUT", f"/expenses/{item_id}/category", body={"category": "Fuel"})
    ev["isBase64Encoded"] = True
    assert handler(ev, None, deps=d)["statusCode"] == 400


def test_set_category_unknown_and_malformed_ids(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    ghost = encode_id(ACCOUNT + "missing")
    assert call(d, "PUT", f"/expenses/{ghost}/category", body={"category": "x"})[0] == 404
    for bad in ("%%%", "a" * 600, "***", "/"):
        assert call(d, "PUT", f"/expenses/{bad}/category", body={"category": "x"})[0] in (400, 404)
    assert call(d, "PUT", "/expenses/a/b/category", body={"category": "x"})[0] == 404


def test_id_codec() -> None:
    key = "enablebanking:abc:REF-1"
    assert decode_id(encode_id(key)) == key
    with pytest.raises(ValidationError):
        decode_id("$$")
    with pytest.raises(ValidationError):
        decode_id("_w")  # valid alphabet, invalid UTF-8


def test_monthly_summary(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    s, body = call(d, "GET", "/summary/monthly", query={"year": "2025"})
    assert s == 200 and body["items"]
    assert all(isinstance(r["total"], str) for r in body["items"])
    assert call(d, "GET", "/summary/monthly", query={"year": "2024"}) == (200, {"items": []})
    assert call(d, "GET", "/summary/monthly", query={"year": "1999"})[0] == 400
    assert call(d, "GET", "/summary/monthly", query={"year": "abc"})[0] == 400


def _session(days: int, status: SessionStatus = SessionStatus.ACTIVE) -> ConsentSession:
    return ConsentSession(
        "s1",
        "enablebanking",
        ("u",),
        NOW - timedelta(days=30),
        NOW + timedelta(days=days, hours=1),
        status,
    )


@pytest.mark.parametrize(("days", "soon"), [(60, False), (14, True), (3, True), (0, True)])
def test_connection_active(txs: dict[str, Any], days: int, soon: bool) -> None:
    sessions = InMemorySessionStore()
    sessions.save(_session(days))
    s, body = call(make_deps(txs, sessions=sessions), "GET", "/connection")
    assert s == 200 and body["status"] == "active"
    assert body["days_left"] == days and body["expiring_soon"] is soon
    assert "s1" not in json.dumps(body)  # the session id is never exposed


def test_connection_none_expired_and_revoked(txs: dict[str, Any]) -> None:
    empty = make_deps(txs)
    assert call(empty, "GET", "/connection")[1] == {
        "status": "none",
        "expires_at": None,
        "days_left": None,
        "expiring_soon": True,
    }
    sessions = InMemorySessionStore()
    sessions.save(_session(-2))  # past expiry, still marked active
    assert call(make_deps(txs, sessions=sessions), "GET", "/connection")[1]["status"] == "expired"
    sessions = InMemorySessionStore()
    sessions.save(_session(10, SessionStatus.REVOKED))
    body = call(make_deps(txs, sessions=sessions), "GET", "/connection")[1]
    assert body["status"] == "revoked" and body["expiring_soon"] is True


def test_start_connection(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    assert call(d, "POST", "/connection") == (200, {"url": "https://bank.example/approve"})
    failing = make_deps(txs, invoker=FakeInvoker(BankError("boom")))
    assert call(failing, "POST", "/connection") == (502, {"error": "bank_unavailable"})


def test_refresh_is_limited(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    assert call(d, "POST", "/refresh") == (202, None)
    assert d.invoker.pulls == 1
    limited = make_deps(txs, limiter=FakeLimiter(wait=120))
    out = handler(event("POST", "/refresh"), None, deps=limited)
    assert out["statusCode"] == 429 and out["headers"]["Retry-After"] == "120"
    assert limited.invoker.pulls == 0


def test_dynamo_limiter_allows_once_per_interval() -> None:
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        table = ddb.create_table(
            TableName="t",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
        )
        limiter = DynamoRefreshLimiter(table, interval_seconds=900)
        assert limiter.try_acquire(NOW) is None
        assert limiter.try_acquire(NOW + timedelta(seconds=100)) == 800
        assert limiter.try_acquire(NOW + timedelta(seconds=899)) == 1
        assert limiter.try_acquire(NOW + timedelta(seconds=901)) is None


def test_unexpected_error_is_a_plain_500(
    txs: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    class Boom:
        def list(self, **kwargs: Any) -> Any:
            raise RuntimeError("secret detail 12345")

    d = make_deps(txs)
    d.repo = Boom()
    with caplog.at_level(logging.INFO, logger="expenses"):
        out = handler(event("GET", "/expenses"), None, deps=d)
    assert out["statusCode"] == 500 and json.loads(out["body"]) == {"error": "internal_error"}
    assert "secret detail" not in caplog.text and "12345" not in caplog.text


def test_responses_are_not_cacheable_and_logs_hold_no_content(
    txs: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    d = make_deps(txs)
    with caplog.at_level(logging.INFO, logger="expenses"):
        out = handler(event("GET", "/expenses"), None, deps=d)
        handler(event("GET", "/expenses", sub="intruder"), None, deps=d)
    assert out["headers"]["Cache-Control"] == "no-store"
    assert out["headers"]["X-Content-Type-Options"] == "nosniff"
    rows = json.loads(out["body"])["items"]
    for r in rows:
        assert r["counterparty"] not in caplog.text and r["id"] not in caplog.text
    assert "intruder" not in caplog.text and OWNER not in caplog.text
    assert "api_denied" in caplog.text


@pytest.fixture(autouse=True)
def _no_aws_creds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    yield


def test_list_defaults_to_the_last_twelve_months(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    assert call(d, "GET", "/expenses")[1]["total"] == 6
    d.clock = lambda: datetime(2026, 6, 1, tzinfo=UTC)  # the 2025 rows fall out of the window
    assert call(d, "GET", "/expenses")[1]["total"] == 0
    explicit = call(d, "GET", "/expenses", query={"from": "2025-01-01", "to": "2025-12-31"})
    assert explicit[1]["total"] == 6


def test_list_with_only_an_old_to_date_still_has_a_window(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    expected = len(d.repo.list(date_to=date(2025, 3, 6)))
    assert 0 < expected < 6
    assert call(d, "GET", "/expenses", query={"to": "2025-03-06"})[1]["total"] == expected
    # `from` later than the defaulted `to` (today + 1) is rejected, not silently empty
    assert call(d, "GET", "/expenses", query={"from": "2030-01-01"})[0] == 400


def test_monthly_defaults_to_the_current_year(txs: dict[str, Any]) -> None:
    d = make_deps(txs)
    assert call(d, "GET", "/summary/monthly")[1]["items"]
    d.clock = lambda: datetime(2026, 6, 1, tzinfo=UTC)
    assert call(d, "GET", "/summary/monthly")[1] == {"items": []}


def test_unset_owner_matches_nobody(txs: dict[str, Any]) -> None:
    d = make_deps(txs, owner="")
    assert call(d, "GET", "/expenses", sub="")[0] == 403
    assert call(d, "GET", "/expenses")[0] == 403
