#!/usr/bin/env python3
"""Load normalized settlement CSV rows into the settlement review SQLite schema."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lcaios.module_manifest import (  # noqa: E402
    begin_module_run,
    fail_module_run,
    finish_database_run,
    input_file_record,
)

SCHEMA_PATH = MODULE_DIR / "schema.sql"

REQUIRED_COMMON = {
    "fiscal_year", "account_name", "raw_value", "unit", "as_of", "definition",
    "source_name", "source_url", "source_locator", "fetched_at",
    "verification_state", "print_page", "pdf_page",
}
REQUIRED_SUMMARY = REQUIRED_COMMON | {"side", "kan_code", "kan_name", "budget_current_amount"}
REQUIRED_REVENUE = REQUIRED_COMMON | {
    "kan_code", "kan_name", "ko_code", "ko_name", "budget_current_amount",
    "collected_amount", "uncollectible_amount", "outstanding_amount",
}
REQUIRED_EXPENDITURE = REQUIRED_COMMON | {
    "kan_code", "kan_name", "ko_code", "ko_name", "moku_code", "moku_name",
    "setsu_code", "setsu_name", "item_budget_current_amount",
    "item_spent_amount", "item_carryover_amount", "item_unused_amount",
    "section_budget_current_amount", "section_spent_amount",
    "section_carryover_amount", "section_unused_amount",
}
INTEGER_FIELDS = {
    "fiscal_year", "budget_current_amount", "collected_amount",
    "uncollectible_amount", "outstanding_amount", "spent_amount",
    "carryover_amount", "unused_amount", "pdf_page", "block_no",
    "item_budget_current_amount", "item_spent_amount", "item_carryover_amount",
    "item_unused_amount", "section_budget_current_amount", "section_spent_amount",
    "section_carryover_amount", "section_unused_amount",
}


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _none_if_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped != "" else None


def _integer(value: str | None, field: str) -> int | None:
    cleaned = _none_if_empty(value)
    if cleaned is None:
        return None
    normalized = cleaned.replace(",", "").replace("△", "-").replace("−", "-")
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer-compatible value: {value!r}") from exc


def _json_locator(value: str | None) -> str:
    cleaned = _none_if_empty(value)
    if cleaned is None:
        raise ValueError("source_locator is required")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = {"locator": cleaned}
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _normalize_row(row: dict[str, str | None]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if key in INTEGER_FIELDS:
            normalized[key] = _integer(value, key)
        elif key == "source_locator":
            normalized[key] = _json_locator(value)
        else:
            normalized[key] = _none_if_empty(value)
    return normalized


def _require(row: dict[str, Any], required: set[str], label: str, line_no: int) -> None:
    missing = [key for key in sorted(required) if row.get(key) is None]
    if missing:
        raise ValueError(f"{label} line {line_no}: missing required fields: {', '.join(missing)}")


def _insert_summary(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    side = row.get("side")
    if side not in {"revenue", "expenditure"}:
        raise ValueError("summary side must be revenue or expenditure")
    if side == "revenue":
        required = REQUIRED_SUMMARY | {"collected_amount", "uncollectible_amount", "outstanding_amount"}
    else:
        required = REQUIRED_SUMMARY | {"spent_amount", "carryover_amount", "unused_amount"}
    _require(row, required, "summary", int(row.get("_line_no", 0)))
    connection.execute(
        """
        INSERT INTO settlement_summary (
            fiscal_year, account_name, side, kan_code, kan_name,
            budget_current_amount, collected_amount, uncollectible_amount,
            outstanding_amount, spent_amount, carryover_amount, unused_amount,
            raw_value, unit, as_of, definition, source_name, source_url,
            source_locator, fetched_at, verification_state, fetch_cache_key,
            robots_decision, request_time, print_page, pdf_page
        ) VALUES (
            :fiscal_year, :account_name, :side, :kan_code, :kan_name,
            :budget_current_amount, :collected_amount, :uncollectible_amount,
            :outstanding_amount, :spent_amount, :carryover_amount, :unused_amount,
            :raw_value, :unit, :as_of, :definition, :source_name, :source_url,
            :source_locator, :fetched_at, :verification_state, :fetch_cache_key,
            :robots_decision, :request_time, :print_page, :pdf_page
        )
        ON CONFLICT(fiscal_year, account_name, side, kan_code) DO UPDATE SET
            kan_name=excluded.kan_name,
            budget_current_amount=excluded.budget_current_amount,
            collected_amount=excluded.collected_amount,
            uncollectible_amount=excluded.uncollectible_amount,
            outstanding_amount=excluded.outstanding_amount,
            spent_amount=excluded.spent_amount,
            carryover_amount=excluded.carryover_amount,
            unused_amount=excluded.unused_amount,
            raw_value=excluded.raw_value,
            unit=excluded.unit,
            as_of=excluded.as_of,
            definition=excluded.definition,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            source_locator=excluded.source_locator,
            fetched_at=excluded.fetched_at,
            verification_state=excluded.verification_state,
            fetch_cache_key=excluded.fetch_cache_key,
            robots_decision=excluded.robots_decision,
            request_time=excluded.request_time,
            print_page=excluded.print_page,
            pdf_page=excluded.pdf_page
        """,
        row,
    )


def _insert_revenue(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    _require(row, REQUIRED_REVENUE, "revenue", int(row.get("_line_no", 0)))
    connection.execute(
        """
        INSERT INTO settlement_revenue (
            fiscal_year, account_name, kan_code, kan_name, ko_code, ko_name,
            budget_current_amount, collected_amount, uncollectible_amount,
            outstanding_amount, raw_value, unit, as_of, definition,
            source_name, source_url, source_locator, fetched_at,
            verification_state, fetch_cache_key, robots_decision, request_time,
            print_page, pdf_page
        ) VALUES (
            :fiscal_year, :account_name, :kan_code, :kan_name, :ko_code, :ko_name,
            :budget_current_amount, :collected_amount, :uncollectible_amount,
            :outstanding_amount, :raw_value, :unit, :as_of, :definition,
            :source_name, :source_url, :source_locator, :fetched_at,
            :verification_state, :fetch_cache_key, :robots_decision, :request_time,
            :print_page, :pdf_page
        )
        ON CONFLICT(fiscal_year, account_name, kan_code, ko_code) DO UPDATE SET
            kan_name=excluded.kan_name,
            ko_name=excluded.ko_name,
            budget_current_amount=excluded.budget_current_amount,
            collected_amount=excluded.collected_amount,
            uncollectible_amount=excluded.uncollectible_amount,
            outstanding_amount=excluded.outstanding_amount,
            raw_value=excluded.raw_value,
            unit=excluded.unit,
            as_of=excluded.as_of,
            definition=excluded.definition,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            source_locator=excluded.source_locator,
            fetched_at=excluded.fetched_at,
            verification_state=excluded.verification_state,
            fetch_cache_key=excluded.fetch_cache_key,
            robots_decision=excluded.robots_decision,
            request_time=excluded.request_time,
            print_page=excluded.print_page,
            pdf_page=excluded.pdf_page
        """,
        row,
    )


def _insert_expenditure(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    row.setdefault("block_no", 1)
    if row.get("block_no") is None:
        row["block_no"] = 1
    _require(row, REQUIRED_EXPENDITURE | {"block_no"}, "expenditure", int(row.get("_line_no", 0)))
    connection.execute(
        """
        INSERT INTO settlement_expenditure (
            fiscal_year, account_name, kan_code, kan_name, ko_code, ko_name,
            moku_code, moku_name, setsu_code, setsu_name, block_no,
            item_budget_current_amount, item_spent_amount,
            item_carryover_amount, item_unused_amount,
            section_budget_current_amount, section_spent_amount,
            section_carryover_amount, section_unused_amount,
            raw_value, unit, as_of, definition, source_name, source_url,
            source_locator, fetched_at, verification_state, fetch_cache_key,
            robots_decision, request_time, print_page, pdf_page
        ) VALUES (
            :fiscal_year, :account_name, :kan_code, :kan_name, :ko_code, :ko_name,
            :moku_code, :moku_name, :setsu_code, :setsu_name, :block_no,
            :item_budget_current_amount, :item_spent_amount,
            :item_carryover_amount, :item_unused_amount,
            :section_budget_current_amount, :section_spent_amount,
            :section_carryover_amount, :section_unused_amount,
            :raw_value, :unit, :as_of, :definition, :source_name, :source_url,
            :source_locator, :fetched_at, :verification_state, :fetch_cache_key,
            :robots_decision, :request_time, :print_page, :pdf_page
        )
        ON CONFLICT(
            fiscal_year, account_name, kan_code, ko_code, moku_code,
            setsu_code, block_no
        ) DO UPDATE SET
            kan_name=excluded.kan_name,
            ko_name=excluded.ko_name,
            moku_name=excluded.moku_name,
            setsu_name=excluded.setsu_name,
            item_budget_current_amount=excluded.item_budget_current_amount,
            item_spent_amount=excluded.item_spent_amount,
            item_carryover_amount=excluded.item_carryover_amount,
            item_unused_amount=excluded.item_unused_amount,
            section_budget_current_amount=excluded.section_budget_current_amount,
            section_spent_amount=excluded.section_spent_amount,
            section_carryover_amount=excluded.section_carryover_amount,
            section_unused_amount=excluded.section_unused_amount,
            raw_value=excluded.raw_value,
            unit=excluded.unit,
            as_of=excluded.as_of,
            definition=excluded.definition,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            source_locator=excluded.source_locator,
            fetched_at=excluded.fetched_at,
            verification_state=excluded.verification_state,
            fetch_cache_key=excluded.fetch_cache_key,
            robots_decision=excluded.robots_decision,
            request_time=excluded.request_time,
            print_page=excluded.print_page,
            pdf_page=excluded.pdf_page
        """,
        row,
    )


def ingest_csv(kind: str, csv_path: Path, db_path: Path) -> dict[str, Any]:
    if kind not in {"summary", "revenue", "expenditure"}:
        raise ValueError("kind must be summary, revenue, or expenditure")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    inserted = 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_schema(connection)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"CSV has no header: {csv_path}")
            for line_no, raw in enumerate(reader, start=2):
                row = _normalize_row(raw)
                row["_line_no"] = line_no
                for optional in (
                    "collected_amount", "uncollectible_amount", "outstanding_amount",
                    "spent_amount", "carryover_amount", "unused_amount",
                    "fetch_cache_key", "robots_decision", "request_time", "block_no",
                ):
                    row.setdefault(optional, None)
                if kind == "summary":
                    _insert_summary(connection, row)
                elif kind == "revenue":
                    _insert_revenue(connection, row)
                else:
                    _insert_expenditure(connection, row)
                inserted += 1
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {"database": str(db_path), "kind": kind, "rows_loaded": inserted, "integrity_check": integrity}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("summary", "revenue", "expenditure"))
    parser.add_argument("csv", type=Path)
    parser.add_argument("--db", required=True, type=Path)
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
            run_type="settlement",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={
                "kind": args.kind,
                "csv": str(args.csv),
                "database": str(args.db),
            },
        )
        result = ingest_csv(args.kind, args.csv, args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finish_database_run(
        manifest_path,
        manifest,
        database=result["database"],
        artifact_kind="settlement_database",
        scope={"action": "ingest", "kind": args.kind},
        coverage={"kind": args.kind, "rows_loaded": result["rows_loaded"]},
        inputs=[input_file_record(args.csv, kind=f"settlement_{args.kind}_csv")],
        checks=[
            {
                "name": "settlement_rows",
                "status": "passed" if result["rows_loaded"] > 0 else "failed",
                "detail": result["rows_loaded"],
            }
        ],
    )
    if manifest_path is not None:
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
