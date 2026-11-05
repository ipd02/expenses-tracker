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


def account_id_for(provider: str, account_uid: str) -> str:
    return f"{provider}:{account_uid}:"


def _resolve_session(
    *,
    provider: str,
    session_id: str,
    bank: BankClient,
    sessions: SessionStore,
) -> ConsentSession:
    existing = sessions.get(session_id)
    if existing is not None:
        return existing
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
    session_id = load_session_id()
    try:
        session = _resolve_session(
            provider=provider, session_id=session_id, bank=bank, sessions=sessions
        )
        if not session.is_active(now):
            if session.status is SessionStatus.ACTIVE:
                sessions.mark_expired(session_id)
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
        if sessions.get(session_id) is not None:
            if exc.reason == "expired":
                sessions.mark_expired(session_id)
            else:
                sessions.mark_revoked(session_id)
        return PullResult(PullStatus.CONSENT_INVALID, accounts, inserted, skipped)
    except RateLimitedError:
        return PullResult(PullStatus.RATE_LIMITED, accounts, inserted, skipped)
    except BankUnavailableError:
        return PullResult(PullStatus.BANK_UNAVAILABLE, accounts, inserted, skipped)
    return PullResult(PullStatus.OK, accounts, inserted, skipped)
