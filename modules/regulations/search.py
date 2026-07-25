#!/usr/bin/env python3
"""Search a regulations SQLite database with FTS5 and a literal fallback."""

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


def _snippet(text: str, query: str, width: int = 160) -> str:
    index = text.casefold().find(query.casefold())
    if index < 0:
        index = 0
    start = max(0, index - width // 3)
    end = min(len(text), start + width)
    value = text[start:end]
    if start:
        value = "…" + value
    if end < len(text):
        value += "…"
    return value


def _row(
    row: sqlite3.Row,
    snippet: str,
    search_path: SearchPath,
) -> dict[str, Any]:
    return {
        "article_id": row["article_id"],
        "document_id": row["document_id"],
        "title": row["title"],
        "article_no": row["article_no"],
        "heading": row["heading"],
        "snippet": snippet,
        "text": row["text"],
        "source_url": row["source_url"],
        "locator": row["locator"],
        "fetched_at": row["fetched_at"],
        "verification_state": row["verification_state"],
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
    parameters = (pattern, pattern, pattern, pattern)
    total = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM regulation_articles AS a
            JOIN regulation_documents AS d ON d.document_id = a.document_id
            WHERE a.text LIKE ? ESCAPE '\\'
               OR COALESCE(a.heading, '') LIKE ? ESCAPE '\\'
               OR COALESCE(a.article_no, '') LIKE ? ESCAPE '\\'
               OR d.title LIKE ? ESCAPE '\\'
            """,
            parameters,
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT
            a.article_id, a.document_id, a.article_no, a.heading, a.text,
            a.locator, d.title, d.source_url, d.fetched_at,
            d.verification_state
        FROM regulation_articles AS a
        JOIN regulation_documents AS d ON d.document_id = a.document_id
        WHERE a.text LIKE ? ESCAPE '\\'
           OR COALESCE(a.heading, '') LIKE ? ESCAPE '\\'
           OR COALESCE(a.article_no, '') LIKE ? ESCAPE '\\'
           OR d.title LIKE ? ESCAPE '\\'
        ORDER BY
            CASE
                WHEN COALESCE(a.article_no, '') = ? THEN 0
                WHEN COALESCE(a.heading, '') = ? THEN 1
                WHEN d.title = ? THEN 2
                WHEN a.text = ? THEN 3
                WHEN instr(COALESCE(a.heading, ''), ?) > 0 THEN 4
                WHEN instr(d.title, ?) > 0 THEN 5
                ELSE 6
            END,
            CASE
                WHEN instr(a.text, ?) > 0 THEN instr(a.text, ?)
                ELSE 2147483647
            END,
            d.title,
            a.seq
        LIMIT ?
        """,
        (*parameters, *(query for _ in range(8)), k),
    ).fetchall()
    return [
        _row(item, _snippet(item["text"], query), "like")
        for item in rows
    ], total


def _short_trigram_query(
    connection: sqlite3.Connection,
    query: str,
) -> bool:
    if fts5_table_tokenizer(
        connection,
        "regulation_articles_fts",
    ) != "trigram":
        return False
    literal = query.strip()
    if len(literal) >= 2 and literal.startswith('"') and literal.endswith('"'):
        literal = literal[1:-1]
    return len(literal) < 3


def search_with_report(
    connection: sqlite3.Connection,
    query: str,
    k: int = 10,
) -> tuple[list[dict[str, Any]], SearchReport]:
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
                    FROM regulation_articles_fts
                    JOIN regulation_articles AS a
                      ON a.article_id = regulation_articles_fts.article_id
                    JOIN regulation_documents AS d
                      ON d.document_id = a.document_id
                    WHERE regulation_articles_fts MATCH ?
                    """,
                    (query,),
                ).fetchone()[0]
            )
            if total:
                rows = connection.execute(
                    """
                    SELECT
                        a.article_id, a.document_id, a.article_no, a.heading,
                        a.text, a.locator, d.title, d.source_url, d.fetched_at,
                        d.verification_state,
                        snippet(
                            regulation_articles_fts,
                            0,
                            '【',
                            '】',
                            '…',
                            24
                        ) AS fts_snippet
                    FROM regulation_articles_fts
                    JOIN regulation_articles AS a
                      ON a.article_id = regulation_articles_fts.article_id
                    JOIN regulation_documents AS d
                      ON d.document_id = a.document_id
                    WHERE regulation_articles_fts MATCH ?
                    ORDER BY bm25(regulation_articles_fts)
                    LIMIT ?
                    """,
                    (query, k),
                ).fetchall()
                results = [
                    _row(item, item["fts_snippet"], "fts")
                    for item in rows
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
    parser.add_argument("query")
    parser.add_argument("--db", required=True)
    parser.add_argument("--k", type=int, default=10)
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
