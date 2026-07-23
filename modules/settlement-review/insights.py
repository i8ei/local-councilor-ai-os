#!/usr/bin/env python3
"""Generate review candidates from a reconciled settlement SQLite database."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import verify_totals


def _stable_id(*parts: Any) -> str:
    joined = "\n".join(str(part) for part in parts)
    return "insight_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def _locator(row: sqlite3.Row) -> Any:
    try:
        return json.loads(row["source_locator"])
    except (TypeError, json.JSONDecodeError):
        return row["source_locator"]


def _source(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "print_page": row["print_page"],
        "pdf_page": row["pdf_page"],
        "source_locator": _locator(row),
        "row_id": row["id"],
    }


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _candidate(
    *,
    kind: str,
    row: sqlite3.Row,
    statement: str,
    value: dict[str, Any],
    formula: str,
    unresolved: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": _stable_id(kind, row["id"], value),
        "analysis_kind": kind,
        "statement": statement,
        "value": value,
        "as_of": row["as_of"],
        "definition": {
            "text": row["definition"],
            "account_name": row["account_name"],
            "grain": "kan",
            "formula": formula,
        },
        "source": _source(row),
        "transformations": [
            {
                "operation": "ratio",
                "expression": formula,
                "unit": row["unit"],
            }
        ],
        "verification_state": row["verification_state"],
        "unresolved": unresolved or ["原因と妥当性は決算数値だけから判断しない。"],
    }


def _verify_database(path: Path) -> dict[str, Any]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = verify_totals.verify(path)
    return {"exit_code": code, "output": buffer.getvalue().splitlines()}


def generate_insights(
    connection: sqlite3.Connection,
    *,
    min_unused_amount: int = 1,
    min_unused_ratio: float = 0.1,
    min_carryover_ratio: float = 0.1,
    min_outstanding_ratio: float = 0.1,
    limit: int = 50,
) -> dict[str, Any]:
    """Return deterministic review candidates without assigning policy blame."""
    connection.row_factory = sqlite3.Row
    candidates: list[dict[str, Any]] = []

    expenditure_rows = connection.execute(
        """
        SELECT * FROM settlement_summary
        WHERE side = 'expenditure'
        ORDER BY fiscal_year, account_name, kan_code
        """
    ).fetchall()
    for row in expenditure_rows:
        unused_ratio = _ratio(row["unused_amount"], row["budget_current_amount"])
        if row["unused_amount"] >= min_unused_amount and (
            unused_ratio is not None and unused_ratio >= min_unused_ratio
        ):
            candidates.append(
                _candidate(
                    kind="large_unused",
                    row=row,
                    statement=(
                        f"{row['account_name']} {row['kan_name']}の不用額は"
                        f"{row['unused_amount']}{row['unit']}、予算現額比"
                        f"{unused_ratio:.2%}。"
                    ),
                    value={
                        "unused_amount": row["unused_amount"],
                        "budget_current_amount": row["budget_current_amount"],
                        "unused_ratio": unused_ratio,
                        "unit": row["unit"],
                    },
                    formula="unused_amount / budget_current_amount",
                )
            )
        carryover_ratio = _ratio(row["carryover_amount"], row["budget_current_amount"])
        if carryover_ratio is not None and carryover_ratio >= min_carryover_ratio:
            candidates.append(
                _candidate(
                    kind="large_carryover",
                    row=row,
                    statement=(
                        f"{row['account_name']} {row['kan_name']}の翌年度繰越額は"
                        f"{row['carryover_amount']}{row['unit']}、予算現額比"
                        f"{carryover_ratio:.2%}。"
                    ),
                    value={
                        "carryover_amount": row["carryover_amount"],
                        "budget_current_amount": row["budget_current_amount"],
                        "carryover_ratio": carryover_ratio,
                        "unit": row["unit"],
                    },
                    formula="carryover_amount / budget_current_amount",
                )
            )

    revenue_rows = connection.execute(
        """
        SELECT * FROM settlement_summary
        WHERE side = 'revenue'
        ORDER BY fiscal_year, account_name, kan_code
        """
    ).fetchall()
    for row in revenue_rows:
        outstanding_ratio = _ratio(row["outstanding_amount"], row["budget_current_amount"])
        if outstanding_ratio is not None and outstanding_ratio >= min_outstanding_ratio:
            candidates.append(
                _candidate(
                    kind="large_outstanding_revenue",
                    row=row,
                    statement=(
                        f"{row['account_name']} {row['kan_name']}の収入未済額は"
                        f"{row['outstanding_amount']}{row['unit']}、予算現額比"
                        f"{outstanding_ratio:.2%}。"
                    ),
                    value={
                        "outstanding_amount": row["outstanding_amount"],
                        "budget_current_amount": row["budget_current_amount"],
                        "outstanding_ratio": outstanding_ratio,
                        "unit": row["unit"],
                    },
                    formula="outstanding_amount / budget_current_amount",
                )
            )

    candidates.sort(
        key=lambda item: (
            item["analysis_kind"],
            -max(
                value for key, value in item["value"].items()
                if key.endswith("_ratio") and isinstance(value, (int, float))
            ),
            item["candidate_id"],
        )
    )
    return {
        "schema_version": "settlement-insights/1",
        "purpose": "差額ゼロ検算後に人が確認する決算審査候補を生成する",
        "thresholds": {
            "min_unused_amount": min_unused_amount,
            "min_unused_ratio": min_unused_ratio,
            "min_carryover_ratio": min_carryover_ratio,
            "min_outstanding_ratio": min_outstanding_ratio,
        },
        "candidates": candidates[:limit],
        "missing_or_unresolved": [] if candidates else ["しきい値に該当する候補なし"],
        "caveats": [
            "候補は質問の入口であり、原因や政策評価を自動確定しない。",
            "外部利用前に原典、定義、単位、公開範囲を確認する。",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--min-unused-amount", type=int, default=1)
    parser.add_argument("--min-unused-ratio", type=float, default=0.1)
    parser.add_argument("--min-carryover-ratio", type=float, default=0.1)
    parser.add_argument("--min-outstanding-ratio", type=float, default=0.1)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip verify_totals gate. Use only for development fixtures.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 1:
        print(json.dumps({"error": "--limit must be at least 1"}, ensure_ascii=False), file=sys.stderr)
        return 2
    if not args.skip_verify:
        verification = _verify_database(args.database)
        if verification["exit_code"] != 0:
            print(
                json.dumps(
                    {"error": "verify_totals failed", "verification": verification},
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
    try:
        with sqlite3.connect(args.database) as connection:
            result = generate_insights(
                connection,
                min_unused_amount=args.min_unused_amount,
                min_unused_ratio=args.min_unused_ratio,
                min_carryover_ratio=args.min_carryover_ratio,
                min_outstanding_ratio=args.min_outstanding_ratio,
                limit=args.limit,
            )
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
