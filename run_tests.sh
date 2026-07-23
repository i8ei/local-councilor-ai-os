#!/usr/bin/env bash
# Run every test suite in the repository from one entry point.
# Root `python3 -m unittest discover` only finds bootstrap tests because
# `modules/` is not a package and its subdirectories use hyphenated names,
# so per-module suites must be invoked explicitly to avoid silent skips.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

failures=0

run_suite() {
  # $1: human-readable label, remaining args: command to run
  local label="$1"
  shift
  echo "== ${label} =="
  if "$@"; then
    echo "-- ${label}: OK"
  else
    echo "-- ${label}: FAILED"
    failures=$((failures + 1))
  fi
  echo
}

# Tier 0-1 bootstrap CLI (a real package, discoverable from the repo root).
run_suite "bootstrap" python3 -m unittest discover -v

# Per-module suites. Each module runs from its own directory so that
# sibling-relative imports (adapters, search, ingest, ...) resolve.
for module in minutes-db regulations benchmark budget-review settlement-review; do
  if compgen -G "modules/${module}/tests/test_*.py" > /dev/null; then
    run_suite "modules/${module}" bash -c \
      "cd 'modules/${module}' && python3 -m unittest discover -s tests -v"
  fi
done

# Settlement reconciliation: rebuild fixtures, expect pass=0 and fail=1.
run_suite "settlement-review verify_totals" bash -c '
  set -euo pipefail
  cd modules/settlement-review
  python3 tests/create_fixtures.py > /dev/null
  python3 verify_totals.py tests/passing.db > /dev/null
  if python3 verify_totals.py tests/failing.db > /dev/null; then
    echo "expected failing.db to return non-zero"
    exit 1
  fi
'

if [ "$failures" -ne 0 ]; then
  echo "TEST RESULT: ${failures} suite(s) FAILED"
  exit 1
fi
echo "TEST RESULT: all suites passed"
