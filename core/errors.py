"""Core-level exceptions. Messages never include transaction text or amounts."""


class CoreError(Exception):
    """Base class for all errors raised by core."""


class NormalizationError(CoreError):
    """A provider transaction is malformed or unsupported."""


class NotFoundError(CoreError):
    """A requested record does not exist."""


class ValidationError(CoreError):
    """A domain value violates an invariant."""


class BankError(CoreError):
    """A bank API call failed. Messages carry no payload, only the error kind.

    `code` is the provider's enumerated error token (for example WRONG_TRANSACTIONS_PERIOD),
    empty when unknown. It is safe to log; response bodies are not.
    """

    def __init__(self, message: str = "", code: str = "") -> None:
        super().__init__(message)
        self.code = code


class ConsentInvalidError(BankError):
    """The bank session is expired, closed, revoked or unknown; the user must reconnect.

    `reason` is "expired" or "revoked" and decides how the stored session is marked.
    """

    def __init__(self, reason: str = "revoked") -> None:
        super().__init__(f"consent invalid: {reason}")
        self.reason = reason


class RateLimitedError(BankError):
    """The bank or Enable Banking rate limit was hit. Do not retry within this run."""


class BankUnavailableError(BankError):
    """Transient bank/API failure (5xx, timeout). The next scheduled run tries again."""


class ConsentCallbackError(CoreError):
    """The consent callback is invalid: bad shape, unknown, expired or already used state."""
