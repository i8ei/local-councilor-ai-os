"""Synthetic tests for regulations ingestion, search, and context packs."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lcaios.http import (
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchResult,
    HttpClient,
    _RawResponse,
)
from lcaios.tests.http_fakes import FakeHttpClient
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


def synthetic_regulation(texts: list[str]) -> dict[str, object]:
    return {
        "document": {
            "document_id": "regdoc_synthetic",
            "title": "架空町介護保険条例",
            "category": "福祉",
            "source_url": "https://example.invalid/reiki/care.html",
            "source_name": "架空町例規集",
            "promulgated_on": "2026-04-01",
            "enforced_on": "2026-04-01",
            "fetched_at": "2026-07-23T00:00:00Z",
            "adapter": "synthetic",
            "verification_state": "verified",
        },
        "articles": [
            {
                "seq": index,
                "article_no": f"第{index}条",
                "heading": None,
                "text": text,
                "locator": f"article:{index}",
            }
            for index, text in enumerate(texts, start=1)
        ],
        "provenance": {
            "resolved_url": "https://example.invalid/reiki/care.html",
            "fetched_at": "2026-07-23T00:00:00Z",
            "status": "verified",
        },
    }


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

            client = FakeHttpClient({INDEX_URL: fetched})
            refs = ingest.discover_documents(config, client=client)

        self.assertIsInstance(refs, list)
        self.assertEqual([DOC_URL, allowed_url], [ref["source_url"] for ref in refs])
        self.assertEqual([skipped_url], refs.skipped_urls)
        self.assertEqual([(INDEX_URL, CacheTier.INDEX)], client.calls)

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

            client = FakeHttpClient({DOC_URL: fetched})
            payload = ingest.fetch_document(
                ref,
                {"municipality": "架空町"},
                client=client,
            )

        self.assertEqual(
            "records.example.invalid",
            payload["document"]["source_name"],
        )
        self.assertEqual([(DOC_URL, CacheTier.DOCUMENT)], client.calls)

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
            client = FakeHttpClient(responses)
            with patch.object(
                ingest,
                "HttpClient",
                return_value=client,
            ) as http_client:
                report = ingest.ingest(config, db, limit=1)
            http_client.assert_called_once_with(
                ingest.REGULATIONS_STATIC_DEFAULT_CACHE_DIR,
                user_agent=REGULATIONS_USER_AGENT,
                offline=False,
                refresh=False,
                timeout=90,
            )
            self.assertEqual(1, report["documents"])
            self.assertEqual(3, report["articles"])
            self.assertEqual(
                [
                    (INDEX_URL, CacheTier.INDEX),
                    (DOC_URL, CacheTier.DOCUMENT),
                ],
                client.calls,
            )
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

    def test_trigram_finds_substring_in_long_cjk_run(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            status = ingest.ensure_schema(connection)
            if status["tokenizer"] != "trigram":
                self.skipTest("SQLite trigram tokenizer is unavailable")
            ingest.store_document(
                connection,
                synthetic_regulation(
                    ["架空町介護保険条例の一部を改正する条例"]
                ),
            )

            rows, report = search.search_with_report(
                connection,
                "介護保険",
                10,
            )

            self.assertEqual(1, len(rows))
            self.assertEqual("fts", rows[0]["search_path"])
            self.assertEqual("fts", report["search_path"])
            self.assertEqual(1, report["total_matches"])
        finally:
            connection.close()

    def test_successful_fts_result_is_not_padded_with_like_rows(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            status = ingest.ensure_schema(connection)
            if status["tokenizer"] != "trigram":
                self.skipTest("SQLite trigram tokenizer is unavailable")
            ingest.store_document(
                connection,
                synthetic_regulation(
                    [
                        "介護保険の対象です。",
                        "介護保険の手続です。",
                        "介護保険の給付です。",
                    ]
                ),
            )
            keep_id = connection.execute(
                "SELECT article_id FROM regulation_articles ORDER BY seq LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM regulation_articles_fts WHERE article_id != ?",
                (keep_id,),
            )

            rows, report = search.search_with_report(
                connection,
                "介護保険",
                10,
            )

            self.assertEqual(1, len(rows))
            self.assertEqual("fts", report["search_path"])
            self.assertTrue(
                all(row["search_path"] == "fts" for row in rows)
            )
        finally:
            connection.close()

    def test_fts_error_is_visible_in_fallback_report(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            ingest.ensure_schema(connection)
            ingest.store_document(
                connection,
                synthetic_regulation(["介護保険の対象です。"]),
            )
            connection.execute("DROP TABLE regulation_articles_fts")

            rows, report = search.search_with_report(
                connection,
                "介護保険",
                10,
            )

            self.assertEqual(1, len(rows))
            self.assertEqual("like", rows[0]["search_path"])
            self.assertEqual("like", report["search_path"])
            self.assertEqual("fts_error", report["fallback_reason"])
            self.assertIn("regulation_articles_fts", report["fts_error"] or "")
        finally:
            connection.close()

    def test_like_report_counts_matches_beyond_limit(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            ingest.ensure_schema(connection)
            ingest.store_document(
                connection,
                synthetic_regulation(
                    ["予算を定める。", "予算を補正する。", "予算を公表する。"]
                ),
            )

            rows, report = search.search_with_report(connection, "予算", 1)

            self.assertEqual(1, len(rows))
            self.assertEqual("like", report["search_path"])
            self.assertEqual(3, report["total_matches"])
            self.assertTrue(report["truncated"])
            self.assertEqual(
                "query_too_short_for_trigram",
                report["fallback_reason"],
            )
            self.assertIsNone(report["fts_error"])
        finally:
            connection.close()

    def test_search_main_prints_report_and_named_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "regulations.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                ingest.ensure_schema(connection)
                ingest.store_document(
                    connection,
                    synthetic_regulation(["予算を定める。"]),
                )
            stdout = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "search.py",
                        "予算",
                        "--db",
                        str(database),
                        "--k",
                        "1",
                    ],
                ),
                redirect_stdout(stdout),
            ):
                self.assertEqual(0, search.main())

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
            ingest.store_document(
                connection,
                synthetic_regulation(
                    ["架空町介護保険条例の一部を改正する条例"]
                ),
            )
            old_matches = connection.execute(
                """
                SELECT COUNT(*)
                FROM regulation_articles_fts
                WHERE regulation_articles_fts MATCH ?
                """,
                ("介護保険",),
            ).fetchone()[0]
            relational_count = connection.execute(
                "SELECT COUNT(*) FROM regulation_articles"
            ).fetchone()[0]
            if not ingest.supports_fts5_trigram(connection):
                self.skipTest("SQLite trigram tokenizer is unavailable")

            status = ingest.ensure_schema(connection)
            rows, report = search.search_with_report(
                connection,
                "介護保険",
                10,
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
                    "SELECT COUNT(*) FROM regulation_articles"
                ).fetchone()[0],
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("fts", report["search_path"])
        finally:
            connection.close()

    def test_manifest_distinguishes_cached_and_refreshed_retrieval(self) -> None:
        index_html = '<a href="privacy.html">架空町個人情報保護条例</a>'
        document_html = (
            "<html><title>架空町個人情報保護条例</title><body>"
            "<p>令和5年4月1日</p>"
            "<p>第1条 この条例は個人情報の取扱いを定める。</p>"
            "</body></html>"
        )
        responses = {
            INDEX_URL: index_html.encode(),
            DOC_URL: document_html.encode(),
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "index_url": INDEX_URL,
                        "link_include_regex": "privacy",
                        "source_name": "架空町例規集",
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
                if item["url"] == INDEX_URL
            ),
        )
        self.assertEqual(2, cached_retrieval["sources"][0]["cache_hits"])
        self.assertEqual(0, cached_retrieval["sources"][0]["network_fetches"])
        self.assertEqual(0, cached_retrieval["sources"][0]["refreshes"])
        self.assertEqual(0, cached_retrieval["sources"][0]["cache_misses"])
        self.assertEqual(2, refreshed_retrieval["refresh_count"])
        self.assertEqual(2, refreshed_retrieval["live_request_count"])
        self.assertTrue(refreshed_retrieval["latestness_rechecked_this_run"])
        self.assertEqual(
            "refreshed",
            next(
                item["status"]
                for item in refreshed_retrieval["accesses"]
                if item["url"] == INDEX_URL
            ),
        )
        self.assertEqual(0, refreshed_retrieval["sources"][0]["cache_hits"])
        self.assertEqual(2, refreshed_retrieval["sources"][0]["network_fetches"])
        self.assertEqual(2, refreshed_retrieval["sources"][0]["refreshes"])
        self.assertEqual(0, refreshed_retrieval["sources"][0]["cache_misses"])


if __name__ == "__main__":
    unittest.main()
