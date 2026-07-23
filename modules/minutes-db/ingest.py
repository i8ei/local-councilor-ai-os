#!/usr/bin/env python3
"""Ingest normalized municipal minutes into SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from adapters.kaigiroku_net import KaigirokuNetAdapter
from adapters.static_html import StaticHtmlAdapter

MODULE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def stable_id(prefix: str, value: str) -> str:
    """Build a deterministic, readable identifier."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _supports_trigram(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE temp.__trigram_probe "
            "USING fts5(value, tokenize='trigram')"
        )
        connection.execute("DROP TABLE temp.__trigram_probe")
        return True
    except sqlite3.OperationalError:
        return False


def ensure_schema(connection: sqlite3.Connection) -> str:
    """Create the schema, preferring trigram when this SQLite supports it."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    tokenizer = "trigram" if _supports_trigram(connection) else "unicode61"
    if tokenizer == "trigram":
        schema = schema.replace("tokenize='unicode61'", "tokenize='trigram'", 1)
    try:
        connection.executescript(schema)
    except sqlite3.OperationalError as exc:
        if "fts5" not in str(exc).lower():
            raise
        # Keep the relational search layer usable on SQLite builds without FTS5.
        relational_schema = schema.split(
            "CREATE VIRTUAL TABLE IF NOT EXISTS speeches_fts", 1
        )[0]
        connection.executescript(relational_schema)
        tokenizer = "unavailable"
    return tokenizer


def _as_json(value: Any, default: Any) -> str:
    if value in (None, ""):
        value = default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def store_meeting(connection: sqlite3.Connection, document: dict[str, Any]) -> int:
    """Upsert one normalized meeting and its speech/provenance records."""
    meeting = document.get("meeting", document)
    source_url = str(meeting["source_url"])
    proposed_id = str(
        meeting.get("meeting_id") or stable_id("meeting", source_url)
    )
    connection.execute(
        """
        INSERT INTO meetings (
            meeting_id, council_name, meeting_name, session, date,
            source_url, adapter, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            council_name=excluded.council_name,
            meeting_name=excluded.meeting_name,
            session=excluded.session,
            date=excluded.date,
            adapter=excluded.adapter,
            fetched_at=excluded.fetched_at
        """,
        (
            proposed_id,
            meeting.get("council_name") or "",
            meeting.get("meeting_name") or "",
            meeting.get("session"),
            meeting.get("date"),
            source_url,
            meeting.get("adapter") or document.get("adapter") or "unknown",
            meeting["fetched_at"],
        ),
    )
    row = connection.execute(
        "SELECT meeting_id FROM meetings WHERE source_url = ?", (source_url,)
    ).fetchone()
    meeting_id = str(row[0])

    stored = 0
    for position, speech in enumerate(document.get("speeches") or [], start=1):
        seq = int(speech.get("seq", position))
        speech_id = str(
            speech.get("speech_id")
            or stable_id("speech", f"{source_url}#{seq}")
        )
        locator = str(speech.get("locator") or f"speech:{seq}")
        connection.execute(
            """
            INSERT INTO speeches (
                speech_id, meeting_id, seq, speaker, speaker_role, text, locator
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id, seq) DO UPDATE SET
                speaker=excluded.speaker,
                speaker_role=excluded.speaker_role,
                text=excluded.text,
                locator=excluded.locator
            """,
            (
                speech_id,
                meeting_id,
                seq,
                speech.get("speaker"),
                speech.get("speaker_role"),
                str(speech.get("text") or ""),
                locator,
            ),
        )
        stored += 1

    provenance = document.get("provenance") or {}
    resolved_url = str(provenance.get("resolved_url") or source_url)
    provenance_id = str(
        provenance.get("provenance_id")
        or stable_id("provenance", f"{meeting_id}:{resolved_url}")
    )
    connection.execute(
        """
        INSERT INTO provenance (
            provenance_id, meeting_id, discovered_from, resolved_url,
            fetched_at, media_type, content_sha256, adapter, transform,
            status, cache_path, issues_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(meeting_id, resolved_url) DO UPDATE SET
            discovered_from=excluded.discovered_from,
            fetched_at=excluded.fetched_at,
            media_type=excluded.media_type,
            content_sha256=excluded.content_sha256,
            adapter=excluded.adapter,
            transform=excluded.transform,
            status=excluded.status,
            cache_path=excluded.cache_path,
            issues_json=excluded.issues_json
        """,
        (
            provenance_id,
            meeting_id,
            provenance.get("discovered_from"),
            resolved_url,
            provenance.get("fetched_at") or meeting["fetched_at"],
            provenance.get("media_type"),
            provenance.get("content_sha256") or provenance.get("sha256"),
            meeting.get("adapter") or document.get("adapter") or "unknown",
            _as_json(
                provenance.get("transform"),
                {"pipeline": "adapter -> normalized meeting/speeches -> SQLite"},
            ),
            provenance.get("status") or "discovered",
            provenance.get("cache_path"),
            _as_json(
                provenance.get("issues") or provenance.get("issues_json"), []
            ),
        ),
    )
    return stored


def _make_adapter(args: argparse.Namespace) -> Any:
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if args.adapter == "kaigiroku_net":
        if not args.url:
            raise ValueError("--url is required for kaigiroku_net")
        return KaigirokuNetAdapter(args.url, cache_dir=cache_dir)
    if not args.config:
        raise ValueError("--config is required for static")
    return StaticHtmlAdapter.from_config(args.config, cache_dir=cache_dir)


def ingest(args: argparse.Namespace) -> dict[str, Any]:
    adapter = _make_adapter(args)
    database_path = Path(args.db)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        tokenizer = ensure_schema(connection)
        meeting_refs = adapter.list_meetings(limit=args.limit)
        meeting_count = 0
        speech_count = 0
        statuses: dict[str, int] = {}
        for ref in meeting_refs:
            meeting_ref = ref.get("meeting_id", ref) if isinstance(ref, dict) else ref
            document = adapter.fetch_meeting(meeting_ref)
            speech_count += store_meeting(connection, document)
            status = str((document.get("provenance") or {}).get("status", "discovered"))
            statuses[status] = statuses.get(status, 0) + 1
            meeting_count += 1
        if tokenizer != "unavailable":
            connection.execute(
                "INSERT INTO speeches_fts(speeches_fts) VALUES ('rebuild')"
            )
        connection.commit()
    return {
        "adapter": args.adapter,
        "database": str(database_path),
        "meetings": meeting_count,
        "speeches": speech_count,
        "statuses": statuses,
        "fts_tokenizer": tokenizer,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adapter",
        required=True,
        choices=("kaigiroku_net", "static"),
    )
    parser.add_argument("--url", help="Tenant or minutes URL")
    parser.add_argument("--config", help="Static adapter JSON config")
    parser.add_argument("--db", required=True, help="Output SQLite database")
    parser.add_argument("--limit", type=int, default=None, help="Meeting limit")
    parser.add_argument("--cache-dir", help="Override the local cache directory")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = ingest(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
