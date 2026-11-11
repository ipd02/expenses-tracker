"""One-off: move expense rows stored under per-session account uids to the stable account key.

Usage (AWS_PROFILE=expenses AWS_REGION=eu-west-1):
    python scripts/migrate_account_ids.py                # dry run, prints counts only
    python scripts/migrate_account_ids.py --apply        # copies, then deletes the old rows

Each old partition `ACCOUNT#<provider>:<uid>:` is mapped to `ACCOUNT#<provider>:<key>:` where
<key> comes from the bank's account details (needs the app credentials in SSM). A row that
already exists under the new key is a duplicate: the old copy is just deleted, so the row the
user may already have categorised is never overwritten. Nothing sensitive is printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.enablebanking import account_key_from_identification  # noqa: E402
from adapters.handlers import wiring  # noqa: E402

PREFIX = "ACCOUNT#"


def _scan_all(table: Any) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(pk, :p)",
        "ExpressionAttributeValues": {":p": PREFIX},
    }
    items: list[dict[str, Any]] = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--known-accounts",
        type=Path,
        help="sessions.json from the spike: fallback for uids whose session the bank has ended",
    )
    args = parser.parse_args()

    table, bank = wiring.table(), wiring.bank()
    items = _scan_all(table)
    keys: dict[str, str] = {}
    known: dict[str, str] = {}
    if args.known_accounts:
        for entry in json.loads(args.known_accounts.read_text()).values():
            for acct in entry.get("accounts", []):
                if acct.get("uid") and acct.get("identification_hash"):
                    known[acct["uid"]] = account_key_from_identification(
                        acct["identification_hash"]
                    )
    plan: Counter[str] = Counter()
    todo: list[tuple[dict[str, Any], str]] = []
    for item in items:
        provider, uid, _ = item["account_id"].split(":", 2)
        if uid not in keys:
            try:
                keys[uid] = known.get(uid) or bank.get_account_key(uid)
            except Exception:  # noqa: BLE001  # session ended and uid not in the known file
                keys[uid] = ""
        if not keys[uid]:
            plan["unresolved"] += 1
            continue
        new_account = f"{provider}:{keys[uid]}:"
        if new_account == item["account_id"]:
            plan["already_stable"] += 1
            continue
        todo.append((item, new_account))
        plan["to_move"] += 1
    print(dict(plan), f"partitions={len({i['pk'] for i in items})}")
    if not args.apply:
        print("dry run; pass --apply to migrate")
        return 0
    if plan["unresolved"]:
        print("refusing: some rows could not be mapped to a stable key", file=sys.stderr)
        return 1

    moved = dropped = 0
    for item, new_account in todo:
        ref = item["source_key"][len(item["account_id"]) :]
        new = {
            **item,
            "pk": f"{PREFIX}{new_account}",
            "account_id": new_account,
            "source_key": f"{new_account}{ref}",
        }
        try:
            table.put_item(
                Item=new,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            moved += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            dropped += 1  # duplicate of a row that already exists under the stable key
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    print({"moved": moved, "duplicates_dropped": dropped})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
