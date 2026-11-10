"""Lambda entry point for the bank consent flow.

Two ways in, told apart by event shape:
- an API Gateway HTTP event: only `GET /callback`, the public route the bank redirects to.
  Everything about it is untrusted; it never honours an `action` field.
- a direct invoke `{"action": "start"}`: needs lambda:InvokeFunction (the owner, via
  scripts/connect_bank.sh). There is deliberately no public "start" route: starting a consent
  from the internet would let anyone link their own bank account to this app.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from adapters.dynamo import DynamoConsentAttemptStore, DynamoSessionStore
from adapters.handlers import wiring
from adapters.safe_log import log
from core.consent import complete_consent, deny_consent, start_consent
from core.errors import BankError, ConsentCallbackError
from core.ports import ConsentAttemptStore, ConsentClient, SessionStore

CALLBACK_PATH = "/callback"
_MAX_PARAM_LEN = 600


@dataclass
class Deps:
    client: ConsentClient
    attempts: ConsentAttemptStore
    sessions: SessionStore
    redirect_url: str
    aspsp_name: str
    aspsp_country: str


def _default_deps() -> Deps:
    table = wiring.table()
    return Deps(
        wiring.bank(),
        DynamoConsentAttemptStore(table),
        DynamoSessionStore(table),
        os.environ["REDIRECT_URL"],
        os.environ.get("ASPSP_NAME", "BBVA"),
        os.environ.get("ASPSP_COUNTRY", "ES"),
    )


_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def _page(status: int, title: str, message: str) -> dict[str, Any]:
    # Fixed strings only: nothing from the request is ever echoed back.
    body = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        f"<title>{title}</title><h1>{title}</h1><p>{message}</p></html>"
    )
    return {"statusCode": status, "headers": dict(_HEADERS), "body": body}


def _param(params: dict[str, Any], name: str) -> str | None:
    value = params.get(name)
    if isinstance(value, str) and 0 < len(value) <= _MAX_PARAM_LEN:
        return value
    return None


def _callback(event: dict[str, Any], d: Deps, now: datetime) -> dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    state, code, error = _param(params, "state"), _param(params, "code"), _param(params, "error")
    if state is None or (code is None and error is None):
        log("consent_rejected", logging.WARNING, reason="bad_request")
        return _page(400, "Invalid request", "This link is not valid.")
    try:
        if error is not None:
            deny_consent(attempts=d.attempts, state=state, now=now)
            log("consent_denied", logging.WARNING)
            return _page(
                400, "Not connected", "The bank connection was cancelled. Nothing changed."
            )
        assert code is not None  # noqa: S101 - narrowed above
        session = complete_consent(
            client=d.client,
            attempts=d.attempts,
            sessions=d.sessions,
            provider=wiring.PROVIDER,
            state=state,
            code=code,
            now=now,
        )
    except ConsentCallbackError:
        log("consent_rejected", logging.WARNING, reason="state_or_code")
        return _page(400, "Invalid request", "This link is not valid or has expired.")
    except BankError as exc:
        log(
            "consent_failed",
            logging.ERROR,
            error_code=type(exc).__name__,
            reason=exc.code or "none",
        )
        return _page(
            502, "Not connected", "The bank connection could not be completed. Start again."
        )
    log("consent_connected", accounts=len(session.account_ids))
    return _page(200, "Connected", "Your bank is connected. You can close this tab.")


def _start(d: Deps, now: datetime) -> dict[str, str]:
    url = start_consent(
        client=d.client,
        attempts=d.attempts,
        aspsp_name=d.aspsp_name,
        country=d.aspsp_country,
        redirect_url=d.redirect_url,
        now=now,
    )
    log("consent_started")
    return {"url": url}


def handler(event: dict[str, Any], context: object, *, deps: Deps | None = None) -> Any:
    d = deps or _default_deps()
    now = datetime.now(UTC)
    try:
        http = (event.get("requestContext") or {}).get("http")
        if isinstance(http, dict):  # API Gateway: public, untrusted
            if http.get("method") == "GET" and event.get("rawPath") == CALLBACK_PATH:
                return _callback(event, d, now)
            return _page(404, "Not found", "Nothing here.")
        if event.get("action") == "start":  # direct invoke: IAM-authenticated owner
            return _start(d, now)
        raise ValueError("unsupported event")
    except Exception as exc:
        log("consent_handler_failed", logging.ERROR, error_code=type(exc).__name__)
        raise
