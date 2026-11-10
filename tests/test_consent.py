from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from core.consent import (
    MAX_VALIDITY,
    SAFETY_MARGIN,
    STATE_TTL,
    complete_consent,
    deny_consent,
    start_consent,
)
from core.errors import BankUnavailableError, ConsentCallbackError
from core.memory import InMemoryConsentAttemptStore, InMemorySessionStore
from core.models import BankSessionInfo, SessionStatus

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
REDIRECT = "https://example.invalid/callback"
CODE = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


class FakeConsentClient:
    def __init__(self, max_validity: timedelta = timedelta(days=180)) -> None:
        self.max_validity = max_validity
        self.started: list[dict[str, object]] = []
        self.exchanged: list[str] = []
        self.fail_exchange = False

    def max_consent_validity(self, aspsp_name: str, country: str) -> timedelta:
        return self.max_validity

    def start_auth(
        self, aspsp_name: str, country: str, state: str, redirect_url: str, valid_until: datetime
    ) -> str:
        self.started.append({"state": state, "redirect_url": redirect_url, "until": valid_until})
        return f"https://bank.invalid/approve?state={state}"

    def create_session(self, code: str) -> tuple[str, BankSessionInfo]:
        if self.fail_exchange:
            raise BankUnavailableError("down")
        self.exchanged.append(code)
        return "new-session-id", BankSessionInfo(("uid-1",), NOW, NOW + timedelta(days=89))


def begin(client: FakeConsentClient, attempts: InMemoryConsentAttemptStore) -> str:
    url = start_consent(
        client=client,
        attempts=attempts,
        aspsp_name="BBVA",
        country="ES",
        redirect_url=REDIRECT,
        now=NOW,
    )
    return parse_qs(urlparse(url).query)["state"][0]


def finish(
    client: FakeConsentClient,
    attempts: InMemoryConsentAttemptStore,
    sessions: InMemorySessionStore,
    state: str,
    code: str = CODE,
    now: datetime = NOW + timedelta(minutes=1),
):  # type: ignore[no-untyped-def]
    return complete_consent(
        client=client,
        attempts=attempts,
        sessions=sessions,
        provider="enablebanking",
        state=state,
        code=code,
        now=now,
    )


def test_states_are_unguessable_and_unique() -> None:
    client, attempts = FakeConsentClient(), InMemoryConsentAttemptStore()
    states = {begin(client, attempts) for _ in range(50)}
    assert len(states) == 50
    assert all(len(s) >= 32 for s in states)


def test_validity_is_capped_and_has_a_safety_margin() -> None:
    client, attempts = FakeConsentClient(timedelta(days=180)), InMemoryConsentAttemptStore()
    begin(client, attempts)
    assert client.started[0]["until"] == NOW + MAX_VALIDITY - SAFETY_MARGIN
    short = FakeConsentClient(timedelta(days=30))
    begin(short, attempts)
    assert short.started[0]["until"] == NOW + timedelta(days=30) - SAFETY_MARGIN


def test_happy_path_stores_an_active_session() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    session = finish(client, attempts, sessions, state)
    assert session.session_id == "new-session-id" and session.account_ids == ("uid-1",)
    active = sessions.get_active("enablebanking", NOW + timedelta(minutes=2))
    assert active == session and active.status is SessionStatus.ACTIVE


def test_state_is_single_use() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    finish(client, attempts, sessions, state)
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state)
    assert client.exchanged == [CODE]  # the replay never reached the bank


def test_unknown_state_is_rejected_before_any_bank_call() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, "a" * 43)
    assert client.exchanged == [] and sessions.list() == []


def test_expired_state_is_rejected() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state, now=NOW + STATE_TTL)
    assert client.exchanged == []


@pytest.mark.parametrize(
    "state", ["", "short", "x" * 200, "has spaces " * 4, "../../etc/passwd" * 3]
)
def test_malformed_state_is_rejected(state: str) -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state)


@pytest.mark.parametrize("code", ["", "x" * 600, "a b", "<script>", "a;b"])
def test_malformed_code_is_rejected_and_does_not_burn_the_state(code: str) -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state, code=code)
    assert finish(client, attempts, sessions, state).session_id == "new-session-id"


def test_failed_exchange_burns_the_state_and_stores_nothing() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    client.fail_exchange = True
    with pytest.raises(BankUnavailableError):
        finish(client, attempts, sessions, state)
    client.fail_exchange = False
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state)
    assert sessions.list() == []


def test_deny_burns_the_state() -> None:
    client, attempts, sessions = (
        FakeConsentClient(),
        InMemoryConsentAttemptStore(),
        InMemorySessionStore(),
    )
    state = begin(client, attempts)
    deny_consent(attempts=attempts, state=state, now=NOW)
    with pytest.raises(ConsentCallbackError):
        finish(client, attempts, sessions, state)
    with pytest.raises(ConsentCallbackError):
        deny_consent(attempts=attempts, state="bad", now=NOW)
