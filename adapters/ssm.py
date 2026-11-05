"""Read secrets from SSM Parameter Store. Values are returned, never logged."""

from __future__ import annotations

from typing import Any


class SsmSecrets:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, name: str) -> str:
        resp = self._client.get_parameter(Name=name, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        if not isinstance(value, str):
            raise TypeError("unexpected SSM value type")
        return value
