"""One scheduled pull: resolve the consent session, fetch booked transactions, ingest.

Failure policy (no retry storm): consent problems mark the stored session and stop; rate
limits and transient bank errors stop the run. Nothing here retries; the next schedule
tick is the retry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from core.errors import BankUnavailableError, ConsentInvalidError, RateLimitedError
from core.ingest import ingest
from core.models import ConsentSession, SessionStatus
from core.ports import BankClient, ExpenseRepository, SessionStore

BOOKED = "BOOK"


class PullStatus(StrEnum):
    OK = "ok"
    CONSENT_INVALID = "consent_invalid"
    RATE_LIMITED = "rate_limited"
    BANK_UNAVAILABLE = "bank_unavailable"


@dataclass(frozen=True)
class PullResult:
    status: PullStatus
    accounts: int = 0
    inserted: int = 0
    skipped: int = 0  # already stored, or not booked
    days_left: int | None = None  # whole days until the consent expires (OK runs only)


def account_id_for(provider: str, account_uid: str) -> str:
    return f"{provider}:{account_uid}:"


def _resolve_session(
    *,
    provider: str,
    load_session_id: Callable[[], str],
    bank: BankClient,
    sessions: SessionStore,
    now: datetime,
) -> ConsentSession:
    """Newest active session in the store, else the bootstrap id parked in SSM."""
    active = sessions.get_active(provider, now)
    if active is not None:
        return active
    session_id = load_session_id()
    existing = sessions.get(session_id)
    if existing is not None:
        return existing  # known but no longer active: the caller reports consent_invalid
    info = bank.get_session(session_id)  # first sight of this id: register it
    session = ConsentSession(
        session_id=session_id,
        provider=provider,
        account_ids=info.account_ids,
        created_at=info.created_at,
        expires_at=info.valid_until,
    )
    sessions.save(session)
    return session


def pull(
    *,
    provider: str,
    load_session_id: Callable[[], str],
    bank: BankClient,
    repo: ExpenseRepository,
    sessions: SessionStore,
    now: datetime,
    date_from: date,
) -> PullResult:
    inserted = skipped = accounts = 0
    session: ConsentSession | None = None
    try:
        session = _resolve_session(
            provider=provider,
            load_session_id=load_session_id,
            bank=bank,
            sessions=sessions,
            now=now,
        )
        if not session.is_active(now):
            if session.status is SessionStatus.ACTIVE:
                sessions.mark_expired(session.session_id)
            return PullResult(PullStatus.CONSENT_INVALID)

        for uid in session.account_ids:
            booked = []
            total = 0
            for tx in bank.iter_transactions(uid, date_from):
                total += 1
                if tx.get("status") == BOOKED:
                    booked.append(tx)
            result = ingest(booked, account_id_for(provider, uid), repo)
            inserted += result.inserted
            skipped += result.skipped + (total - len(booked))
            accounts += 1
    except ConsentInvalidError as exc:
        if session is not None:
            if exc.reason == "expired":
                sessions.mark_expired(session.session_id)
            else:
                sessions.mark_revoked(session.session_id)
        return PullResult(PullStatus.CONSENT_INVALID, accounts, inserted, skipped)
    except RateLimitedError:
        return PullResult(PullStatus.RATE_LIMITED, accounts, inserted, skipped)
    except BankUnavailableError:
        return PullResult(PullStatus.BANK_UNAVAILABLE, accounts, inserted, skipped)
    days_left = max(0, (session.expires_at - now).days)
    return PullResult(PullStatus.OK, accounts, inserted, skipped, days_left)
