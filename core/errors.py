"""Core-level exceptions. Messages never include transaction text or amounts."""


class CoreError(Exception):
    """Base class for all errors raised by core."""


class NormalizationError(CoreError):
    """A provider transaction is malformed or unsupported."""


class NotFoundError(CoreError):
    """A requested record does not exist."""


class ValidationError(CoreError):
    """A domain value violates an invariant."""
