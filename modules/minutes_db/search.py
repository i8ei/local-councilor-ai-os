#!/usr/bin/env python3
"""Search a minutes SQLite database with FTS5 and a LIKE fallback."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, TypedDict

from lcaios.database import fts5_table_tokenizer

SearchPath = Literal["fts", "like"]


class SearchReport(TypedDict):
    query: str
    total_matches: int
    truncated: bool
    search_path: SearchPath
    fts_error: str | None
    fallback_reason: str | None


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


def _row_to_result(
    row: sqlite3.Row,
    snippet: str,
    search_path: SearchPath,
) -> dict[str, Any]:
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
        "search_path": search_path,
    }


def _literal_pattern(query: str) -> str:
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _like_search(
    connection: sqlite3.Connection,
    query: str,
    k: int,
) -> tuple[list[dict[str, Any]], int]:
    pattern = _literal_pattern(query)
    parameters = (pattern, pattern)
    total = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM speeches AS s
            JOIN meetings AS m ON m.meeting_id = s.meeting_id
            WHERE s.text LIKE ? ESCAPE '\\'
               OR COALESCE(s.speaker, '') LIKE ? ESCAPE '\\'
            """,
            parameters,
        ).fetchone()[0]
    )
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
        ORDER BY
            CASE
                WHEN COALESCE(s.speaker, '') = ? THEN 0
                WHEN s.text = ? THEN 1
                WHEN instr(COALESCE(s.speaker, ''), ?) > 0 THEN 2
                ELSE 3
            END,
            CASE
                WHEN instr(s.text, ?) > 0 THEN instr(s.text, ?)
                ELSE 2147483647
            END,
            COALESCE(m.date, '') DESC,
            s.seq
        LIMIT ?
        """,
        (*parameters, *(query for _ in range(5)), k),
    ).fetchall()
    return [
        _row_to_result(
            row,
            _plain_snippet(row["text"], query),
            "like",
        )
        for row in rows
    ], total


def _short_trigram_query(
    connection: sqlite3.Connection,
    query: str,
) -> bool:
    if fts5_table_tokenizer(connection, "speeches_fts") != "trigram":
        return False
    literal = query.strip()
    if len(literal) >= 2 and literal.startswith('"') and literal.endswith('"'):
        literal = literal[1:-1]
    return len(literal) < 3


_BENIGN_FTS_ERROR_PATTERNS = ("syntax error", "no such table", "no such column")


def _is_benign_fts_error(exc: sqlite3.OperationalError) -> bool:
    """True for expected FTS states (bad query syntax, absent/older schema).

    Anything else (corruption, disk errors) must surface instead of silently
    degrading every search to the LIKE scan.
    """
    text = str(exc)
    return any(pattern in text for pattern in _BENIGN_FTS_ERROR_PATTERNS)


def search_with_report(
    connection: sqlite3.Connection, query: str, k: int = 10
) -> tuple[list[dict[str, Any]], SearchReport]:
    """Return one search path's results and its execution report."""
    connection.row_factory = sqlite3.Row
    fts_error: str | None = None
    fallback_reason: str | None = None
    if _short_trigram_query(connection, query):
        fallback_reason = "query_too_short_for_trigram"
    else:
        try:
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM speeches_fts
                    JOIN speeches AS s ON s.rowid = speeches_fts.rowid
                    JOIN meetings AS m ON m.meeting_id = s.meeting_id
                    WHERE speeches_fts MATCH ?
                    """,
                    (query,),
                ).fetchone()[0]
            )
            if total:
                rows = connection.execute(
                    """
                    SELECT
                        s.speech_id, s.speaker, s.speaker_role, s.text,
                        s.locator, m.date, m.meeting_name, m.council_name,
                        m.source_url, m.fetched_at,
                        snippet(
                            speeches_fts,
                            0,
                            '【',
                            '】',
                            '…',
                            24
                        ) AS fts_snippet
                    FROM speeches_fts
                    JOIN speeches AS s ON s.rowid = speeches_fts.rowid
                    JOIN meetings AS m ON m.meeting_id = s.meeting_id
                    WHERE speeches_fts MATCH ?
                    ORDER BY bm25(speeches_fts)
                    LIMIT ?
                    """,
                    (query, k),
                ).fetchall()
                results = [
                    _row_to_result(row, row["fts_snippet"], "fts")
                    for row in rows
                ]
                if results:
                    return results, {
                        "query": query,
                        "total_matches": total,
                        "truncated": total > len(results),
                        "search_path": "fts",
                        "fts_error": None,
                        "fallback_reason": None,
                    }
            fallback_reason = "no_fts_matches"
        except sqlite3.OperationalError as exc:
            if not _is_benign_fts_error(exc):
                raise
            fts_error = str(exc)
            fallback_reason = "fts_error"

    results, total = _like_search(connection, query, k)
    return results, {
        "query": query,
        "total_matches": total,
        "truncated": total > len(results),
        "search_path": "like",
        "fts_error": fts_error,
        "fallback_reason": fallback_reason,
    }


def search_database(
    connection: sqlite3.Connection,
    query: str,
    k: int = 10,
) -> list[dict[str, Any]]:
    results, _report = search_with_report(connection, query, k)
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
            results, report = search_with_report(
                connection,
                args.query,
                args.k,
            )
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"report": report, "results": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
