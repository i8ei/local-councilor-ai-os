#!/usr/bin/env python3
"""Build a comparison SQLite database from bootstrap municipality.db files."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

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


def _iter_inputs(paths: Iterable[Path]) -> list[Path]:
    databases: list[Path] = []
    for path in paths:
        if path.is_dir():
            databases.extend(sorted(path.rglob("municipality.db")))
        elif path.is_file():
            databases.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for db in databases:
        resolved = db.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(db)
    return unique


def _read_source(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        municipality_row = connection.execute("SELECT * FROM municipality").fetchone()
        if municipality_row is None:
            raise ValueError(f"municipality row not found: {path}")
        indicators = [dict(row) for row in connection.execute("SELECT * FROM indicator")]
        return dict(municipality_row), indicators


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def build(input_paths: Iterable[Path], output: Path) -> dict[str, Any]:
    sources = _iter_inputs(input_paths)
    if not sources:
        raise ValueError("No municipality.db inputs found")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as target:
        target.execute("PRAGMA foreign_keys = ON")
        ensure_schema(target)
        municipality_count = 0
        indicator_count = 0
        for source in sources:
            municipality, indicators = _read_source(source)
            area_code = str(municipality["area_code_5"])
            target.execute(
                """
                INSERT INTO benchmark_municipality (
                    area_code_5, local_government_code_6, name, prefecture,
                    municipality_kind, source_url, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(area_code_5) DO UPDATE SET
                    local_government_code_6=excluded.local_government_code_6,
                    name=excluded.name,
                    prefecture=excluded.prefecture,
                    municipality_kind=excluded.municipality_kind,
                    source_url=excluded.source_url,
                    fetched_at=excluded.fetched_at
                """,
                (
                    area_code,
                    municipality.get("local_government_code_6"),
                    municipality["name"],
                    municipality["prefecture"],
                    municipality.get("region_level"),
                    municipality.get("source_url"),
                    municipality.get("resolved_at"),
                ),
            )
            municipality_count += 1
            for row in indicators:
                target.execute(
                    """
                    INSERT INTO benchmark_indicator (
                        area_code_5, indicator_key, value, raw_value, unit,
                        as_of, definition, source_name, source_url,
                        source_locator, fetched_at, verification_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(area_code_5, indicator_key, as_of, source_url)
                    DO UPDATE SET
                        value=excluded.value,
                        raw_value=excluded.raw_value,
                        unit=excluded.unit,
                        definition=excluded.definition,
                        source_name=excluded.source_name,
                        source_locator=excluded.source_locator,
                        fetched_at=excluded.fetched_at,
                        verification_state=excluded.verification_state
                    """,
                    (
                        area_code,
                        row["indicator_key"],
                        row["value"],
                        row["raw_value"],
                        row["unit"],
                        row["as_of"],
                        row["definition"],
                        row["source_name"],
                        row["source_url"],
                        row["source_locator"],
                        row["fetched_at"],
                        row["verification_state"],
                    ),
                )
                indicator_count += 1
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "database": str(output),
        "input_databases": [str(path) for path in sources],
        "municipalities": municipality_count,
        "indicators": indicator_count,
        "integrity_check": integrity,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="municipality.db files or directories")
    parser.add_argument("--db", required=True, type=Path, help="Output benchmark SQLite database")
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
            run_type="benchmark",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={
                "inputs": [str(path) for path in args.inputs],
                "database": str(args.db),
            },
        )
        result = build(args.inputs, args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finish_database_run(
        manifest_path,
        manifest,
        database=result["database"],
        artifact_kind="benchmark_database",
        scope={"action": "build"},
        coverage={
            "municipalities": result["municipalities"],
            "indicators": result["indicators"],
        },
        inputs=[
            input_file_record(path, kind="municipality_database")
            for path in result["input_databases"]
        ],
        checks=[
            {
                "name": "municipality_rows",
                "status": (
                    "passed" if result["municipalities"] > 0 else "failed"
                ),
                "detail": result["municipalities"],
            }
        ],
    )
    if manifest_path is not None:
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
