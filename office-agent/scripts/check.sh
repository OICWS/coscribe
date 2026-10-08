#!/usr/bin/env bash
# The checks CI runs, in the same order, so a local pass means a CI pass.
#   bash scripts/check.sh          everything (the tests take about 12 minutes)
#   bash scripts/check.sh --fast   everything except pytest
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d .venv/bin ]; then
  PATH="$PWD/.venv/bin:$PATH"
elif [ -d .venv/Scripts ]; then
  PATH="$PWD/.venv/Scripts:$PATH"
fi

fast=0
[ "${1:-}" = "--fast" ] && fast=1

echo "== frontend: tsc -b and vite build =="
# `tsc -b` is stricter than `tsc --noEmit -p .`; CI runs this one.
(cd frontend && npm run build)

echo "== ruff =="
ruff check src tests

echo "== mypy =="
mypy src

if [ "$fast" -eq 0 ]; then
  echo "== pytest =="
  pytest -q
fi
echo "All checks passed."
