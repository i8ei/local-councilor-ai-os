"""Synthetic integration tests for SQLite ingestion, search, and context packs."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from context_pack import build_context_pack
from ingest import ensure_schema, store_meeting
from search import search_database


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


if __name__ == "__main__":
    unittest.main()
