"""Build the provenance-first municipality SQLite database."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE municipality (
    area_code_5 TEXT PRIMARY KEY CHECK(length(area_code_5) = 5),
    local_government_code_6 TEXT NOT NULL UNIQUE
        CHECK(length(local_government_code_6) = 6),
    name TEXT NOT NULL,
    prefecture TEXT NOT NULL,
    prefecture_code_2 TEXT NOT NULL CHECK(length(prefecture_code_2) = 2),
    region_level TEXT NOT NULL,
    resolved_from TEXT NOT NULL,
    source_url TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);

CREATE TABLE indicator (
    id INTEGER PRIMARY KEY,
    municipality_code TEXT NOT NULL
        REFERENCES municipality(area_code_5),
    indicator_key TEXT NOT NULL,
    value REAL,
    raw_value TEXT NOT NULL,
    unit TEXT NOT NULL,
    as_of TEXT NOT NULL,
    definition TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    UNIQUE(municipality_code, indicator_key, as_of, source_url)
);

CREATE INDEX indicator_lookup
ON indicator(municipality_code, indicator_key, as_of);

CREATE TABLE build_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DatabaseError(RuntimeError):
    """Raised when the generated database fails validation."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _verification_state(record: dict[str, Any]) -> str:
    check = record.get("source_locator", {}).get("cross_check", {})
    if check.get("state") in {"matched", "matched_missing"}:
        return "reconciled"
    if check.get("state") == "mismatch":
        return "needs_review"
    return "verified_source_extraction"


def build_database(
    municipality: dict[str, Any],
    records: Iterable[dict[str, Any]],
    metadata: dict[str, Any],
    output_path: str | Path,
) -> dict[str, Any]:
    """Build a new database beside the prior one, verify it, then replace."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".new")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        connection.execute(
            """
            INSERT INTO municipality (
                area_code_5, local_government_code_6, name, prefecture,
                prefecture_code_2, region_level, resolved_from, source_url,
                resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                municipality["area_code_5"],
                municipality["local_government_code_6"],
                municipality["name"],
                municipality["prefecture"],
                municipality["prefecture_code_2"],
                municipality["region_level"],
                municipality["resolved_from"],
                municipality["source_url"],
                municipality["resolved_at"],
            ),
        )
        row_count = 0
        for record in records:
            required = (
                "indicator",
                "raw_value",
                "unit",
                "as_of",
                "definition",
                "source_name",
                "source_url",
                "source_locator",
                "fetched_at",
            )
            missing = [
                key
                for key in required
                if record.get(key) is None or record.get(key) == ""
            ]
            if missing:
                raise DatabaseError(
                    f"{record.get('indicator', 'unknown')} の必須来歴が空です: {missing}"
                )
            connection.execute(
                """
                INSERT INTO indicator (
                    municipality_code, indicator_key, value, raw_value, unit,
                    as_of, definition, source_name, source_url, source_locator,
                    fetched_at, verification_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    municipality["area_code_5"],
                    record["indicator"],
                    record.get("value"),
                    str(record["raw_value"]),
                    str(record["unit"]),
                    str(record["as_of"]),
                    str(record["definition"]),
                    str(record["source_name"]),
                    str(record["source_url"]),
                    json.dumps(
                        record["source_locator"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(record["fetched_at"]),
                    _verification_state(record),
                ),
            )
            row_count += 1
        build_metadata = {
            "schema_version": "1",
            "built_at": _utc_now(),
            **{
                key: (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False, sort_keys=True)
                )
                for key, value in metadata.items()
            },
        }
        connection.executemany(
            "INSERT INTO build_metadata(key, value) VALUES (?, ?)",
            build_metadata.items(),
        )
        connection.commit()
        integrity = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        if integrity != "ok":
            raise DatabaseError(f"SQLite integrity_check failed: {integrity}")
        actual_count = int(
            connection.execute("SELECT count(*) FROM indicator").fetchone()[0]
        )
        if actual_count != row_count:
            raise DatabaseError(
                f"指標行数が一致しません: expected={row_count}, actual={actual_count}"
            )
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    os.replace(temporary, output)
    return {
        "path": str(output),
        "indicator_rows": row_count,
        "integrity_check": "ok",
    }
