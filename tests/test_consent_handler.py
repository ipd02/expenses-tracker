from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from adapters.handlers.consent import Deps, handler
from core.errors import BankUnavailableError
from core.memory import InMemoryConsentAttemptStore, InMemorySessionStore
from core.models import BankSessionInfo

CODE = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
REDIRECT = "https://example.invalid/callback"


class Client:
    def __init__(self) -> None:
        self.fail = False

    def max_consent_validity(self, aspsp_name: str, country: str) -> timedelta:
        return timedelta(days=90)

    def start_auth(
        self, aspsp_name: str, country: str, state: str, redirect_url: str, valid_until: datetime
    ) -> str:
        assert redirect_url == REDIRECT
        return f"https://bank.invalid/approve?state={state}"

    def create_session(self, code: str) -> tuple[str, BankSessionInfo]:
        if self.fail:
            raise BankUnavailableError("down")
        now = datetime.now(UTC)
        return "sess-new", BankSessionInfo(("uid-1",), now, now + timedelta(days=89))


@pytest.fixture
def d() -> Deps:
    return Deps(
        Client(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
        REDIRECT,
        "BBVA",
        "ES",
    )


def http_event(path: str = "/callback", method: str = "GET", **params: str) -> dict[str, Any]:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": params or None,
    }


def start(d: Deps) -> str:
    out = handler({"action": "start"}, None, deps=d)
    return parse_qs(urlparse(out["url"]).query)["state"][0]


def test_start_returns_a_bank_url_with_a_stored_state(d: Deps) -> None:
    state = start(d)
    assert d.attempts.consume(state, datetime.now(UTC)) is True  # type: ignore[attr-defined]


def test_callback_connects_and_stores_the_session(d: Deps) -> None:
    state = start(d)
    out = handler(http_event(code=CODE, state=state), None, deps=d)
    assert out["statusCode"] == 200 and "Connected" in out["body"]
    stored = d.sessions.get("sess-new")
    assert stored is not None and stored.account_ids == ("uid-1",)


def test_replaying_the_callback_fails(d: Deps) -> None:
    state = start(d)
    assert handler(http_event(code=CODE, state=state), None, deps=d)["statusCode"] == 200
    assert handler(http_event(code=CODE, state=state), None, deps=d)["statusCode"] == 400


def test_forged_state_is_rejected_without_storing_anything(d: Deps) -> None:
    out = handler(http_event(code=CODE, state="a" * 43), None, deps=d)
    assert out["statusCode"] == 400 and d.sessions.list() == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"state": "a" * 43},
        {"code": CODE},
        {"state": "x" * 700, "code": CODE},
        {"state": "", "code": CODE},
    ],
)
def test_missing_or_oversized_params_are_a_400(d: Deps, params: dict[str, str]) -> None:
    assert handler(http_event(**params), None, deps=d)["statusCode"] == 400


def test_user_cancelling_burns_the_state(d: Deps) -> None:
    state = start(d)
    out = handler(http_event(error="access_denied", state=state), None, deps=d)
    assert out["statusCode"] == 400 and "cancelled" in out["body"]
    assert handler(http_event(code=CODE, state=state), None, deps=d)["statusCode"] == 400


def test_bank_failure_is_a_502_and_burns_the_state(d: Deps) -> None:
    state = start(d)
    d.client.fail = True  # type: ignore[attr-defined]
    assert handler(http_event(code=CODE, state=state), None, deps=d)["statusCode"] == 502
    d.client.fail = False  # type: ignore[attr-defined]
    assert handler(http_event(code=CODE, state=state), None, deps=d)["statusCode"] == 400
    assert d.sessions.list() == []


def test_http_requests_cannot_trigger_start(d: Deps) -> None:
    ev = http_event("/other", action="start")
    ev["action"] = "start"
    assert handler(ev, None, deps=d)["statusCode"] == 404
    ev2 = http_event(method="POST", code=CODE, state="a" * 43)
    ev2["action"] = "start"
    assert handler(ev2, None, deps=d)["statusCode"] == 404
    assert d.attempts._items == {}  # type: ignore[attr-defined]


def test_unknown_direct_invoke_raises(d: Deps) -> None:
    with pytest.raises(ValueError):
        handler({"action": "something-else"}, None, deps=d)
    with pytest.raises(ValueError):
        handler({}, None, deps=d)


def test_responses_have_hardening_headers_and_never_echo_input(d: Deps) -> None:
    out = handler(http_event(code="<script>alert(1)</script>", state="<b>x</b>"), None, deps=d)
    assert "<script>" not in out["body"] and "<b>" not in out["body"]
    h = out["headers"]
    assert h["Cache-Control"] == "no-store" and h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in h["Content-Security-Policy"]
    assert h["Referrer-Policy"] == "no-referrer"


def test_logs_never_contain_the_code_state_or_session(
    d: Deps, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        state = start(d)
        handler(http_event(code=CODE, state=state), None, deps=d)
        handler(http_event(code=CODE, state=state), None, deps=d)  # replay
    text = " ".join(r.getMessage() for r in caplog.records)
    assert text  # something was logged
    for needle in (CODE, state, "sess-new", "bank.invalid"):
        assert needle not in text
    events = [json.loads(r.getMessage())["event"] for r in caplog.records if r.name == "expenses"]
    assert events == ["consent_started", "consent_connected", "consent_rejected"]
