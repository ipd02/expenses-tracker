"""Invoke the other Lambdas. The API function never holds the bank key itself: starting a
consent and pulling both happen inside the functions that already own that access."""

from __future__ import annotations

import json
from typing import Any

from core.errors import BankError


class LambdaInvoker:
    def __init__(self, client: Any, consent_function: str, pull_function: str) -> None:
        self._client = client
        self._consent = consent_function
        self._pull = pull_function

    def start_consent(self) -> str:
        resp = self._client.invoke(
            FunctionName=self._consent,
            Payload=json.dumps({"action": "start"}).encode(),
        )
        payload = json.loads(resp["Payload"].read())
        url = payload.get("url") if isinstance(payload, dict) else None
        if resp.get("FunctionError") or not isinstance(url, str) or not url.startswith("https://"):
            raise BankError("consent start failed")
        return url

    def start_pull(self) -> None:
        self._client.invoke(
            FunctionName=self._pull, InvocationType="Event", Payload=json.dumps({}).encode()
        )
