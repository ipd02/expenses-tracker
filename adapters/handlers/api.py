"""Lambda entry point for the owner's read API (HTTP API v2 payload, Cognito JWT authorizer).

The gateway authorizer already rejects missing or invalid tokens. This handler re-checks the
claims anyway (deny by default) and only serves the single configured owner `sub`.
Responses never include raw bank payloads, account identifiers or session ids.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from adapters.dynamo import DynamoExpenseRepository, DynamoRefreshLimiter, DynamoSessionStore
from adapters.handlers import wiring
from adapters.lambda_invoker import LambdaInvoker
from adapters.safe_log import log
from core.errors import BankError, NotFoundError, ValidationError
from core.models import Direction, Expense, SessionStatus, validate_category

MAX_LIMIT = 500
DEFAULT_LIMIT = 100
MAX_BODY = 1024
EXPIRING_DAYS = 14
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,512}")
_CATEGORY_PATH = re.compile(r"/expenses/([^/]+)/category")
_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


@dataclass
class Deps:
    repo: Any
    sessions: Any
    limiter: Any
    invoker: Any
    owner_sub: str
    provider: str = wiring.PROVIDER


def _default_deps() -> Deps:
    import boto3

    table = wiring.table()
    return Deps(
        DynamoExpenseRepository(table),
        DynamoSessionStore(table),
        DynamoRefreshLimiter(table),
        LambdaInvoker(
            boto3.client("lambda"),
            os.environ["CONSENT_FUNCTION"],
            os.environ["PULL_FUNCTION"],
        ),
        os.environ["OWNER_SUB"],
    )


def _resp(status: int, body: Any = None, extra: dict[str, str] | None = None) -> dict[str, Any]:
    headers = {**_HEADERS, **(extra or {})}
    return {"statusCode": status, "headers": headers, "body": json.dumps(body) if body else ""}


def _error(status: int, code: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
    return _resp(status, {"error": code}, extra)


def encode_id(source_key: str) -> str:
    return base64.urlsafe_b64encode(source_key.encode()).decode().rstrip("=")


def decode_id(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValidationError("invalid id")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("invalid id") from exc


def _expense_json(e: Expense) -> dict[str, Any]:
    return {
        "id": encode_id(e.source_key),
        "booking_date": e.booking_date.isoformat(),
        "amount": e.amount_str,
        "currency": e.currency,
        "direction": e.direction.value,
        "counterparty": e.counterparty,
        "payment_type": e.payment_type,
        "description": e.description,
        "category": e.category,
        "source": e.source.value,
    }


def _authorize(event: dict[str, Any], d: Deps) -> dict[str, Any] | None:
    """None when the caller is the owner, else the error response."""
    ctx = event.get("requestContext") or {}
    jwt = (ctx.get("authorizer") or {}).get("jwt") or {}
    claims = jwt.get("claims") if isinstance(jwt, dict) else None
    if not isinstance(claims, dict) or not isinstance(claims.get("sub"), str):
        return _error(401, "unauthorized")
    if claims.get("token_use") != "access":
        return _error(401, "unauthorized")
    if claims["sub"] != d.owner_sub:
        log("api_denied", logging.WARNING, reason="not_owner")
        return _error(403, "forbidden")
    return None


def _date(value: str) -> date:
    if len(value) != 10:
        raise ValidationError("invalid date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("invalid date") from exc


def _int(value: str, low: int, high: int | None) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 9:
        raise ValidationError("invalid integer")
    n = int(value)
    if n < low or (high is not None and n > high):
        raise ValidationError("integer out of range")
    return n


def _params(event: dict[str, Any], allowed: set[str]) -> dict[str, str]:
    params = event.get("queryStringParameters") or {}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValidationError("unknown parameter")
    if any(not isinstance(v, str) or len(v) > 200 for v in params.values()):
        raise ValidationError("invalid parameter")
    return dict(params)


def _list(event: dict[str, Any], d: Deps) -> dict[str, Any]:
    p = _params(event, {"from", "to", "category", "uncategorized", "direction", "limit", "offset"})
    date_from = _date(p["from"]) if "from" in p else None
    date_to = _date(p["to"]) if "to" in p else None
    if date_from and date_to and date_from > date_to:
        raise ValidationError("from after to")
    category = validate_category(p["category"]) if "category" in p else None
    if p.get("uncategorized", "false") not in ("true", "false"):
        raise ValidationError("invalid boolean")
    uncategorized = p.get("uncategorized") == "true"
    if uncategorized and category is not None:
        raise ValidationError("category and uncategorized conflict")
    if p.get("direction", "debit") not in {d.value for d in Direction}:
        raise ValidationError("invalid direction")
    direction = Direction(p["direction"]) if "direction" in p else None
    limit = _int(p["limit"], 1, MAX_LIMIT) if "limit" in p else DEFAULT_LIMIT
    offset = _int(p["offset"], 0, None) if "offset" in p else 0
    rows = d.repo.list(
        date_from=date_from,
        date_to=date_to,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
    )
    page = rows[offset : offset + limit]
    return _resp(200, {"items": [_expense_json(e) for e in page], "total": len(rows)})


def _set_category(event: dict[str, Any], raw_id: str, d: Deps) -> dict[str, Any]:
    source_key = decode_id(raw_id)
    body = event.get("body") or ""
    if event.get("isBase64Encoded") or not isinstance(body, str) or len(body) > MAX_BODY:
        raise ValidationError("invalid body")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValidationError("invalid body") from exc
    if not isinstance(data, dict) or set(data) != {"category"}:
        raise ValidationError("invalid body")
    category = data["category"]
    if category is not None and not isinstance(category, str):
        raise ValidationError("invalid category")
    return _resp(200, _expense_json(d.repo.set_category(source_key, category)))


def _monthly(event: dict[str, Any], d: Deps) -> dict[str, Any]:
    p = _params(event, {"year"})
    year = _int(p["year"], 2000, 2100) if "year" in p else None
    rows = d.repo.monthly_aggregates(year=year)
    items = [
        {
            "month": r.month,
            "category": r.category,
            "direction": r.direction.value,
            "currency": r.currency,
            "total": format(r.total, "f"),
            "count": r.count,
        }
        for r in rows
    ]
    return _resp(200, {"items": items})


def _connection(d: Deps, now: datetime) -> dict[str, Any]:
    active = d.sessions.get_active(d.provider, now)
    if active is not None:
        days = max(0, (active.expires_at - now).days)
        return _resp(
            200,
            {
                "status": SessionStatus.ACTIVE.value,
                "expires_at": active.expires_at.isoformat(),
                "days_left": days,
                "expiring_soon": days <= EXPIRING_DAYS,
            },
        )
    known = d.sessions.list(d.provider)
    status = known[-1].status.value if known else "none"
    if known and known[-1].status is SessionStatus.ACTIVE:
        status = SessionStatus.EXPIRED.value  # past its expiry but not yet marked by a pull
    expires = known[-1].expires_at.isoformat() if known else None
    return _resp(
        200, {"status": status, "expires_at": expires, "days_left": None, "expiring_soon": True}
    )


def _refresh(d: Deps, now: datetime) -> dict[str, Any]:
    wait = d.limiter.try_acquire(now)
    if wait is not None:
        return _error(429, "too_many_requests", {"Retry-After": str(wait)})
    d.invoker.start_pull()
    return _resp(202)


def _route(event: dict[str, Any], d: Deps, now: datetime) -> dict[str, Any]:
    http = (event.get("requestContext") or {}).get("http") or {}
    method, path = http.get("method"), event.get("rawPath")
    if not isinstance(path, str):
        return _error(404, "not_found")
    if method == "GET" and path == "/expenses":
        return _list(event, d)
    if method == "GET" and path == "/summary/monthly":
        return _monthly(event, d)
    if method == "GET" and path == "/connection":
        return _connection(d, now)
    if method == "POST" and path == "/connection":
        return _resp(200, {"url": d.invoker.start_consent()})
    if method == "POST" and path == "/refresh":
        return _refresh(d, now)
    match = _CATEGORY_PATH.fullmatch(path)
    if method == "PUT" and match:
        return _set_category(event, match.group(1), d)
    return _error(404, "not_found")


def handler(event: dict[str, Any], context: object, *, deps: Deps | None = None) -> dict[str, Any]:
    d = deps or _default_deps()
    denied = _authorize(event, d)
    if denied is not None:
        return denied
    try:
        out = _route(event, d, datetime.now(UTC))
    except ValidationError:
        out = _error(400, "invalid_request")
    except NotFoundError:
        out = _error(404, "not_found")
    except BankError:
        out = _error(502, "bank_unavailable")
    except Exception as exc:
        log("api_failed", logging.ERROR, error_code=type(exc).__name__)
        out = _error(500, "internal_error")
    log("api_request", http_status=out["statusCode"])
    return out
