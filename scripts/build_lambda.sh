#!/usr/bin/env bash
# Build the pull Lambda package directory (build/pull) for Python 3.12 on arm64.
# Terraform zips it. Only core/, adapters/ and the pinned runtime deps are included.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/build/pull"
rm -rf "$out"
mkdir -p "$out"

"$root/.venv/bin/pip" install --quiet --no-compile \
  --target "$out" \
  --platform manylinux2014_aarch64 --platform manylinux_2_28_aarch64 \
  --python-version 3.12 --implementation cp --only-binary=:all: \
  -r "$root/requirements-lambda.txt"

cp -R "$root/core" "$root/adapters" "$out/"
find "$out" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$out" -name "*.dist-info" -type d -prune -exec rm -rf {} +
rm -rf "$out/bin"
echo "built $out"
