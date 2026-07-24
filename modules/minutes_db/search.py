#!/usr/bin/env python3
"""Search a minutes SQLite database with FTS5 and a LIKE fallback."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any


def _plain_snippet(text: str, query: str, width: int = 140) -> str:
    folded_text = text.casefold()
    index = folded_text.find(query.casefold())
    if index < 0:
        index = 0
    start = max(0, index - width // 3)
    end = min(len(text), start + width)
    excerpt = text[start:end]
    if start:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt += "…"
    return excerpt


def _row_to_result(row: sqlite3.Row, snippet: str) -> dict[str, Any]:
    return {
        "speech_id": row["speech_id"],
        "speaker": row["speaker"],
        "speaker_role": row["speaker_role"],
        "date": row["date"],
        "meeting": row["meeting_name"],
        "council_name": row["council_name"],
        "snippet": snippet,
        "text": row["text"],
        "source_url": row["source_url"],
        "locator": row["locator"],
        "fetched_at": row["fetched_at"],
    }


def search_database(
    connection: sqlite3.Connection, query: str, k: int = 10
) -> list[dict[str, Any]]:
    """Return ranked FTS hits, supplementing with literal LIKE matches."""
    connection.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        rows = connection.execute(
            """
            SELECT
                s.speech_id, s.speaker, s.speaker_role, s.text, s.locator,
                m.date, m.meeting_name, m.council_name, m.source_url,
                m.fetched_at,
                snippet(speeches_fts, 0, '【', '】', '…', 24) AS fts_snippet
            FROM speeches_fts
            JOIN speeches AS s ON s.rowid = speeches_fts.rowid
            JOIN meetings AS m ON m.meeting_id = s.meeting_id
            WHERE speeches_fts MATCH ?
            ORDER BY bm25(speeches_fts)
            LIMIT ?
            """,
            (query, k),
        ).fetchall()
        for row in rows:
            results.append(_row_to_result(row, row["fts_snippet"]))
            seen.add(str(row["speech_id"]))
    except sqlite3.OperationalError:
        # A missing FTS5 module, short trigram query, or FTS syntax error is
        # handled by the literal search below.
        pass

    if len(results) < k:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = connection.execute(
            """
            SELECT
                s.speech_id, s.speaker, s.speaker_role, s.text, s.locator,
                m.date, m.meeting_name, m.council_name, m.source_url,
                m.fetched_at
            FROM speeches AS s
            JOIN meetings AS m ON m.meeting_id = s.meeting_id
            WHERE s.text LIKE ? ESCAPE '\\'
               OR COALESCE(s.speaker, '') LIKE ? ESCAPE '\\'
            ORDER BY COALESCE(m.date, '') DESC, s.seq
            LIMIT ?
            """,
            (f"%{escaped}%", f"%{escaped}%", k * 3),
        ).fetchall()
        for row in rows:
            speech_id = str(row["speech_id"])
            if speech_id in seen:
                continue
            results.append(_row_to_result(row, _plain_snippet(row["text"], query)))
            seen.add(speech_id)
            if len(results) >= k:
                break
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="FTS5 expression or literal fallback text")
    parser.add_argument("--db", required=True, help="Minutes SQLite database")
    parser.add_argument("--k", type=int, default=10, help="Maximum results")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.k < 1:
        print("--k must be at least 1", file=sys.stderr)
        return 2
    try:
        with closing(sqlite3.connect(Path(args.db))) as connection:
            results = search_database(connection, args.query, args.k)
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
