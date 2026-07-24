#!/usr/bin/env python3
"""Verify budget arithmetic, hierarchy totals, and revenue/expenditure balance."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from lcaios.database import sqlite_read_only_uri
from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_verification_run,
)

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
AMOUNT_FIELDS = (
    "current_year_amount",
    "previous_year_amount",
    "comparison_amount",
    "pre_supplement_amount",
    "supplement_amount",
    "post_supplement_amount",
)


@contextmanager
def _open(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection that is always closed on exit."""

    with closing(
        sqlite3.connect(sqlite_read_only_uri(path.resolve()), uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        yield connection


def _key(row: sqlite3.Row) -> tuple[Any, ...]:
    return (row["fiscal_year"], row["account_name"], row["budget_stage"], row["proposal_no"])


def _label(row: sqlite3.Row) -> str:
    proposal = f" 議案={row['proposal_no']}" if row["proposal_no"] else ""
    return f"年度={row['fiscal_year']} 会計={row['account_name']} 段階={row['budget_stage']}{proposal}"


def _check_comparison(connection: sqlite3.Connection) -> int:
    failures = 0
    print("[前年度比較]")
    rows = connection.execute(
        """
        SELECT * FROM budget_line
        WHERE current_year_amount IS NOT NULL
          AND previous_year_amount IS NOT NULL
          AND comparison_amount IS NOT NULL
        ORDER BY fiscal_year, account_name, budget_stage, side, grain,
                 kan_code, ko_code, moku_code, setsu_code
        """
    )
    found = False
    for row in rows:
        found = True
        expected = row["current_year_amount"] - row["previous_year_amount"]
        difference = row["comparison_amount"] - expected
        print(
            f"{_label(row)} {row['side']} {row['grain']} "
            f"本年度={row['current_year_amount']} 前年度={row['previous_year_amount']} "
            f"比較={row['comparison_amount']} 差額={difference} 単位={row['unit']}"
        )
        if difference != 0:
            failures += 1
    if not found:
        print("  対象行なし")
    return failures


def _check_supplement(connection: sqlite3.Connection) -> int:
    failures = 0
    print("[補正前後]")
    rows = connection.execute(
        """
        SELECT * FROM budget_line
        WHERE pre_supplement_amount IS NOT NULL
          AND supplement_amount IS NOT NULL
          AND post_supplement_amount IS NOT NULL
        ORDER BY fiscal_year, account_name, budget_stage, proposal_no,
                 side, grain, kan_code, ko_code, moku_code, setsu_code
        """
    )
    found = False
    for row in rows:
        found = True
        expected = row["pre_supplement_amount"] + row["supplement_amount"]
        difference = row["post_supplement_amount"] - expected
        print(
            f"{_label(row)} {row['side']} {row['grain']} "
            f"補正前={row['pre_supplement_amount']} 補正={row['supplement_amount']} "
            f"補正後={row['post_supplement_amount']} 差額={difference} "
            f"単位={row['unit']}"
        )
        if difference != 0:
            failures += 1
    if not found:
        print("  対象行なし")
    return failures


def _amount(row: sqlite3.Row, field: str) -> int | None:
    value = row[field]
    if value is not None:
        return int(value)
    if field == "current_year_amount" and row["post_supplement_amount"] is not None:
        return int(row["post_supplement_amount"])
    return None


def _parent_rows(connection: sqlite3.Connection, grain: str) -> list[sqlite3.Row]:
    return list(connection.execute(
        """
        SELECT * FROM budget_line
        WHERE grain = ?
        ORDER BY fiscal_year, account_name, budget_stage, proposal_no,
                 side, kan_code, ko_code, moku_code
        """,
        (grain,),
    ))


def _child_values(
    connection: sqlite3.Connection,
    parent: sqlite3.Row,
    child_grain: str,
    field: str,
) -> list[sqlite3.Row]:
    if field not in AMOUNT_FIELDS:
        raise ValueError(f"Unsupported amount field: {field}")
    filters = [
        "fiscal_year = :fiscal_year",
        "account_name = :account_name",
        "budget_stage = :budget_stage",
        "COALESCE(proposal_no, '') = COALESCE(:proposal_no, '')",
        "side = :side",
        "grain = :child_grain",
    ]
    params: dict[str, Any] = {
        "fiscal_year": parent["fiscal_year"],
        "account_name": parent["account_name"],
        "budget_stage": parent["budget_stage"],
        "proposal_no": parent["proposal_no"],
        "side": parent["side"],
        "child_grain": child_grain,
    }
    for key in ("kan_code", "ko_code", "moku_code"):
        if parent[key] is not None:
            filters.append(f"{key} = :{key}")
            params[key] = parent[key]
    return list(connection.execute(
        f"SELECT {field} AS value, unit FROM budget_line WHERE "
        + " AND ".join(filters),
        params,
    ))


def _child_sum(
    connection: sqlite3.Connection,
    parent: sqlite3.Row,
    child_grain: str,
    field: str,
) -> int | None:
    rows = _child_values(connection, parent, child_grain, field)
    values = [row["value"] for row in rows if row["value"] is not None]
    if not values:
        return None
    return int(sum(values))


def _check_hierarchy(connection: sqlite3.Connection) -> int:
    failures = 0
    print("[階層合計]")
    pairs = (("total", "kan"), ("kan", "ko"), ("ko", "moku"), ("moku", "setsu"))
    found = False
    for parent_grain, child_grain in pairs:
        for parent in _parent_rows(connection, parent_grain):
            parent_amount = _amount(parent, "current_year_amount")
            if parent_amount is None:
                continue
            child_rows = _child_values(
                connection,
                parent,
                child_grain,
                "current_year_amount",
            )
            child_values = [
                row["value"] for row in child_rows if row["value"] is not None
            ]
            if not child_values:
                continue
            child_amount = int(sum(child_values))
            found = True
            child_units = sorted(
                {
                    str(row["unit"])
                    for row in child_rows
                    if row["value"] is not None
                }
            )
            if len(child_units) != 1 or child_units[0] != parent["unit"]:
                print(
                    f"{_label(parent)} {parent['side']} "
                    f"{parent_grain}->{child_grain} "
                    f"単位不一致=親:{parent['unit']} "
                    f"子:{','.join(child_units)}"
                )
                failures += 1
                continue
            difference = child_amount - parent_amount
            print(
                f"{_label(parent)} {parent['side']} {parent_grain}->{child_grain} "
                f"親={parent_amount} 子合計={child_amount} 差額={difference} "
                f"単位={parent['unit']}"
            )
            if difference != 0:
                failures += 1
    if not found:
        print("  対象行なし")
    return failures


def _total_amounts(connection: sqlite3.Connection, key: tuple[Any, ...]) -> dict[str, int]:
    return {
        side: amount
        for side, (amount, _unit) in _total_entries(connection, key).items()
    }


def _total_entries(
    connection: sqlite3.Connection,
    key: tuple[Any, ...],
) -> dict[str, tuple[int, str]]:
    rows = connection.execute(
        """
        SELECT side, current_year_amount, post_supplement_amount, unit
        FROM budget_line
        WHERE fiscal_year = ? AND account_name = ? AND budget_stage = ?
          AND COALESCE(proposal_no, '') = COALESCE(?, '')
          AND grain = 'total'
        """,
        key,
    ).fetchall()
    result: dict[str, tuple[int, str]] = {}
    for row in rows:
        amount = row["current_year_amount"]
        if amount is None:
            amount = row["post_supplement_amount"]
        if amount is not None:
            result[row["side"]] = (int(amount), str(row["unit"]))
    return result


def _check_balance(connection: sqlite3.Connection) -> int:
    failures = 0
    print("[歳入歳出一致]")
    keys = [
        tuple(row)
        for row in connection.execute(
            """
            SELECT DISTINCT fiscal_year, account_name, budget_stage, proposal_no
            FROM budget_line
            ORDER BY fiscal_year, account_name, budget_stage, proposal_no
            """
        )
    ]
    if not keys:
        print("  欠落=予算行")
        return 1
    for key in keys:
        entries = _total_entries(connection, key)
        label = f"年度={key[0]} 会計={key[1]} 段階={key[2]}"
        if key[3]:
            label += f" 議案={key[3]}"
        if "revenue" not in entries or "expenditure" not in entries:
            print(f"{label} 欠落=歳入または歳出のtotal行")
            failures += 1
            continue
        revenue, revenue_unit = entries["revenue"]
        expenditure, expenditure_unit = entries["expenditure"]
        if revenue_unit != expenditure_unit:
            print(
                f"{label} 単位不一致=歳入:{revenue_unit} "
                f"歳出:{expenditure_unit}"
            )
            failures += 1
            continue
        difference = revenue - expenditure
        print(
            f"{label} 歳入={revenue} 歳出={expenditure} "
            f"差額={difference} 単位={revenue_unit}"
        )
        if difference != 0:
            failures += 1
    return failures


def verify(path: Path) -> int:
    if not path.is_file():
        print(f"DB が見つかりません: {path}", file=sys.stderr)
        return 2
    try:
        with _open(path) as connection:
            failures = 0
            failures += _check_balance(connection)
            failures += _check_hierarchy(connection)
            failures += _check_comparison(connection)
            failures += _check_supplement(connection)
    except sqlite3.Error as exc:
        print(f"検算を実行できません: {exc}", file=sys.stderr)
        return 2
    if failures:
        print(f"検算結果=不合格 不一致件数={failures}")
        return 1
    print("検算結果=合格 すべての差額=0")
    return 0


def _manifest_coverage(path: Path) -> dict[str, Any]:
    with _open(path) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS rows,
                MIN(fiscal_year) AS first_year,
                MAX(fiscal_year) AS last_year,
                COUNT(DISTINCT account_name) AS accounts
            FROM budget_line
            """
        ).fetchone()
    return {
        "rows": int(row["rows"]),
        "first_fiscal_year": row["first_year"],
        "last_fiscal_year": row["last_year"],
        "accounts": int(row["accounts"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    try:
        manifest_path, manifest = begin_module_run(
            args.manifest_dir,
            run_type="budget",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={"action": "verify", "database": str(args.database)},
        )
        exit_code = verify(args.database)
        if not args.database.is_file():
            fail_module_run(
                manifest_path,
                manifest,
                f"database not found: {args.database}",
            )
            return exit_code
        finish_verification_run(
            manifest_path,
            manifest,
            database=args.database,
            artifact_kind="budget_database",
            verification_name="budget_reconciliation",
            exit_code=exit_code,
            coverage=_manifest_coverage(args.database),
        )
        return exit_code
    except (OSError, ValueError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(f"manifestを記録できません: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
