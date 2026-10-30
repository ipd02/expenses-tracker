import copy
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT = "enablebanking:00000000-0000-0000-0000-000000000000:"


@pytest.fixture
def txs() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads((FIXTURES / "transactions.json").read_text())
    return copy.deepcopy(data)


@pytest.fixture
def account_id() -> str:
    return ACCOUNT
