#!/usr/bin/env bash
# Run terraform in infra/ pinned to the expenses profile and eu-west-1.
# Usage: scripts/tf.sh plan|apply|destroy [extra terraform args]
set -euo pipefail

if [[ "${AWS_PROFILE:-}" != "expenses" ]]; then
  echo "refusing: AWS_PROFILE must be 'expenses' (got '${AWS_PROFILE:-unset}')" >&2
  exit 1
fi
if [[ "${AWS_REGION:-}" != "eu-west-1" ]]; then
  echo "refusing: AWS_REGION must be 'eu-west-1' (got '${AWS_REGION:-unset}')" >&2
  exit 1
fi

cmd="${1:-}"
case "$cmd" in
  plan | apply | destroy) shift ;;
  *)
    echo "usage: AWS_PROFILE=expenses AWS_REGION=eu-west-1 $0 plan|apply|destroy [args]" >&2
    exit 2
    ;;
esac

export AWS_DEFAULT_REGION=eu-west-1
root="$(cd "$(dirname "$0")/.." && pwd)"
terraform -chdir="$root/infra" init -input=false >/dev/null
exec terraform -chdir="$root/infra" "$cmd" "$@"
