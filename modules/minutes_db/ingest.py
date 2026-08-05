#!/usr/bin/env python3
"""Ingest normalized municipal minutes into SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from lcaios.database import (
    FtsSchemaStatus,
    fts5_table_tokenizer,
    supports_fts5,
    supports_fts5_trigram,
)
from lcaios.http import MINUTES_USER_AGENT, HttpClient
from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_database_run,
    finish_dry_run,
    input_file_record,
)

from .adapters.kaigiroku_net import KaigirokuNetAdapter
from .adapters.static_html import StaticHtmlAdapter
from .coverage import diagnose_coverage, validate_coverage_config

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
SCHEMA_PATH = MODULE_DIR / "schema.sql"
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache"


def stable_id(prefix: str, value: str) -> str:
    """Build a deterministic, readable identifier."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _rebuild_fts(
    connection: sqlite3.Connection,
    tokenizer: str,
) -> None:
    connection.execute("SAVEPOINT rebuild_speeches_fts")
    try:
        connection.execute("DROP TABLE speeches_fts")
        connection.execute(
            f"""
            CREATE VIRTUAL TABLE speeches_fts USING fts5(
                text,
                speaker,
                meeting_id UNINDEXED,
                speech_id UNINDEXED,
                content='speeches',
                content_rowid='rowid',
                tokenize='{tokenizer}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO speeches_fts (
                rowid, text, speaker, meeting_id, speech_id
            )
            SELECT rowid, text, speaker, meeting_id, speech_id
            FROM speeches
            """
        )
    except sqlite3.Error:
        connection.execute("ROLLBACK TO rebuild_speeches_fts")
        connection.execute("RELEASE rebuild_speeches_fts")
        raise
    connection.execute("RELEASE rebuild_speeches_fts")


def ensure_schema(connection: sqlite3.Connection) -> FtsSchemaStatus:
    """Create the schema, preferring trigram when this SQLite supports it."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    relational_schema = schema.split(
        "CREATE VIRTUAL TABLE IF NOT EXISTS speeches_fts",
        1,
    )[0]
    previous = fts5_table_tokenizer(connection, "speeches_fts")
    if supports_fts5_trigram(connection):
        tokenizer = "trigram"
    elif supports_fts5(connection):
        tokenizer = "unicode61"
    else:
        connection.executescript(relational_schema)
        return {
            "tokenizer": "unavailable",
            "rebuilt": False,
            "previous_tokenizer": previous,
        }
    if tokenizer == "trigram":
        schema = schema.replace("tokenize='unicode61'", "tokenize='trigram'", 1)
    try:
        connection.executescript(schema)
    except sqlite3.OperationalError as exc:
        if "fts5" not in str(exc).lower():
            raise
        # Keep the relational search layer usable on SQLite builds without FTS5.
        connection.executescript(relational_schema)
        return {
            "tokenizer": "unavailable",
            "rebuilt": False,
            "previous_tokenizer": previous,
        }

    current = fts5_table_tokenizer(connection, "speeches_fts")
    rebuilt = current != tokenizer
    if rebuilt:
        _rebuild_fts(connection, tokenizer)
    return {
        "tokenizer": tokenizer,
        "rebuilt": rebuilt,
        "previous_tokenizer": previous,
    }


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


def _make_adapter(args: argparse.Namespace, client: HttpClient) -> Any:
    if args.adapter == "kaigiroku_net":
        if not args.url:
            raise ValueError("--url is required for kaigiroku_net")
        return KaigirokuNetAdapter(args.url, client=client)
    if not args.config:
        raise ValueError("--config is required for static")
    return StaticHtmlAdapter.from_config(args.config, client=client)


def ingest(args: argparse.Namespace) -> dict[str, Any]:
    client = HttpClient(
        Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR,
        user_agent=MINUTES_USER_AGENT,
        offline=args.offline,
        refresh=args.refresh,
        timeout=args.timeout,
    )
    adapter = _make_adapter(args, client)
    if args.dry_run:
        if args.adapter != "static":
            raise ValueError("--dry-run is currently supported only for static")
        meeting_refs = adapter.list_meetings(limit=args.limit)
        return {
            "adapter": args.adapter,
            "status": "dry_run",
            "database": None,
            "database_created": False,
            "meeting_bodies_fetched": 0,
            "documents_downloaded": 0,
            "selected_meetings": meeting_refs,
            "candidates": adapter.discovery_candidates,
            "note": (
                "索引とfollow対象HTMLだけを確認。会議本文・PDF・DBは未取得"
            ),
            "retrieval": client.retrieval_report(),
        }
    if not args.db:
        raise ValueError("--db is required unless --dry-run is used")
    database_path = Path(args.db)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        fts_schema = ensure_schema(connection)
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
        if fts_schema["tokenizer"] != "unavailable":
            connection.execute(
                "INSERT INTO speeches_fts(speeches_fts) VALUES ('rebuild')"
            )
        connection.commit()
        coverage_options = validate_coverage_config(
            getattr(adapter, "config", {}).get("coverage")
        )
        coverage_diagnostics = diagnose_coverage(
            connection,
            options=coverage_options,
            candidate_sessions=getattr(
                adapter,
                "coverage_candidate_sessions",
                None,
            ),
        )
    return {
        "adapter": args.adapter,
        "database": str(database_path),
        "meetings": meeting_count,
        "speeches": speech_count,
        "statuses": statuses,
        "fts_tokenizer": fts_schema["tokenizer"],
        "fts_schema": fts_schema,
        "coverage_diagnostics": coverage_diagnostics,
        "retrieval": client.retrieval_report(),
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
    parser.add_argument("--db", help="Output SQLite database")
    parser.add_argument("--limit", type=int, default=None, help="Meeting limit")
    parser.add_argument("--cache-dir", help="Override the local cache directory")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ネットワークを使わず検証済みローカルキャッシュだけを使う",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="検証済みcacheがあっても公式参照先を再取得する",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90,
        help="取得タイムアウト秒",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List static candidates without fetching meeting bodies or creating a DB",
    )
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    try:
        manifest_path, manifest = begin_module_run(
            args.manifest_dir,
            run_type="minutes",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={
                "adapter": args.adapter,
                "url": args.url,
                "config": args.config,
                "database": args.db,
                "limit": args.limit,
                "cache_directory": (
                    args.cache_dir or str(DEFAULT_CACHE_DIR)
                ),
                "dry_run": args.dry_run,
                "offline": args.offline,
                "refresh": args.refresh,
                "timeout": args.timeout,
            },
        )
        result = ingest(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if manifest is not None:
        manifest["retrieval"] = result["retrieval"]
    if args.dry_run:
        finish_dry_run(
            manifest_path,
            manifest,
            scope={"adapter": args.adapter, "action": "dry_run"},
            coverage={
                "selected_meetings": len(result["selected_meetings"]),
                "candidates": len(result["candidates"]),
            },
        )
    else:
        inputs = (
            [input_file_record(args.config, kind="minutes_adapter_config")]
            if args.config
            else []
        )
        finish_database_run(
            manifest_path,
            manifest,
            database=result["database"],
            artifact_kind="minutes_database",
            scope={"adapter": args.adapter, "action": "ingest"},
            coverage={
                "meetings": result["meetings"],
                "speeches": result["speeches"],
                "statuses": result["statuses"],
                "fts_tokenizer": result["fts_tokenizer"],
                "fts_schema": result["fts_schema"],
                "diagnostics": result["coverage_diagnostics"],
            },
            inputs=inputs,
            checks=[
                {
                    "name": "meeting_rows",
                    "status": "passed" if result["meetings"] > 0 else "failed",
                    "detail": result["meetings"],
                }
            ],
        )
    if manifest_path is not None:
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
