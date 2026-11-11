"""Enable Banking client (read-only): JWT signing, session, transactions, balances.

Errors are mapped to core exceptions using only the HTTP status and the `error` code from the
response. Response bodies and URLs are never put into exception messages or logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core.errors import BankError, BankUnavailableError, ConsentInvalidError, RateLimitedError
from core.models import BankSessionInfo

BASE_URL = "https://api.enablebanking.com"
JWT_TTL = timedelta(minutes=30)  # the API allows up to 24 h; short-lived is safer
TIMEOUT_SECONDS = 10.0
MAX_PAGES = 100
MAX_RESPONSE_BYTES = 5_000_000

_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_CODE_RE = re.compile(r"[A-Z_]{1,64}")
_EXPIRED_CODES = {"EXPIRED_SESSION"}
_REVOKED_CODES = {"CLOSED_SESSION", "REVOKED_SESSION", "SESSION_DOES_NOT_EXIST"}
_UNAVAILABLE_CODES = {"ASPSP_TIMEOUT", "ASPSP_ERROR"}
_BAD_SESSION_STATUSES = {"EXPIRED": "expired"}  # anything else not AUTHORIZED counts as revoked


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]: ...


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None = None
) -> tuple[int, bytes]:
    if not url.startswith(BASE_URL + "/"):
        raise BankError("refusing request outside the Enable Banking API")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return resp.status, resp.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BankUnavailableError("network error") from exc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_jwt(private_key_pem: bytes, app_id: str, now: datetime) -> str:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise BankError("application key must be RSA")
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    claims = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": int(now.timestamp()),
        "exp": int((now + JWT_TTL).timestamp()),
    }
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def _check_id(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise BankError("invalid identifier")
    return value


_COUNTRY_RE = re.compile(r"[A-Z]{2}")


def _account_uid(account: object) -> str:
    """POST /sessions returns account objects; GET /sessions returns bare uids."""
    uid = account.get("uid") if isinstance(account, Mapping) else account
    if not isinstance(uid, str):
        raise BankError("unexpected response shape")
    return _check_id(uid)


def account_key_from_identification(identification_hash: str) -> str:
    """Hash again so the bank's identifier never lands in a storage key or a log line."""
    return hashlib.sha256(identification_hash.encode()).hexdigest()[:32]


def _check_country(value: str) -> str:
    if not _COUNTRY_RE.fullmatch(value):
        raise BankError("invalid country")
    return value


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise BankError("unexpected response shape")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BankError("unexpected response shape") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key_pem: bytes,
        *,
        transport: Transport = urllib_transport,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._app_id = app_id
        self._key = private_key_pem
        self._transport = transport
        self._clock = clock

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params, None)

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, None, payload)

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        token = sign_jwt(self._key, self._app_id, self._clock())
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        status, body = self._transport(method, url, headers, data)
        if status in (200, 201):
            try:
                data = json.loads(body)
            except ValueError as exc:
                raise BankError("unreadable response") from exc
            if not isinstance(data, dict):
                raise BankError("unexpected response shape")
            return data
        raise _map_error(status, body)

    def get_session(self, session_id: str) -> BankSessionInfo:
        data = self._get(f"/sessions/{_check_id(session_id)}")
        status = data.get("status")
        if status != "AUTHORIZED":
            raise ConsentInvalidError(_BAD_SESSION_STATUSES.get(str(status), "revoked"))
        access = data.get("access")
        accounts = data.get("accounts")
        if not isinstance(access, Mapping) or not isinstance(accounts, list):
            raise BankError("unexpected response shape")
        uids = tuple(_check_id(a) for a in accounts if isinstance(a, str))
        created = _parse_time(data.get("created"))
        return BankSessionInfo(uids, created, _parse_time(access.get("valid_until")))

    def iter_transactions(self, account_uid: str, date_from: date) -> Iterator[Mapping[str, Any]]:
        path = f"/accounts/{_check_id(account_uid)}/transactions"
        params = {"date_from": date_from.isoformat()}
        for _ in range(MAX_PAGES):
            data = self._get(path, params)
            txs = data.get("transactions")
            if not isinstance(txs, list):
                raise BankError("unexpected response shape")
            for tx in txs:
                if isinstance(tx, dict):
                    yield tx
            key = data.get("continuation_key")
            if not key:
                return
            if not isinstance(key, str):
                raise BankError("unexpected response shape")
            params = {"date_from": date_from.isoformat(), "continuation_key": key}
        raise BankError("too many pages")

    def get_balances(self, account_uid: str) -> Sequence[Mapping[str, Any]]:
        data = self._get(f"/accounts/{_check_id(account_uid)}/balances")
        balances = data.get("balances")
        if not isinstance(balances, list):
            raise BankError("unexpected response shape")
        return [b for b in balances if isinstance(b, dict)]

    def get_account_key(self, account_uid: str) -> str:
        data = self._get(f"/accounts/{_check_id(account_uid)}/details")
        ident = data.get("identification_hash")
        if not isinstance(ident, str) or not ident or len(ident) > 1024:
            raise BankError("unexpected response shape")
        return account_key_from_identification(ident)

    def max_consent_validity(self, aspsp_name: str, country: str) -> timedelta:
        data = self._get("/aspsps", {"country": _check_country(country)})
        for aspsp in data.get("aspsps", []):
            if isinstance(aspsp, Mapping) and aspsp.get("name") == aspsp_name:
                seconds = aspsp.get("maximum_consent_validity")
                if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
                    return timedelta(seconds=seconds)
                break
        raise BankError("bank not found or has no consent validity")

    def start_auth(
        self, aspsp_name: str, country: str, state: str, redirect_url: str, valid_until: datetime
    ) -> str:
        data = self._post(
            "/auth",
            {
                "access": {"valid_until": valid_until.isoformat()},
                "aspsp": {"name": aspsp_name, "country": _check_country(country)},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": "personal",
            },
        )
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise BankError("unexpected response shape")
        return url

    def create_session(self, code: str) -> tuple[str, BankSessionInfo]:
        data = self._post("/sessions", {"code": code})
        session_id = data.get("session_id")
        access = data.get("access")
        accounts = data.get("accounts")
        if not isinstance(session_id, str) or not isinstance(access, Mapping):
            raise BankError("unexpected response shape")
        if not isinstance(accounts, list):
            raise BankError("unexpected response shape")
        uids = tuple(_account_uid(a) for a in accounts)
        info = BankSessionInfo(uids, self._clock(), _parse_time(access.get("valid_until")))
        return _check_id(session_id), info


def _map_error(status: int, body: bytes) -> BankError:
    code = ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            code = parsed["error"]
    except ValueError:
        pass
    if not _CODE_RE.fullmatch(code):
        code = ""  # only plain enumerated tokens are ever surfaced
    if code in _EXPIRED_CODES:
        return ConsentInvalidError("expired")
    if code in _REVOKED_CODES:
        return ConsentInvalidError("revoked")
    if status == 429 or code == "ASPSP_RATE_LIMIT_EXCEEDED":
        return RateLimitedError("rate limited")
    if status in (408, 500, 502, 503, 504) or code in _UNAVAILABLE_CODES:
        return BankUnavailableError(f"bank unavailable ({status})")
    # Anything else (bad JWT, unauthorized IP, wrong params) is a configuration/code bug:
    # fail loudly so the Lambda error alarm fires, without echoing the body.
    detail = f"{status} {code}" if code else str(status)
    return BankError(f"bank request failed ({detail})", code)
