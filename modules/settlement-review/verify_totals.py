#!/usr/bin/env python3
"""Reconcile settlement detail rows to item and summary totals."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


REVENUE_PAIRS = (
    ("予算現額", "budget_current_amount", "budget_current_amount"),
    ("収入済額", "collected_amount", "collected_amount"),
    ("不納欠損額", "uncollectible_amount", "uncollectible_amount"),
    ("収入未済額", "outstanding_amount", "outstanding_amount"),
)

EXPENDITURE_PAIRS = (
    ("予算現額", "budget_current_amount", "budget_current_amount"),
    ("支出済額", "spent_amount", "spent_amount"),
    ("翌年度繰越額", "carryover_amount", "carryover_amount"),
    ("不用額", "unused_amount", "unused_amount"),
)

ITEM_PAIRS = (
    ("予算現額", "item_budget_current_amount", "section_budget_current_amount"),
    ("支出済額", "item_spent_amount", "section_spent_amount"),
    ("翌年度繰越額", "item_carryover_amount", "section_carryover_amount"),
    ("不用額", "item_unused_amount", "section_unused_amount"),
)


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _key(row: sqlite3.Row) -> tuple[Any, ...]:
    return (
        row["fiscal_year"],
        row["account_name"],
        row["kan_code"],
    )


def _key_label(key: tuple[Any, ...], row: sqlite3.Row | None) -> str:
    year, account, kan_code = key
    kan_name = row["kan_name"] if row is not None else "<名称欠落>"
    return f"年度={year} 会計={account} 款={kan_code} {kan_name}"


def _summary_rows(
    connection: sqlite3.Connection, side: str
) -> dict[tuple[Any, ...], sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT *
        FROM settlement_summary
        WHERE side = ?
        ORDER BY fiscal_year, account_name, kan_code
        """,
        (side,),
    )
    return {_key(row): row for row in rows}


def _revenue_kan_rows(
    connection: sqlite3.Connection,
) -> dict[tuple[Any, ...], sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT
            fiscal_year,
            account_name,
            kan_code,
            MIN(kan_name) AS kan_name,
            MIN(unit) AS unit,
            COUNT(DISTINCT unit) AS unit_count,
            SUM(budget_current_amount) AS budget_current_amount,
            SUM(collected_amount) AS collected_amount,
            SUM(uncollectible_amount) AS uncollectible_amount,
            SUM(outstanding_amount) AS outstanding_amount
        FROM settlement_revenue
        GROUP BY fiscal_year, account_name, kan_code
        ORDER BY fiscal_year, account_name, kan_code
        """
    )
    return {_key(row): row for row in rows}


def _expenditure_item_rows(
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                fiscal_year,
                account_name,
                kan_code,
                MIN(kan_name) AS kan_name,
                ko_code,
                MIN(ko_name) AS ko_name,
                moku_code,
                MIN(moku_name) AS moku_name,
                MIN(unit) AS unit,
                COUNT(DISTINCT unit) AS unit_count,
                MIN(item_budget_current_amount)
                    AS item_budget_current_amount,
                MAX(item_budget_current_amount)
                    AS item_budget_current_amount_max,
                MIN(item_spent_amount) AS item_spent_amount,
                MAX(item_spent_amount) AS item_spent_amount_max,
                MIN(item_carryover_amount) AS item_carryover_amount,
                MAX(item_carryover_amount) AS item_carryover_amount_max,
                MIN(item_unused_amount) AS item_unused_amount,
                MAX(item_unused_amount) AS item_unused_amount_max,
                SUM(section_budget_current_amount)
                    AS section_budget_current_amount,
                SUM(section_spent_amount) AS section_spent_amount,
                SUM(section_carryover_amount) AS section_carryover_amount,
                SUM(section_unused_amount) AS section_unused_amount
            FROM settlement_expenditure
            GROUP BY
                fiscal_year, account_name, kan_code, ko_code, moku_code
            ORDER BY
                fiscal_year, account_name, kan_code, ko_code, moku_code
            """
        )
    )


def _expenditure_kan_rows(
    connection: sqlite3.Connection,
) -> dict[tuple[Any, ...], sqlite3.Row]:
    rows = connection.execute(
        """
        WITH item_grain AS (
            SELECT
                fiscal_year,
                account_name,
                kan_code,
                MIN(kan_name) AS kan_name,
                ko_code,
                moku_code,
                MIN(unit) AS unit,
                COUNT(DISTINCT unit) AS item_unit_count,
                MIN(item_budget_current_amount)
                    AS budget_current_amount,
                MAX(item_budget_current_amount)
                    AS budget_current_amount_max,
                MIN(item_spent_amount) AS spent_amount,
                MAX(item_spent_amount) AS spent_amount_max,
                MIN(item_carryover_amount) AS carryover_amount,
                MAX(item_carryover_amount) AS carryover_amount_max,
                MIN(item_unused_amount) AS unused_amount,
                MAX(item_unused_amount) AS unused_amount_max
            FROM settlement_expenditure
            GROUP BY
                fiscal_year, account_name, kan_code, ko_code, moku_code
        )
        SELECT
            fiscal_year,
            account_name,
            kan_code,
            MIN(kan_name) AS kan_name,
            MIN(unit) AS unit,
            CASE
                WHEN MAX(item_unit_count) = 1
                    AND COUNT(DISTINCT unit) = 1
                THEN 1
                ELSE 0
            END AS units_consistent,
            SUM(
                CASE
                    WHEN budget_current_amount != budget_current_amount_max
                        OR spent_amount != spent_amount_max
                        OR carryover_amount != carryover_amount_max
                        OR unused_amount != unused_amount_max
                    THEN 1
                    ELSE 0
                END
            ) AS inconsistent_item_totals,
            SUM(budget_current_amount) AS budget_current_amount,
            SUM(spent_amount) AS spent_amount,
            SUM(carryover_amount) AS carryover_amount,
            SUM(unused_amount) AS unused_amount
        FROM item_grain
        GROUP BY fiscal_year, account_name, kan_code
        ORDER BY fiscal_year, account_name, kan_code
        """
    )
    return {_key(row): row for row in rows}


def _compare_kan(
    label: str,
    summaries: dict[tuple[Any, ...], sqlite3.Row],
    details: dict[tuple[Any, ...], sqlite3.Row],
    pairs: Iterable[tuple[str, str, str]],
    unit_consistency_field: str,
) -> int:
    failures = 0
    print(f"[{label}: 款突合]")
    keys = sorted(set(summaries) | set(details))
    if not keys:
        print("  欠落=総括表と明細")
        return 1
    for key in keys:
        summary = summaries.get(key)
        detail = details.get(key)
        row_for_label = summary or detail
        print(_key_label(key, row_for_label))
        if summary is None or detail is None:
            missing = "総括表" if summary is None else "明細"
            print(f"  欠落={missing}")
            failures += 1
            continue
        detail_units_ok = int(detail[unit_consistency_field]) == 1
        if not detail_units_ok or summary["unit"] != detail["unit"]:
            print(
                "  単位不一致="
                f"総括表:{summary['unit']} 明細:{detail['unit']}"
            )
            failures += 1
            continue
        for amount_label, summary_field, detail_field in pairs:
            expected = summary[summary_field]
            actual = detail[detail_field]
            difference = actual - expected
            print(
                f"  {amount_label}: 総括表={expected} 明細={actual} "
                f"差額={difference} 単位={summary['unit']}"
            )
            if difference != 0:
                failures += 1
    return failures


def _compare_items(rows: Iterable[sqlite3.Row]) -> int:
    failures = 0
    print("[歳出: 節から目の突合]")
    found = False
    for row in rows:
        found = True
        label = (
            f"年度={row['fiscal_year']} 会計={row['account_name']} "
            f"款={row['kan_code']} 項={row['ko_code']} "
            f"目={row['moku_code']} {row['moku_name']}"
        )
        print(label)
        if int(row["unit_count"]) != 1:
            print("  単位不一致=同じ目の節行に複数単位")
            failures += 1
            continue
        repeated_totals_match = True
        for _, item_field, _ in ITEM_PAIRS:
            if row[item_field] != row[f"{item_field}_max"]:
                repeated_totals_match = False
        if not repeated_totals_match:
            print("  反復目合計不一致=同じ目の節行で目合計が異なる")
            failures += 1
        for amount_label, item_field, section_field in ITEM_PAIRS:
            expected = row[item_field]
            actual = row[section_field]
            difference = actual - expected
            print(
                f"  {amount_label}: 目={expected} 節合計={actual} "
                f"差額={difference} 単位={row['unit']}"
            )
            if difference != 0:
                failures += 1
    if not found:
        print("  欠落=歳出明細")
        failures += 1
    return failures


def verify(path: Path) -> int:
    if not path.is_file():
        print(f"DB が見つかりません: {path}", file=sys.stderr)
        return 2
    try:
        with _open_read_only(path) as connection:
            revenue_summary = _summary_rows(connection, "revenue")
            expenditure_summary = _summary_rows(connection, "expenditure")
            revenue = _revenue_kan_rows(connection)
            expenditure_items = _expenditure_item_rows(connection)
            expenditure = _expenditure_kan_rows(connection)
            failures = 0
            failures += _compare_kan(
                "歳入",
                revenue_summary,
                revenue,
                REVENUE_PAIRS,
                "unit_count",
            )
            failures += _compare_items(expenditure_items)
            failures += _compare_kan(
                "歳出",
                expenditure_summary,
                expenditure,
                EXPENDITURE_PAIRS,
                "units_consistent",
            )
            for row in expenditure.values():
                if int(row["inconsistent_item_totals"]) != 0:
                    print(
                        "歳出款内の反復目合計不一致="
                        f"{_key_label(_key(row), row)} "
                        f"目数={row['inconsistent_item_totals']}"
                    )
    except sqlite3.Error as exc:
        print(f"検算を実行できません: {exc}", file=sys.stderr)
        return 2
    if failures:
        print(f"検算結果=不合格 不一致件数={failures}")
        return 1
    print("検算結果=合格 すべての差額=0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Settlement SQLite database")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return verify(args.database)


if __name__ == "__main__":
    raise SystemExit(main())
