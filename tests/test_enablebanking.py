from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from adapters.enablebanking import BASE_URL, EnableBankingClient, sign_jwt
from core.errors import BankError, BankUnavailableError, ConsentInvalidError, RateLimitedError

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
UID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class FakeTransport:
    def __init__(self, *responses: tuple[int, Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, str]]] = []
        self.requests: list[tuple[str, str, bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        self.requests.append((method, url, body))
        status, body = self.responses.pop(0)
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def client(key: rsa.RSAPrivateKey, transport: FakeTransport) -> EnableBankingClient:
    return EnableBankingClient("app-id-1", pem(key), transport=transport, clock=lambda: NOW)


def test_jwt_has_expected_claims_and_valid_signature(key: rsa.RSAPrivateKey) -> None:
    token = sign_jwt(pem(key), "app-id-1", NOW)
    h, c, s = token.split(".")
    assert json.loads(unb64(h)) == {"typ": "JWT", "alg": "RS256", "kid": "app-id-1"}
    claims = json.loads(unb64(c))
    assert claims["iss"] == "enablebanking.com" and claims["aud"] == "api.enablebanking.com"
    assert claims["exp"] - claims["iat"] <= 24 * 3600
    key.public_key().verify(unb64(s), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_non_rsa_key_is_rejected() -> None:
    ec_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(BankError):
        sign_jwt(ec_pem, "app", NOW)


def test_get_session(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport(
        (
            200,
            {
                "status": "AUTHORIZED",
                "accounts": [UID],
                "created": "2026-10-01T10:00:00Z",
                "access": {"valid_until": "2027-01-01T10:00:00+00:00"},
            },
        )
    )
    info = client(key, t).get_session("sess-1")
    assert info.account_ids == (UID,)
    assert info.valid_until.year == 2027
    url, headers = t.calls[0]
    assert url == f"{BASE_URL}/sessions/sess-1"
    assert headers["Authorization"].startswith("Bearer ")


@pytest.mark.parametrize(("status", "reason"), [("EXPIRED", "expired"), ("REVOKED", "revoked")])
def test_non_authorized_session_is_consent_invalid(
    key: rsa.RSAPrivateKey, status: str, reason: str
) -> None:
    t = FakeTransport((200, {"status": status}))
    with pytest.raises(ConsentInvalidError) as exc:
        client(key, t).get_session("sess-1")
    assert exc.value.reason == reason


def test_transactions_follow_continuation_key(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport(
        (200, {"transactions": [{"entry_reference": "A"}], "continuation_key": "k1"}),
        (200, {"transactions": [{"entry_reference": "B"}], "continuation_key": None}),
    )
    got = list(client(key, t).iter_transactions(UID, date(2026, 10, 1)))
    assert [g["entry_reference"] for g in got] == ["A", "B"]
    assert "date_from=2026-10-01" in t.calls[0][0] and "continuation_key" not in t.calls[0][0]
    assert "continuation_key=k1" in t.calls[1][0]


def test_endless_paging_is_bounded(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport(*[(200, {"transactions": [], "continuation_key": "k"})] * 200)
    with pytest.raises(BankError):
        list(client(key, t).iter_transactions(UID, date(2026, 10, 1)))


def test_balances(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport((200, {"balances": [{"balance_type": "CLAV"}, "junk"]}))
    assert client(key, t).get_balances(UID) == [{"balance_type": "CLAV"}]


@pytest.mark.parametrize(
    ("status", "code", "exc_type", "reason"),
    [
        (422, "EXPIRED_SESSION", ConsentInvalidError, "expired"),
        (422, "REVOKED_SESSION", ConsentInvalidError, "revoked"),
        (422, "CLOSED_SESSION", ConsentInvalidError, "revoked"),
        (404, "SESSION_DOES_NOT_EXIST", ConsentInvalidError, "revoked"),
        (429, None, RateLimitedError, None),
        (400, "ASPSP_RATE_LIMIT_EXCEEDED", RateLimitedError, None),
        (503, None, BankUnavailableError, None),
        (408, "ASPSP_TIMEOUT", BankUnavailableError, None),
        (401, "UNAUTHORIZED_ACCESS", BankError, None),
        (422, "WRONG_TRANSACTIONS_PERIOD", BankError, None),
    ],
)
def test_error_mapping(
    key: rsa.RSAPrivateKey, status: int, code: str | None, exc_type: type[BankError], reason: str
) -> None:
    body: dict[str, Any] = {"message": "secret detail IBAN ES00", "error": code}
    t = FakeTransport((status, body))
    with pytest.raises(exc_type) as exc:
        client(key, t).get_balances(UID)
    assert type(exc.value) is exc_type
    assert "IBAN" not in str(exc.value) and "secret" not in str(exc.value)
    if reason:
        assert isinstance(exc.value, ConsentInvalidError) and exc.value.reason == reason


def test_unreadable_error_body_is_a_plain_bank_error(key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(BankError):
        client(key, FakeTransport((400, b"<html>"))).get_balances(UID)


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "x" * 200, "a b"])
def test_identifiers_are_validated(key: rsa.RSAPrivateKey, bad: str) -> None:
    t = FakeTransport()
    with pytest.raises(BankError):
        client(key, t).get_session(bad)
    assert t.calls == []


def test_unmapped_error_exposes_only_a_plain_code(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport((422, {"message": "IBAN ES00 leaked", "error": "WRONG_TRANSACTIONS_PERIOD"}))
    with pytest.raises(BankError) as exc:
        client(key, t).get_balances(UID)
    assert exc.value.code == "WRONG_TRANSACTIONS_PERIOD"
    assert str(exc.value) == "bank request failed (422 WRONG_TRANSACTIONS_PERIOD)"


@pytest.mark.parametrize("bad", ["lower case", "with spaces and 123", "X" * 100, "<script>"])
def test_non_token_error_codes_are_dropped(key: rsa.RSAPrivateKey, bad: str) -> None:
    t = FakeTransport((422, {"error": bad}))
    with pytest.raises(BankError) as exc:
        client(key, t).get_balances(UID)
    assert exc.value.code == "" and str(exc.value) == "bank request failed (422)"


def test_max_consent_validity(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport(
        (
            200,
            {
                "aspsps": [
                    {"name": "Other", "maximum_consent_validity": 100},
                    {"name": "BBVA", "maximum_consent_validity": 7776000},
                ]
            },
        )
    )
    assert client(key, t).max_consent_validity("BBVA", "ES").days == 90
    assert t.requests[0][0] == "GET" and "country=ES" in t.requests[0][1]


@pytest.mark.parametrize(
    "aspsps",
    [[], [{"name": "BBVA"}], [{"name": "BBVA", "maximum_consent_validity": 0}], ["junk"]],
)
def test_max_consent_validity_unknown_bank(key: rsa.RSAPrivateKey, aspsps: list[Any]) -> None:
    with pytest.raises(BankError):
        client(key, FakeTransport((200, {"aspsps": aspsps}))).max_consent_validity("BBVA", "ES")


def test_start_auth_posts_the_expected_body(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport((200, {"url": "https://bank.example/approve"}))
    url = client(key, t).start_auth("BBVA", "ES", "state-1", "https://cb.example/callback", NOW)
    assert url == "https://bank.example/approve"
    method, target, body = t.requests[0]
    assert method == "POST" and target == f"{BASE_URL}/auth"
    sent = json.loads(body or b"")
    assert sent["state"] == "state-1" and sent["redirect_url"] == "https://cb.example/callback"
    assert sent["aspsp"] == {"name": "BBVA", "country": "ES"} and sent["psu_type"] == "personal"
    assert sent["access"]["valid_until"] == NOW.isoformat()
    assert t.calls[0][1]["Content-Type"] == "application/json"


@pytest.mark.parametrize("body", [{}, {"url": 1}, {"url": "http://insecure.example"}])
def test_start_auth_rejects_bad_urls(key: rsa.RSAPrivateKey, body: dict[str, Any]) -> None:
    with pytest.raises(BankError):
        client(key, FakeTransport((200, body))).start_auth("BBVA", "ES", "s", "https://x", NOW)


def test_start_auth_validates_country(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport()
    with pytest.raises(BankError):
        client(key, t).start_auth("BBVA", "es/../x", "s", "https://x", NOW)
    assert t.requests == []


def test_create_session_accepts_account_objects(key: rsa.RSAPrivateKey) -> None:
    t = FakeTransport(
        (
            200,
            {
                "session_id": "sess-9",
                "accounts": [{"uid": UID, "iban": "ES00"}],
                "access": {"valid_until": "2027-02-01T10:00:00Z"},
            },
        )
    )
    session_id, info = client(key, t).create_session("the-code")
    assert session_id == "sess-9" and info.account_ids == (UID,) and info.created_at == NOW
    assert json.loads(t.requests[0][2] or b"") == {"code": "the-code"}


@pytest.mark.parametrize(
    "body",
    [
        {"accounts": [], "access": {"valid_until": "2027-02-01T10:00:00Z"}},
        {"session_id": "s", "accounts": "x", "access": {"valid_until": "2027-02-01T10:00:00Z"}},
        {
            "session_id": "s",
            "accounts": [{"no": "uid"}],
            "access": {"valid_until": "2027-02-01T10:00:00Z"},
        },
        {"session_id": "s", "accounts": [UID], "access": {"valid_until": "garbage"}},
        {
            "session_id": "../s",
            "accounts": [UID],
            "access": {"valid_until": "2027-02-01T10:00:00Z"},
        },
    ],
)
def test_create_session_rejects_bad_shapes(key: rsa.RSAPrivateKey, body: dict[str, Any]) -> None:
    with pytest.raises(BankError):
        client(key, FakeTransport((200, body))).create_session("c")


def test_account_key_is_a_stable_hash_of_the_identification(key: rsa.RSAPrivateKey) -> None:
    body = {"uid": UID, "identification_hash": "bank-ident-hash"}
    k1 = client(key, FakeTransport((200, body))).get_account_key(UID)
    k2 = client(key, FakeTransport((200, {**body, "uid": "other"}))).get_account_key("other-uid")
    assert k1 == k2 and len(k1) == 32 and "bank-ident" not in k1
    t = FakeTransport((200, body))
    client(key, t).get_account_key(UID)
    assert t.requests[0][1].endswith(f"/accounts/{UID}/details")


@pytest.mark.parametrize("body", [{}, {"identification_hash": ""}, {"identification_hash": 5}])
def test_account_key_rejects_bad_shapes(key: rsa.RSAPrivateKey, body: dict[str, Any]) -> None:
    with pytest.raises(BankError):
        client(key, FakeTransport((200, body))).get_account_key(UID)
