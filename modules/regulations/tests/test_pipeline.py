"""Synthetic tests for regulations ingestion, search, and context packs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from modules.minutes_db.adapters.base import FetchResult
from modules.regulations import context_pack, ingest, search

INDEX_URL = "https://example.invalid/reiki/index.html"
DOC_URL = "https://example.invalid/reiki/privacy.html"


def result(url: str, body: str, path: Path) -> FetchResult:
    raw = body.encode("utf-8")
    path.write_bytes(raw)
    return FetchResult(
        url=url,
        final_url=url,
        body=raw,
        fetched_at="2026-07-23T00:00:00Z",
        content_type="text/html",
        encoding="utf-8",
        cache_path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        from_cache=False,
    )


class RegulationsPipelineTests(unittest.TestCase):
    def test_discovery_confines_hosts_and_reports_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed_url = "https://files.example.invalid/shared/rule.html"
            skipped_url = "https://outside.invalid/foreign-regulation.html"
            index_html = (
                '<a href="privacy.html">架空町個人情報保護条例</a>'
                f'<a href="{allowed_url}">架空町共同規則</a>'
                f'<a href="{skipped_url}">別の架空町条例</a>'
            )
            fetched = result(INDEX_URL, index_html, root / "index.cache")
            config = {
                "index_url": [INDEX_URL],
                "allow_hosts": ["FILES.EXAMPLE.INVALID"],
            }

            with patch(
                "modules.regulations.ingest.polite_fetch",
                return_value=fetched,
            ):
                refs = ingest.discover_documents(config)

        self.assertIsInstance(refs, list)
        self.assertEqual([DOC_URL, allowed_url], [ref["source_url"] for ref in refs])
        self.assertEqual([skipped_url], refs.skipped_urls)

    def test_fetch_document_uses_redirected_host_as_source_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            redirected_url = "https://records.example.invalid/rule.html"
            fetched = result(
                redirected_url,
                "<p>第1条 架空の規則を定める。</p>",
                root / "document.cache",
            )
            ref = {
                "source_url": DOC_URL,
                "discovered_from": INDEX_URL,
                "title": "架空町規則",
            }

            with patch(
                "modules.regulations.ingest.polite_fetch",
                return_value=fetched,
            ):
                payload = ingest.fetch_document(
                    ref,
                    {"municipality": "架空町"},
                )

        self.assertEqual(
            "records.example.invalid",
            payload["document"]["source_name"],
        )

    def test_ingest_search_and_context_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({
                "index_url": INDEX_URL,
                "link_include_regex": "privacy",
                "source_name": "架空町例規集",
            }, ensure_ascii=False), encoding="utf-8")
            index_html = '<a href="privacy.html">架空町個人情報保護条例</a>'
            document_html = """
            <html><head><title>架空町個人情報保護条例</title></head>
            <body>
            <p>令和5年4月1日</p>
            <p>第1条 この条例は個人情報の適正な取扱いを定める。</p>
            <p>第2条 実施機関は必要な措置を講じる。</p>
            </body></html>
            """
            responses = {
                INDEX_URL: result(INDEX_URL, index_html, root / "index.cache"),
                DOC_URL: result(DOC_URL, document_html, root / "doc.cache"),
            }
            db = root / "regulations.db"
            with patch("modules.regulations.ingest.polite_fetch", side_effect=lambda url, **_: responses[url]):
                report = ingest.ingest(config, db, limit=1)
            self.assertEqual(1, report["documents"])
            self.assertEqual(3, report["articles"])
            with closing(sqlite3.connect(db)) as connection, connection:
                hits = search.search_database(connection, "個人情報", 5)
                pack = context_pack.build_context_pack(
                    connection,
                    "個人情報",
                    5,
                    20,
                    question="個人情報の取扱いは何条にあるか",
                )
            self.assertGreaterEqual(len(hits), 1)
            self.assertEqual("架空町個人情報保護条例", hits[0]["title"])
            self.assertEqual(1, len(pack["items"]))
            self.assertTrue(pack["items"][0]["quote_is_verbatim"])
            self.assertLessEqual(pack["limits"]["quote_characters_used"], 20)
            self.assertEqual("個人情報", pack["search"]["query"])
            self.assertEqual(
                "個人情報の取扱いは何条にあるか",
                pack["question"],
            )


if __name__ == "__main__":
    unittest.main()
