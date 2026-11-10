#!/usr/bin/env bash
# Connect (or reconnect) the bank: asks the consent Lambda for a bank approval URL and opens it.
# Needs AWS_PROFILE=expenses AWS_REGION=eu-west-1 (the Makefile sets them). There is no public
# "start" endpoint on purpose; this direct invoke is the authenticated way in.
set -euo pipefail

if [[ "${AWS_PROFILE:-}" != "expenses" || "${AWS_REGION:-}" != "eu-west-1" ]]; then
  echo "refusing: AWS_PROFILE must be 'expenses' and AWS_REGION 'eu-west-1'" >&2
  exit 1
fi

out="$(mktemp)"
status="$out.status"
trap 'rm -f "$out" "$status"' EXIT

aws lambda invoke --function-name expenses-tracker-consent \
  --payload '{"action":"start"}' --cli-binary-format raw-in-base64-out \
  --query FunctionError --output text "$out" >"$status"
if [[ "$(cat "$status")" != "None" ]]; then
  echo "the consent function failed; check its log group /aws/lambda/expenses-tracker-consent" >&2
  exit 1
fi

url="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$out")"
echo "Opening the bank approval page. Approve it, and you will land on a 'Connected' page."
echo "If the browser does not open, copy this URL (valid for 15 minutes):"
echo "$url"
command -v open >/dev/null && open "$url" || true
