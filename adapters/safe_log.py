"""Structured logging with an allowlist, so sensitive values cannot be logged by accident.

Only counters and short code-like tokens are accepted. Anything else (amounts, counterparties,
tokens, keys, session ids, free text) is rejected at the call site by design.
"""

from __future__ import annotations

import json
import logging
import re

_LOGGER = logging.getLogger("expenses")
_LOGGER.setLevel(logging.INFO)  # Lambda's root default (WARNING) would drop run summaries
ALLOWED_FIELDS = frozenset(
    {"status", "accounts", "inserted", "skipped", "http_status", "error_code", "reason", "pages"}
)
_TOKEN_RE = re.compile(r"[A-Za-z_]{1,64}")


def log(event: str, level: int = logging.INFO, **fields: int | str) -> None:
    if not _TOKEN_RE.fullmatch(event):
        raise ValueError("event must be a short code-like token")
    clean: dict[str, int | str] = {}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            raise ValueError(f"field not allowed in logs: {key}")
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError(f"bad log value type for {key}")
        if isinstance(value, str) and not _TOKEN_RE.fullmatch(value):
            raise ValueError(f"log value for {key} is not a code-like token")
        clean[key] = value
    _LOGGER.log(level, json.dumps({"event": event, **clean}, sort_keys=True))
