#!/usr/bin/env python3
"""Build a provenance-aware JSON context pack from minutes search hits."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from search import search_database


def _verbatim_excerpt(text: str, query: str, limit: int) -> tuple[str, int, int]:
    """Return an unchanged substring around the first literal query match."""
    if limit >= len(text):
        return text, 0, len(text)
    index = text.casefold().find(query.casefold())
    if index < 0:
        index = 0
    start = max(0, index - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    return text[start:end], start, end


def build_context_pack(
    connection: sqlite3.Connection,
    query: str,
    k: int = 5,
    char_budget: int = 8000,
    question: str | None = None,
) -> dict[str, Any]:
    """Select top hits without exceeding the cumulative quote budget."""
    hits = search_database(connection, query, k)
    items: list[dict[str, Any]] = []
    used = 0
    for hit in hits:
        remaining = char_budget - used
        if remaining <= 0:
            break
        quote, start, end = _verbatim_excerpt(hit["text"], query, remaining)
        if not quote:
            continue
        used += len(quote)
        locator = hit["locator"]
        if start or end < len(hit["text"]):
            locator = f"{locator}; chars:{start}-{end}"
        items.append(
            {
                "evidence_id": hit["speech_id"],
                "quote": quote,
                "quote_is_verbatim": True,
                "speaker": hit["speaker"],
                "speaker_role": hit["speaker_role"],
                "meeting": hit["meeting"],
                "council_name": hit["council_name"],
                "date": hit["date"],
                "source_url": hit["source_url"],
                "locator": locator,
                "fetched_at": hit["fetched_at"],
            }
        )

    created_at = datetime.now(timezone.utc).isoformat()
    pack_key = f"{query}\n{created_at}\n{len(items)}"
    return {
        "schema_version": "minutes-context-pack/1",
        "pack_id": "mcp_" + hashlib.sha256(pack_key.encode()).hexdigest()[:24],
        "purpose": "議事録検索結果から、問いに必要な原典抜粋だけを渡す",
        "question": question or query,
        "created_at": created_at,
        "information_classification": "public",
        "search": {"query": query, "requested_k": k, "selected_hits": len(items)},
        "limits": {
            "quote_character_budget": char_budget,
            "quote_characters_used": used,
        },
        "evidence": items,
        "missing_or_unresolved": (
            [] if len(items) == len(hits) else ["文字数上限により検索結果を省略"]
        ),
        "ai_permissions": {
            "allowed": ["抜粋の要約", "抜粋間の比較", "原典URLの提示"],
            "prohibited": [
                "抜粋にない事実の補完",
                "SQLiteを原典そのものとして扱うこと",
            ],
        },
        "regeneration": {
            "command": f"python3 context_pack.py {json.dumps(query, ensure_ascii=False)} "
            f"--db <minutes.db> --k {k} --char-budget {char_budget}"
            + (
                f" --question {json.dumps(question, ensure_ascii=False)}"
                if question
                else ""
            )
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Question or minutes search expression")
    parser.add_argument("--db", required=True, help="Minutes SQLite database")
    parser.add_argument(
        "--question",
        help="Human question to record separately from the search expression",
    )
    parser.add_argument("--k", type=int, default=5, help="Maximum source hits")
    parser.add_argument(
        "--char-budget",
        type=int,
        default=8000,
        help="Maximum cumulative verbatim quote characters",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.k < 1 or args.char_budget < 1:
        print("--k and --char-budget must be at least 1", file=sys.stderr)
        return 2
    try:
        with sqlite3.connect(Path(args.db)) as connection:
            pack = build_context_pack(
                connection,
                args.query,
                args.k,
                args.char_budget,
                question=args.question,
            )
    except sqlite3.Error as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(pack, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
