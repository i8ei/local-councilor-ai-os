#!/usr/bin/env python3
"""Generate budget review candidates after the budget verification gate passes."""

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

from . import verify_totals


def _stable_id(*parts: Any) -> str:
    joined = "\n".join(str(part) for part in parts)
    return "budget_insight_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


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


def _name(row: sqlite3.Row) -> str:
    return " ".join(
        str(row[key])
        for key in ("kan_name", "ko_name", "moku_name", "setsu_name")
        if row[key]
    ) or row["side"]


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
            "budget_stage": row["budget_stage"],
            "proposal_no": row["proposal_no"],
            "side": row["side"],
            "grain": row["grain"],
            "formula": formula,
        },
        "source": _source(row),
        "transformations": [{"operation": "ratio", "expression": formula, "unit": row["unit"]}],
        "verification_state": row["verification_state"],
        "unresolved": ["確認候補であり、政策評価や原因は自動確定しない。"],
    }


def _verify_database(path: Path) -> dict[str, Any]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = verify_totals.verify(path)
    return {"exit_code": code, "output": buffer.getvalue().splitlines()}


def generate_insights(
    connection: sqlite3.Connection,
    *,
    min_change_ratio: float = 0.1,
    min_change_amount: int = 1,
    min_supplement_ratio: float = 0.1,
    limit: int = 50,
) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    candidates: list[dict[str, Any]] = []
    rows = connection.execute(
        """
        SELECT * FROM budget_line
        WHERE grain != 'total'
        ORDER BY fiscal_year, account_name, budget_stage, proposal_no,
                 side, grain, kan_code, ko_code, moku_code, setsu_code
        """
    ).fetchall()
    for row in rows:
        comparison = row["comparison_amount"]
        current = row["current_year_amount"]
        previous = row["previous_year_amount"]
        if comparison is not None and current is not None:
            denominator = abs(previous) if previous not in (None, 0) else None
            change_ratio = _ratio(abs(comparison), denominator)
            if abs(comparison) >= min_change_amount and (
                previous in (None, 0) or (change_ratio is not None and change_ratio >= min_change_ratio)
            ):
                kind = "large_budget_increase" if comparison > 0 else "large_budget_decrease"
                direction = "増" if comparison > 0 else "減"
                ratio_text = "前年度額が0または欠測" if change_ratio is None else f"前年度比{change_ratio:.2%}"
                candidates.append(_candidate(
                    kind=kind,
                    row=row,
                    statement=(
                        f"{row['account_name']} {_name(row)}の本年度予算額は"
                        f"{current}{row['unit']}、比較は{comparison}{row['unit']}の{direction}。"
                        f"{ratio_text}。"
                    ),
                    value={
                        "current_year_amount": current,
                        "previous_year_amount": previous,
                        "comparison_amount": comparison,
                        "change_ratio": change_ratio,
                        "unit": row["unit"],
                    },
                    formula="comparison_amount / previous_year_amount",
                ))
        supplement = row["supplement_amount"]
        pre = row["pre_supplement_amount"]
        post = row["post_supplement_amount"]
        supplement_ratio = None
        if supplement is not None:
            supplement_ratio = _ratio(abs(supplement), abs(pre) if pre is not None else None)
        if supplement is not None and post is not None and (
            abs(supplement) >= min_change_amount and (
                pre in (None, 0) or (supplement_ratio is not None and supplement_ratio >= min_supplement_ratio)
            )
        ):
            kind = "large_supplement_increase" if supplement > 0 else "large_supplement_decrease"
            direction = "増" if supplement > 0 else "減"
            ratio_text = "補正前額が0または欠測" if supplement_ratio is None else f"補正前比{supplement_ratio:.2%}"
            candidates.append(_candidate(
                kind=kind,
                row=row,
                statement=(
                    f"{row['account_name']} {_name(row)}の補正額は"
                    f"{supplement}{row['unit']}の{direction}。補正後額は{post}{row['unit']}。"
                    f"{ratio_text}。"
                ),
                value={
                    "pre_supplement_amount": pre,
                    "supplement_amount": supplement,
                    "post_supplement_amount": post,
                    "supplement_ratio": supplement_ratio,
                    "unit": row["unit"],
                },
                formula="supplement_amount / pre_supplement_amount",
            ))
    candidates.sort(key=lambda item: (item["analysis_kind"], item["candidate_id"]))
    return {
        "schema_version": "budget-insights/1",
        "purpose": "予算検算後に人が確認する予算審議候補を生成する",
        "thresholds": {
            "min_change_ratio": min_change_ratio,
            "min_change_amount": min_change_amount,
            "min_supplement_ratio": min_supplement_ratio,
        },
        "candidates": candidates[:limit],
        "missing_or_unresolved": [] if candidates else ["しきい値に該当する候補なし"],
        "caveats": [
            "候補は予算審議の入口であり、必要性や妥当性を自動確定しない。",
            "歳出増減は財源、事業内容、制度変更、組替えを別途確認する。",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--min-change-ratio", type=float, default=0.1)
    parser.add_argument("--min-change-amount", type=int, default=1)
    parser.add_argument("--min-supplement-ratio", type=float, default=0.1)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--skip-verify", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 1:
        print(json.dumps({"error": "--limit must be at least 1"}, ensure_ascii=False), file=sys.stderr)
        return 2
    if not args.skip_verify:
        verification = _verify_database(args.database)
        if verification["exit_code"] != 0:
            print(json.dumps({"error": "verify_totals failed", "verification": verification}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
    try:
        with contextlib.closing(
            sqlite3.connect(args.database)
        ) as connection:
            result = generate_insights(
                connection,
                min_change_ratio=args.min_change_ratio,
                min_change_amount=args.min_change_amount,
                min_supplement_ratio=args.min_supplement_ratio,
                limit=args.limit,
            )
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
