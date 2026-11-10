"""Shared Lambda wiring: AWS clients and the bank client, cached across warm invocations."""

from __future__ import annotations

import os
from typing import Any

import boto3

from adapters.enablebanking import EnableBankingClient
from adapters.ssm import SsmSecrets

PROVIDER = "enablebanking"

_secrets: SsmSecrets | None = None
_table: Any = None
_bank: EnableBankingClient | None = None


def secrets() -> SsmSecrets:
    global _secrets
    if _secrets is None:
        _secrets = SsmSecrets(boto3.client("ssm"))
    return _secrets


def table() -> Any:
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def bank() -> EnableBankingClient:
    global _bank
    if _bank is None:
        s = secrets()
        _bank = EnableBankingClient(
            s.get(os.environ["SSM_APP_ID"]), s.get(os.environ["SSM_PRIVATE_KEY"]).encode()
        )
    return _bank
