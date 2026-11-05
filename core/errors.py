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
    """A bank API call failed. Messages carry no payload, only the error kind."""


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
