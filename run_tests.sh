#!/usr/bin/env bash
# Run every test suite in the repository from one entry point.
# Every component, including `modules/`, is an importable package, so one
# root discovery run covers the complete test suite.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
export PYTHONWARNINGS="${PYTHONWARNINGS:-error::ResourceWarning}"

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

run_suite "all tests" python3 -m unittest discover -v

# Settlement reconciliation: rebuild fixtures, expect pass=0 and fail=1.
run_suite "settlement_review verify_totals" bash -c '
  set -euo pipefail
  python3 -m modules.settlement_review.tests.create_fixtures > /dev/null
  python3 -m modules.settlement_review.verify_totals \
    modules/settlement_review/tests/passing.db > /dev/null
  if python3 -m modules.settlement_review.verify_totals \
    modules/settlement_review/tests/failing.db > /dev/null; then
    echo "expected failing.db to return non-zero"
    exit 1
  fi
'

if [ "$failures" -ne 0 ]; then
  echo "TEST RESULT: ${failures} suite(s) FAILED"
  exit 1
fi
echo "TEST RESULT: all suites passed"
