"""Start a bank consent and complete it from the callback.

Security: `state` is unguessable, stored server-side, expires quickly and is consumed
atomically, so a forged, replayed or late callback is rejected before any bank call.
The authorization `code` is never logged or stored.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from core.errors import ConsentCallbackError
from core.models import ConsentSession
from core.ports import ConsentAttemptStore, ConsentClient, SessionStore

STATE_TTL = timedelta(minutes=15)
MAX_VALIDITY = timedelta(days=90)
SAFETY_MARGIN = timedelta(hours=1)

_STATE_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
_CODE_RE = re.compile(r"[A-Za-z0-9._~-]{1,512}")


def start_consent(
    *,
    client: ConsentClient,
    attempts: ConsentAttemptStore,
    aspsp_name: str,
    country: str,
    redirect_url: str,
    now: datetime,
) -> str:
    """Returns the bank URL to open. The state is stored before the URL exists."""
    state = secrets.token_urlsafe(32)
    validity = min(client.max_consent_validity(aspsp_name, country), MAX_VALIDITY)
    attempts.create(state, now + STATE_TTL)
    return client.start_auth(
        aspsp_name, country, state, redirect_url, now + validity - SAFETY_MARGIN
    )


def _check_state(state: str) -> None:
    if not _STATE_RE.fullmatch(state):
        raise ConsentCallbackError("invalid state")


def complete_consent(
    *,
    client: ConsentClient,
    attempts: ConsentAttemptStore,
    sessions: SessionStore,
    provider: str,
    state: str,
    code: str,
    now: datetime,
) -> ConsentSession:
    """Verify and consume the state, exchange the code, store the new session."""
    _check_state(state)
    if not _CODE_RE.fullmatch(code):
        raise ConsentCallbackError("invalid code")
    if not attempts.consume(state, now):
        raise ConsentCallbackError("unknown, expired or used state")
    session_id, info = client.create_session(code)
    session = ConsentSession(
        session_id=session_id,
        provider=provider,
        account_ids=info.account_ids,
        created_at=info.created_at,
        expires_at=info.valid_until,
    )
    sessions.save(session)
    return session


def deny_consent(*, attempts: ConsentAttemptStore, state: str, now: datetime) -> None:
    """The user cancelled at the bank: burn the state so it cannot be reused."""
    _check_state(state)
    attempts.consume(state, now)
