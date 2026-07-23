#!/usr/bin/env python3
"""Search a regulations SQLite database with FTS5 and a literal fallback."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


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


def _row(row: sqlite3.Row, snippet: str) -> dict[str, Any]:
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
    }


def search_database(connection: sqlite3.Connection, query: str, k: int = 10) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        rows = connection.execute(
            """
            SELECT
                a.article_id, a.document_id, a.article_no, a.heading, a.text,
                a.locator, d.title, d.source_url, d.fetched_at,
                d.verification_state,
                snippet(regulation_articles_fts, 0, '【', '】', '…', 24) AS fts_snippet
            FROM regulation_articles_fts
            JOIN regulation_articles AS a
              ON a.article_id = regulation_articles_fts.article_id
            JOIN regulation_documents AS d ON d.document_id = a.document_id
            WHERE regulation_articles_fts MATCH ?
            ORDER BY bm25(regulation_articles_fts)
            LIMIT ?
            """,
            (query, k),
        ).fetchall()
        for item in rows:
            results.append(_row(item, item["fts_snippet"]))
            seen.add(str(item["article_id"]))
    except sqlite3.OperationalError:
        pass
    if len(results) < k:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
            ORDER BY d.title, a.seq
            LIMIT ?
            """,
            (f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", k * 3),
        ).fetchall()
        for item in rows:
            article_id = str(item["article_id"])
            if article_id in seen:
                continue
            results.append(_row(item, _snippet(item["text"], query)))
            seen.add(article_id)
            if len(results) >= k:
                break
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
        with sqlite3.connect(Path(args.db)) as connection:
            results = search_database(connection, args.query, args.k)
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
