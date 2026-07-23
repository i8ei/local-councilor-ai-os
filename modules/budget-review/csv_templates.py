#!/usr/bin/env python3
"""Print an empty budget-review CSV template with required headers."""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Sequence

FIELDS = [
    "fiscal_year", "account_name", "budget_stage", "proposal_no", "side",
    "grain", "kan_code", "kan_name", "ko_code", "ko_name", "moku_code",
    "moku_name", "setsu_code", "setsu_name", "current_year_amount",
    "previous_year_amount", "comparison_amount", "pre_supplement_amount",
    "supplement_amount", "post_supplement_amount", "raw_value", "unit",
    "as_of", "definition", "source_name", "source_url", "source_locator",
    "fetched_at", "verification_state", "fetch_cache_key", "robots_decision",
    "request_time", "print_page", "pdf_page",
]


def write_template(output: object) -> None:
    writer = csv.writer(output)
    writer.writerow(FIELDS)


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    write_template(sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
