#!/usr/bin/env python3
"""Load normalized budget-review CSV rows into SQLite."""

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

REQUIRED = {
    "fiscal_year", "account_name", "budget_stage", "side", "grain",
    "raw_value", "unit", "as_of", "definition", "source_name", "source_url",
    "source_locator", "fetched_at", "verification_state", "print_page", "pdf_page",
}
INTEGER_FIELDS = {
    "fiscal_year", "current_year_amount", "previous_year_amount",
    "comparison_amount", "pre_supplement_amount", "supplement_amount",
    "post_supplement_amount", "pdf_page",
}
CODE_REQUIREMENTS = {
    "total": set(),
    "kan": {"kan_code"},
    "ko": {"kan_code", "ko_code"},
    "moku": {"kan_code", "ko_code", "moku_code"},
    "setsu": {"kan_code", "ko_code", "moku_code", "setsu_code"},
}
STAGES = {"initial", "supplemental", "current"}
SIDES = {"revenue", "expenditure"}


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _none_if_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


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
    for optional in (
        "proposal_no", "kan_code", "kan_name", "ko_code", "ko_name",
        "moku_code", "moku_name", "setsu_code", "setsu_name",
        "current_year_amount", "previous_year_amount", "comparison_amount",
        "pre_supplement_amount", "supplement_amount", "post_supplement_amount",
        "fetch_cache_key", "robots_decision", "request_time",
    ):
        normalized.setdefault(optional, None)
    return normalized


def _validate(row: dict[str, Any], line_no: int) -> None:
    missing = [key for key in sorted(REQUIRED) if row.get(key) is None]
    if missing:
        raise ValueError(f"line {line_no}: missing required fields: {', '.join(missing)}")
    if row["budget_stage"] not in STAGES:
        raise ValueError(f"line {line_no}: invalid budget_stage {row['budget_stage']!r}")
    if row["side"] not in SIDES:
        raise ValueError(f"line {line_no}: invalid side {row['side']!r}")
    if row["grain"] not in CODE_REQUIREMENTS:
        raise ValueError(f"line {line_no}: invalid grain {row['grain']!r}")
    code_missing = [key for key in sorted(CODE_REQUIREMENTS[row["grain"]]) if row.get(key) is None]
    if code_missing:
        raise ValueError(f"line {line_no}: missing code fields for grain: {', '.join(code_missing)}")
    if not any(row.get(key) is not None for key in ("current_year_amount", "supplement_amount", "post_supplement_amount")):
        raise ValueError(f"line {line_no}: one amount column is required")


def ingest_csv(csv_path: Path, db_path: Path) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    loaded = 0
    with sqlite3.connect(db_path) as connection:
        ensure_schema(connection)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"CSV has no header: {csv_path}")
            for line_no, raw in enumerate(reader, start=2):
                row = _normalize_row(raw)
                _validate(row, line_no)
                connection.execute(
                    """
                    INSERT INTO budget_line (
                        fiscal_year, account_name, budget_stage, proposal_no,
                        side, grain, kan_code, kan_name, ko_code, ko_name,
                        moku_code, moku_name, setsu_code, setsu_name,
                        current_year_amount, previous_year_amount,
                        comparison_amount, pre_supplement_amount,
                        supplement_amount, post_supplement_amount, raw_value,
                        unit, as_of, definition, source_name, source_url,
                        source_locator, fetched_at, verification_state,
                        fetch_cache_key, robots_decision, request_time,
                        print_page, pdf_page
                    ) VALUES (
                        :fiscal_year, :account_name, :budget_stage, :proposal_no,
                        :side, :grain, :kan_code, :kan_name, :ko_code, :ko_name,
                        :moku_code, :moku_name, :setsu_code, :setsu_name,
                        :current_year_amount, :previous_year_amount,
                        :comparison_amount, :pre_supplement_amount,
                        :supplement_amount, :post_supplement_amount, :raw_value,
                        :unit, :as_of, :definition, :source_name, :source_url,
                        :source_locator, :fetched_at, :verification_state,
                        :fetch_cache_key, :robots_decision, :request_time,
                        :print_page, :pdf_page
                    )
                    ON CONFLICT DO UPDATE SET
                        kan_name=excluded.kan_name,
                        ko_name=excluded.ko_name,
                        moku_name=excluded.moku_name,
                        setsu_name=excluded.setsu_name,
                        current_year_amount=excluded.current_year_amount,
                        previous_year_amount=excluded.previous_year_amount,
                        comparison_amount=excluded.comparison_amount,
                        pre_supplement_amount=excluded.pre_supplement_amount,
                        supplement_amount=excluded.supplement_amount,
                        post_supplement_amount=excluded.post_supplement_amount,
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
                loaded += 1
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {"database": str(db_path), "rows_loaded": loaded, "integrity_check": integrity}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
            run_type="budget",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={"csv": str(args.csv), "database": str(args.db)},
        )
        result = ingest_csv(args.csv, args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finish_database_run(
        manifest_path,
        manifest,
        database=result["database"],
        artifact_kind="budget_database",
        scope={"action": "ingest"},
        coverage={"rows_loaded": result["rows_loaded"]},
        inputs=[input_file_record(args.csv, kind="budget_normalized_csv")],
        checks=[
            {
                "name": "budget_rows",
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
