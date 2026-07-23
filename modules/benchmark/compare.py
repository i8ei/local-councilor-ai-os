#!/usr/bin/env python3
"""Compare one official indicator across municipalities in a benchmark DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def compare(connection: sqlite3.Connection, indicator: str, *, as_of: str | None = None, limit: int = 20) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if as_of is None:
        row = connection.execute(
            "SELECT MAX(as_of) FROM benchmark_indicator WHERE indicator_key = ?",
            (indicator,),
        ).fetchone()
        as_of = row[0] if row else None
    if not as_of:
        return {"indicator_key": indicator, "as_of": None, "items": [], "missing_or_unresolved": ["indicator not found"]}
    rows = connection.execute(
        """
        SELECT
            m.area_code_5, m.local_government_code_6, m.name, m.prefecture,
            i.indicator_key, i.value, i.raw_value, i.unit, i.as_of,
            i.definition, i.source_name, i.source_url, i.source_locator,
            i.fetched_at, i.verification_state
        FROM benchmark_indicator AS i
        JOIN benchmark_municipality AS m ON m.area_code_5 = i.area_code_5
        WHERE i.indicator_key = ? AND i.as_of = ?
        ORDER BY
            CASE WHEN i.value IS NULL THEN 1 ELSE 0 END,
            i.value DESC,
            m.prefecture,
            m.name
        LIMIT ?
        """,
        (indicator, as_of, limit),
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        try:
            item["source_locator"] = json.loads(item["source_locator"])
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "indicator_key": indicator,
        "as_of": as_of,
        "order": "value_desc_nulls_last",
        "items": items,
        "external_use_note": "対外利用時は各itemsの value/as_of/definition/source_name/source_url を一体で表示する。",
        "missing_or_unresolved": [] if items else ["indicator/as_of combination not found"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("indicator_key")
    parser.add_argument("--db", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        with sqlite3.connect(Path(args.db)) as connection:
            result = compare(connection, args.indicator_key, as_of=args.as_of, limit=args.limit)
    except (sqlite3.Error, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
