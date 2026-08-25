"""Synthetic integration tests for SQLite ingestion, search, and context packs."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lcaios.http import FetchError, HttpClient, _RawResponse
from modules.minutes_db import ingest
from modules.minutes_db import search as minutes_search
from modules.minutes_db.context_pack import build_context_pack
from modules.minutes_db.ingest import ensure_schema, store_meeting
from modules.minutes_db.search import search_database, search_with_report


def synthetic_document(text: str) -> dict[str, object]:
    return {
        "meeting": {
            "council_name": "架空町議会",
            "meeting_name": "令和8年第1回定例会",
            "session": "第1日",
            "date": "2026-06-03",
            "source_url": "https://example.invalid/minutes/meeting-1",
            "adapter": "static_html",
            "fetched_at": "2026-07-23T00:00:00Z",
        },
        "speeches": [
            {
                "seq": 1,
                "speaker": "架空花子",
                "speaker_role": "議員",
                "text": text,
                "locator": "paragraph:1",
            }
        ],
        "provenance": {
            "discovered_from": "https://example.invalid/minutes/",
            "resolved_url": "https://example.invalid/minutes/meeting-1",
            "fetched_at": "2026-07-23T00:00:00Z",
            "media_type": "text/html",
            "content_sha256": "0" * 64,
            "transform": {"extractor": "synthetic"},
            "status": "verified",
            "cache_path": "/tmp/synthetic-cache",
            "issues": [],
        },
    }


def synthetic_documents(texts: list[str]) -> dict[str, object]:
    document = synthetic_document(texts[0])
    document["speeches"] = [
        {
            "seq": index,
            "speaker": f"架空議員{index}",
            "speaker_role": "議員",
            "text": text,
            "locator": f"paragraph:{index}",
        }
        for index, text in enumerate(texts, start=1)
    ]
    return document


def fetch_failed_document(source_url: str) -> dict[str, object]:
    return {
        "meeting": {
            "council_name": "架空町議会",
            "meeting_name": "令和8年第1回定例会",
            "date": None,
            "source_url": source_url,
            "adapter": "static_html",
            "fetched_at": "2026-07-23T00:00:00Z",
        },
        "speeches": [],
        "provenance": {
            "resolved_url": source_url,
            "status": "fetch_failed",
        },
    }


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "minutes.db"
        self.connection = sqlite3.connect(self.database)
        ensure_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def _rebuild_fts(self) -> None:
        self.connection.execute(
            "INSERT INTO speeches_fts(speeches_fts) VALUES ('rebuild')"
        )
        self.connection.commit()

    def test_upsert_is_idempotent_by_source_and_sequence(self) -> None:
        store_meeting(self.connection, synthetic_document("防災について質問します。"))
        store_meeting(self.connection, synthetic_document("防災計画を質問します。"))
        self.connection.commit()

        meeting_count = self.connection.execute(
            "SELECT count(*) FROM meetings"
        ).fetchone()[0]
        speech_count = self.connection.execute(
            "SELECT count(*) FROM speeches"
        ).fetchone()[0]
        provenance_count = self.connection.execute(
            "SELECT count(*) FROM provenance"
        ).fetchone()[0]
        text = self.connection.execute("SELECT text FROM speeches").fetchone()[0]

        self.assertEqual(1, meeting_count)
        self.assertEqual(1, speech_count)
        self.assertEqual(1, provenance_count)
        self.assertEqual("防災計画を質問します。", text)

    def test_reingest_with_fewer_speeches_removes_stale_rows_and_fts(self) -> None:
        document = synthetic_documents(
            [f"発言{n}の防災について述べます。" for n in range(1, 6)]
        )
        store_meeting(self.connection, document)
        self._rebuild_fts()
        revised = synthetic_documents(
            [f"発言{n}の防災について述べます。" for n in range(1, 4)]
        )
        other = synthetic_documents(["別会議の発言です。"])
        assert isinstance(other, dict) and isinstance(other["meeting"], dict)
        other["meeting"]["source_url"] = "https://example.invalid/minutes/meeting-2"
        store_meeting(self.connection, other)
        self._rebuild_fts()

        stored = store_meeting(self.connection, revised)
        self.connection.commit()

        self.assertEqual(3, stored)
        rows = self.connection.execute(
            "SELECT seq, text FROM speeches WHERE meeting_id = ("
            "SELECT meeting_id FROM meetings WHERE source_url ="
            " 'https://example.invalid/minutes/meeting-1') ORDER BY seq"
        ).fetchall()
        self.assertEqual([1, 2, 3], [row[0] for row in rows])
        stale_fts = self.connection.execute(
            "SELECT count(*) FROM speeches_fts WHERE speeches_fts MATCH '発言5'"
        ).fetchone()[0]
        self.assertEqual(0, stale_fts)
        kept_fts = self.connection.execute(
            "SELECT count(*) FROM speeches_fts WHERE speeches_fts MATCH '発言3'"
        ).fetchone()[0]
        self.assertEqual(1, kept_fts)
        other_count = self.connection.execute(
            "SELECT count(*) FROM speeches WHERE meeting_id = ("
            "SELECT meeting_id FROM meetings WHERE source_url ="
            " 'https://example.invalid/minutes/meeting-2')"
        ).fetchone()[0]
        self.assertEqual(1, other_count)

    def test_fetch_failed_placeholder_preserves_existing_speeches(self) -> None:
        document = synthetic_documents(
            [f"発言{n}の予算について質問します。" for n in range(1, 4)]
        )
        store_meeting(self.connection, document)
        self._rebuild_fts()
        other = synthetic_documents(["別会議の発言です。"])
        assert isinstance(other, dict) and isinstance(other["meeting"], dict)
        other["meeting"]["source_url"] = "https://example.invalid/minutes/meeting-2"
        store_meeting(self.connection, other)

        placeholder = fetch_failed_document(
            "https://example.invalid/minutes/meeting-1"
        )
        store_meeting(self.connection, placeholder)
        self.connection.commit()

        rows = self.connection.execute(
            "SELECT seq, text FROM speeches WHERE meeting_id = ("
            "SELECT meeting_id FROM meetings WHERE source_url ="
            " 'https://example.invalid/minutes/meeting-1') ORDER BY seq"
        ).fetchall()
        self.assertEqual(3, len(rows))
        fts_hits = self.connection.execute(
            "SELECT count(*) FROM speeches_fts WHERE speeches_fts MATCH '質問します'"
        ).fetchone()[0]
        self.assertEqual(3, fts_hits)
        other_count = self.connection.execute(
            "SELECT count(*) FROM speeches WHERE meeting_id = ("
            "SELECT meeting_id FROM meetings WHERE source_url ="
            " 'https://example.invalid/minutes/meeting-2')"
        ).fetchone()[0]
        self.assertEqual(1, other_count)

    def test_search_and_context_pack_include_provenance(self) -> None:
        source_text = "地域防災計画の見直しについて質問します。"
        store_meeting(self.connection, synthetic_document(source_text))
        self._rebuild_fts()

        hits = search_database(self.connection, "防災", k=3)
        self.assertEqual(1, len(hits))
        self.assertEqual("架空花子", hits[0]["speaker"])
        self.assertEqual("paragraph:1", hits[0]["locator"])

        pack = build_context_pack(
            self.connection,
            "防災",
            k=3,
            char_budget=10,
            question="地域防災計画はいつ見直されたか",
        )
        self.assertEqual(1, len(pack["evidence"]))
        evidence = pack["evidence"][0]
        self.assertIn(evidence["quote"], source_text)
        self.assertTrue(evidence["quote_is_verbatim"])
        self.assertEqual("2026-07-23T00:00:00Z", evidence["fetched_at"])
        self.assertLessEqual(pack["limits"]["quote_characters_used"], 10)
        self.assertEqual("防災", pack["search"]["query"])
        self.assertEqual(
            "地域防災計画はいつ見直されたか",
            pack["question"],
        )

    def test_short_query_uses_literal_fallback(self) -> None:
        store_meeting(self.connection, synthetic_document("町の水対策です。"))
        self._rebuild_fts()

        hits = search_database(self.connection, "水", k=3)
        self.assertEqual(1, len(hits))
        self.assertIn("水", hits[0]["text"])

    def test_trigram_finds_substring_in_long_cjk_run(self) -> None:
        status = ensure_schema(self.connection)
        if status["tokenizer"] != "trigram":
            self.skipTest("SQLite trigram tokenizer is unavailable")
        store_meeting(
            self.connection,
            synthetic_document(
                "架空町介護保険事業の運営について質問します。"
            ),
        )

        rows, report = search_with_report(
            self.connection,
            "介護保険",
            k=10,
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("fts", rows[0]["search_path"])
        self.assertEqual("fts", report["search_path"])
        self.assertEqual(1, report["total_matches"])

    def test_successful_fts_result_is_not_padded_with_like_rows(self) -> None:
        status = ensure_schema(self.connection)
        if status["tokenizer"] != "trigram":
            self.skipTest("SQLite trigram tokenizer is unavailable")
        store_meeting(
            self.connection,
            synthetic_documents(
                [
                    "介護保険の対象です。",
                    "介護保険の手続です。",
                    "介護保険の給付です。",
                ]
            ),
        )
        keep_rowid = self.connection.execute(
            "SELECT rowid FROM speeches ORDER BY seq LIMIT 1"
        ).fetchone()[0]
        self.connection.execute(
            "DELETE FROM speeches_fts WHERE rowid != ?",
            (keep_rowid,),
        )

        rows, report = search_with_report(
            self.connection,
            "介護保険",
            k=10,
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("fts", report["search_path"])
        self.assertTrue(all(row["search_path"] == "fts" for row in rows))

    def test_fts_error_is_visible_in_fallback_report(self) -> None:
        store_meeting(
            self.connection,
            synthetic_document("介護保険の対象です。"),
        )
        self.connection.execute("DROP TABLE speeches_fts")

        rows, report = search_with_report(
            self.connection,
            "介護保険",
            k=10,
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("like", rows[0]["search_path"])
        self.assertEqual("like", report["search_path"])
        self.assertEqual("fts_error", report["fallback_reason"])
        self.assertIn("speeches_fts", report["fts_error"] or "")

    def test_like_report_counts_matches_beyond_limit(self) -> None:
        store_meeting(
            self.connection,
            synthetic_documents(
                ["予算を審議します。", "予算を補正します。", "予算を公表します。"]
            ),
        )

        rows, report = search_with_report(self.connection, "予算", k=1)

        self.assertEqual(1, len(rows))
        self.assertEqual("like", report["search_path"])
        self.assertEqual(3, report["total_matches"])
        self.assertTrue(report["truncated"])
        self.assertEqual(
            "query_too_short_for_trigram",
            report["fallback_reason"],
        )
        self.assertIsNone(report["fts_error"])

    def test_search_main_prints_report_and_named_results(self) -> None:
        store_meeting(
            self.connection,
            synthetic_document("予算を審議します。"),
        )
        self.connection.commit()
        stdout = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "search.py",
                    "予算",
                    "--db",
                    str(self.database),
                    "--k",
                    "1",
                ],
            ),
            redirect_stdout(stdout),
        ):
            self.assertEqual(0, minutes_search.main())

        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            {
                "query",
                "total_matches",
                "truncated",
                "search_path",
                "fts_error",
                "fallback_reason",
            },
            set(payload["report"]),
        )
        self.assertIsInstance(payload["results"], list)

    def test_ensure_schema_rebuilds_old_unicode61_database(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(
                ingest.SCHEMA_PATH.read_text(encoding="utf-8")
            )
            store_meeting(
                connection,
                synthetic_document(
                    "架空町介護保険事業の運営について質問します。"
                ),
            )
            old_matches = connection.execute(
                """
                SELECT COUNT(*)
                FROM speeches_fts
                WHERE speeches_fts MATCH ?
                """,
                ("介護保険",),
            ).fetchone()[0]
            relational_count = connection.execute(
                "SELECT COUNT(*) FROM speeches"
            ).fetchone()[0]
            if not ingest.supports_fts5_trigram(connection):
                self.skipTest("SQLite trigram tokenizer is unavailable")

            status = ensure_schema(connection)
            rows, report = search_with_report(
                connection,
                "介護保険",
                k=10,
            )

            self.assertEqual(0, old_matches)
            self.assertEqual("trigram", status["tokenizer"])
            self.assertTrue(status["rebuilt"])
            self.assertEqual(
                "unicode61",
                status["previous_tokenizer"],
            )
            self.assertEqual(
                relational_count,
                connection.execute(
                    "SELECT COUNT(*) FROM speeches"
                ).fetchone()[0],
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("fts", report["search_path"])
        finally:
            connection.close()

    def test_manifest_distinguishes_cached_and_refreshed_retrieval(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        meeting_url = "https://example.invalid/council/meeting.html"
        responses = {
            index_url: (
                '<a href="meeting.html">'
                "令和8年第1回定例会 2026年7月1日"
                "</a>"
            ).encode(),
            meeting_url: (
                "<html><title>令和8年第1回定例会</title>"
                "<p>2026年7月1日</p><p>○議長　開会します。</p></html>"
            ).encode(),
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "index_url": index_url,
                        "link_include_regex": "meeting\\.html$",
                        "pdf": False,
                        "council_name": "架空町議会",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            cache = root / "cache"
            manifests = root / "manifests"

            def request_once(client: HttpClient, url: str) -> _RawResponse:
                client.request_count += 1
                return _RawResponse(
                    url=url,
                    status=200,
                    body=responses[url],
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    fetched_at=datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                )

            def run(run_id: str, *, refresh: bool = False) -> dict[str, object]:
                argv = [
                    "--adapter",
                    "static",
                    "--config",
                    str(config),
                    "--db",
                    str(root / f"{run_id}.db"),
                    "--cache-dir",
                    str(cache),
                    "--manifest-dir",
                    str(manifests),
                    "--run-id",
                    run_id,
                    "--limit",
                    "1",
                ]
                if refresh:
                    argv.append("--refresh")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, ingest.main(argv))
                return json.loads(
                    (manifests / f"{run_id}.json").read_text(encoding="utf-8")
                )

            with (
                patch.object(HttpClient, "_assert_robots_allowed"),
                patch.object(
                    HttpClient,
                    "_request_once",
                    autospec=True,
                    side_effect=request_once,
                ),
            ):
                run("network")
                cached = run("cached")
                refreshed = run("refreshed", refresh=True)

        cached_retrieval = cached["retrieval"]
        refreshed_retrieval = refreshed["retrieval"]
        self.assertEqual(2, cached_retrieval["cache_hit_count"])
        self.assertEqual(0, cached_retrieval["live_request_count"])
        self.assertFalse(cached_retrieval["latestness_rechecked_this_run"])
        self.assertEqual(
            "cache_hit",
            next(
                item["status"]
                for item in cached_retrieval["accesses"]
                if item["url"] == index_url
            ),
        )
        self.assertEqual(
            {
                "cache_hits": 2,
                "network_fetches": 0,
                "refreshes": 0,
                "cache_misses": 0,
                },
            {
                key: cached_retrieval["sources"][0][key]
                for key in (
                    "cache_hits",
                    "network_fetches",
                    "refreshes",
                    "cache_misses",
                )
            },
        )
        self.assertEqual(2, refreshed_retrieval["refresh_count"])
        self.assertEqual(2, refreshed_retrieval["live_request_count"])
        self.assertTrue(refreshed_retrieval["latestness_rechecked_this_run"])
        self.assertEqual(
            "refreshed",
            next(
                item["status"]
                for item in refreshed_retrieval["accesses"]
                if item["url"] == index_url
            ),
        )
        self.assertEqual(
            {
                "cache_hits": 0,
                "network_fetches": 2,
                "refreshes": 2,
                "cache_misses": 0,
            },
            {
                key: refreshed_retrieval["sources"][0][key]
                for key in (
                    "cache_hits",
                    "network_fetches",
                    "refreshes",
                    "cache_misses",
                )
            },
        )


class SkipBrokenDocumentsTests(unittest.TestCase):
    """Synthetic fixture tests for --skip-broken-documents."""

    def _make_refs(self) -> list[dict[str, object]]:
        return [
            {
                "meeting_id": "meeting_1",
                "source_url": "https://example.invalid/meeting-1.html",
                "meeting_name": "会議1",
                "discovered_from": "https://example.invalid/index.html",
                "is_pdf": False,
            },
            {
                "meeting_id": "meeting_2",
                "source_url": "https://example.invalid/meeting-2.html",
                "meeting_name": "会議2",
                "discovered_from": "https://example.invalid/index.html",
                "is_pdf": False,
            },
            {
                "meeting_id": "meeting_3",
                "source_url": "https://example.invalid/meeting-3.html",
                "meeting_name": "会議3",
                "discovered_from": "https://example.invalid/index.html",
                "is_pdf": False,
            },
        ]

    def _good_document(self, ref: dict[str, object], text: str) -> dict[str, object]:
        doc = synthetic_document(text)
        meeting = dict(doc["meeting"])  # type: ignore[arg-type]
        meeting["source_url"] = str(ref["source_url"])
        meeting["meeting_id"] = str(ref["meeting_id"])
        meeting["meeting_name"] = str(ref["meeting_name"])
        doc["meeting"] = meeting
        provenance = dict(doc["provenance"])  # type: ignore[arg-type]
        provenance["resolved_url"] = str(ref["source_url"])
        provenance["discovered_from"] = str(ref["discovered_from"])
        doc["provenance"] = provenance
        return doc  # type: ignore[return-value]

    def test_with_flag_404_is_recorded_and_remaining_ingested(self) -> None:
        refs = self._make_refs()
        good_1 = self._good_document(refs[0], "防災について質問します。")
        good_3 = self._good_document(refs[2], "予算について質問します。")
        error = FetchError("取得に失敗しました: HTTP 404: https://example.invalid/meeting-2.html")

        def fetch_meeting(self: object, meeting_id: object) -> dict[str, object]:
            mid = str(meeting_id)
            if mid == "meeting_2":
                raise error
            if mid == "meeting_1":
                return good_1
            if mid == "meeting_3":
                return good_3
            raise AssertionError(f"unexpected meeting_id {mid!r}")

        adapter = type(
            "StubAdapter",
            (),
            {
                "config": {"council_name": "架空町議会", "coverage": {}},
                "coverage_candidate_sessions": None,
                "discovery_candidates": [],
                "list_meetings": lambda self, limit=None: refs[:limit] if limit is not None else refs,  # type: ignore[no-untyped-def]
                "fetch_meeting": fetch_meeting,  # type: ignore[arg-type]
                "adapter_name": "static_html",
            },
        )()

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minutes.db"
            args = type(
                "Args",
                (),
                {
                    "adapter": "static",
                    "config": str(Path(tmp) / "dummy.json"),
                    "url": None,
                    "db": str(db),
                    "limit": None,
                    "cache_dir": tmp,
                    "offline": False,
                    "refresh": False,
                    "timeout": 90,
                    "dry_run": False,
                    "skip_broken_documents": True,
                },
            )()
            with patch("modules.minutes_db.ingest._make_adapter", return_value=adapter):
                result = ingest.ingest(args)  # type: ignore[arg-type]
            self.assertEqual(3, result["meetings"])
            self.assertEqual(1, result["statuses"].get("fetch_failed", 0))
            self.assertEqual(2, result["speeches"])
            conn = sqlite3.connect(db)
            try:
                statuses = [row[0] for row in conn.execute("SELECT status FROM provenance").fetchall()]
                self.assertIn("fetch_failed", statuses)
                row = conn.execute(
                    "SELECT issues_json FROM provenance WHERE status = 'fetch_failed'"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertIn("404", row[0])
                speech_count = conn.execute("SELECT count(*) FROM speeches").fetchone()[0]
                self.assertEqual(2, speech_count)
                meeting_count = conn.execute("SELECT count(*) FROM meetings").fetchone()[0]
                self.assertEqual(3, meeting_count)
            finally:
                conn.close()

    def test_without_flag_still_fails_fast(self) -> None:
        refs = self._make_refs()[:2]
        good_1 = self._good_document(refs[0], "防災について質問します。")
        error = FetchError("取得に失敗しました: HTTP 404: https://example.invalid/meeting-2.html")

        def fetch_meeting(self: object, meeting_id: object) -> dict[str, object]:
            mid = str(meeting_id)
            if mid == "meeting_1":
                return good_1
            raise error

        adapter = type(
            "StubAdapter",
            (),
            {
                "config": {"council_name": "架空町議会", "coverage": {}},
                "coverage_candidate_sessions": None,
                "discovery_candidates": [],
                "list_meetings": lambda self, limit=None: refs[:limit] if limit is not None else refs,  # type: ignore[no-untyped-def]
                "fetch_meeting": fetch_meeting,  # type: ignore[arg-type]
                "adapter_name": "static_html",
            },
        )()

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minutes.db"
            args = type(
                "Args",
                (),
                {
                    "adapter": "static",
                    "config": str(Path(tmp) / "dummy.json"),
                    "url": None,
                    "db": str(db),
                    "limit": None,
                    "cache_dir": tmp,
                    "offline": False,
                    "refresh": False,
                    "timeout": 90,
                    "dry_run": False,
                    "skip_broken_documents": False,
                },
            )()
            with patch("modules.minutes_db.ingest._make_adapter", return_value=adapter):
                with self.assertRaises(FetchError):
                    ingest.ingest(args)  # type: ignore[arg-type]
            # default flag is false
            parser = ingest.build_parser()
            parsed = parser.parse_args(["--adapter", "static", "--config", "x", "--db", "y"])
            self.assertFalse(parsed.skip_broken_documents)

    def test_index_failure_still_aborts_even_with_flag(self) -> None:
        def failing_list(self: object, limit: object = None) -> list[dict[str, object]]:
            raise FetchError("取得に失敗しました: HTTP 404: https://example.invalid/index.html")

        def _unexpected_fetch(self: object, meeting_id: object) -> dict[str, object]:
            raise AssertionError("should not be called")

        adapter = type(
            "StubAdapter",
            (),
            {
                "config": {},
                "coverage_candidate_sessions": None,
                "discovery_candidates": [],
                "list_meetings": failing_list,  # type: ignore[arg-type]
                "fetch_meeting": _unexpected_fetch,  # type: ignore[arg-type]
                "adapter_name": "static_html",
            },
        )()

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minutes.db"
            args = type(
                "Args",
                (),
                {
                    "adapter": "static",
                    "config": str(Path(tmp) / "dummy.json"),
                    "url": None,
                    "db": str(db),
                    "limit": None,
                    "cache_dir": tmp,
                    "offline": False,
                    "refresh": False,
                    "timeout": 90,
                    "dry_run": False,
                    "skip_broken_documents": True,
                },
            )()
            with patch("modules.minutes_db.ingest._make_adapter", return_value=adapter):
                with self.assertRaises(FetchError):
                    ingest.ingest(args)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
