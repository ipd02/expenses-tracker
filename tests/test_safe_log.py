import json
import logging

import pytest

from adapters.safe_log import log


def test_logs_allowed_fields(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="expenses"):
        log("pull_finished", status="ok", inserted=3, skipped=0)
    assert json.loads(caplog.records[0].getMessage()) == {
        "event": "pull_finished",
        "status": "ok",
        "inserted": 3,
        "skipped": 0,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"amount": 12},
        {"counterparty": "SHOP"},
        {"session_id": "abc"},
        {"status": "free text with spaces"},
        {"status": "12.50"},
        {"inserted": True},
        {"status": 1.5},
    ],
)
def test_sensitive_or_odd_fields_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        log("event", **fields)  # type: ignore[arg-type]


def test_event_name_must_be_a_token() -> None:
    with pytest.raises(ValueError):
        log("has spaces and 123")
