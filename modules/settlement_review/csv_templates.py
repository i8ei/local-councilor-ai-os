#!/usr/bin/env python3
"""Print empty settlement CSV templates with the required headers."""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Sequence, TextIO

COMMON = [
    "fiscal_year", "account_name", "raw_value", "unit", "as_of",
    "definition", "source_name", "source_url", "source_locator",
    "fetched_at", "verification_state", "fetch_cache_key",
    "robots_decision", "request_time", "print_page", "pdf_page",
]
SUMMARY = [
    "side", "kan_code", "kan_name", "budget_current_amount",
    "collected_amount", "uncollectible_amount", "outstanding_amount",
    "spent_amount", "carryover_amount", "unused_amount", *COMMON,
]
REVENUE = [
    "kan_code", "kan_name", "ko_code", "ko_name", "budget_current_amount",
    "collected_amount", "uncollectible_amount", "outstanding_amount", *COMMON,
]
EXPENDITURE = [
    "kan_code", "kan_name", "ko_code", "ko_name", "moku_code", "moku_name",
    "setsu_code", "setsu_name", "block_no", "item_budget_current_amount",
    "item_spent_amount", "item_carryover_amount", "item_unused_amount",
    "section_budget_current_amount", "section_spent_amount",
    "section_carryover_amount", "section_unused_amount", *COMMON,
]
TEMPLATES = {"summary": SUMMARY, "revenue": REVENUE, "expenditure": EXPENDITURE}


def write_template(kind: str, output: TextIO) -> None:
    writer = csv.writer(output)
    writer.writerow(TEMPLATES[kind])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=tuple(TEMPLATES))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_template(args.kind, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
