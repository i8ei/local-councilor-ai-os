"""Synthetic tests for the joureikun regulations adapter."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lcaios.http import (
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchResult,
    HttpClient,
    RobotsDeniedError,
    _RawResponse,
)
from lcaios.tests.http_fakes import FakeHttpClient
from modules.regulations import context_pack, search, vendor_joureikun

INDEX_URL = "https://example.invalid/joureikun/aggregate/catalog/index.html"
ACT_1_URL = "https://example.invalid/joureikun/act/1.html"
ACT_2_URL = "https://example.invalid/joureikun/act/2.html"
ACT_3_URL = "https://example.invalid/joureikun/act/3_20200101.html"

CATALOG_HTML = """\
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>Catalog</title></head>
<body>
<h1>Omachi Regulations</h1>
<ul>
  <li><a href="../../act/1.html">Omachi Sample Ordinance</a></li>
  <li><a href="../../act/2.html">Omachi Second Rule</a></li>
  <li><a href="../../act/3_20200101.html">Omachi Dated Act</a></li>
  <li><a href="https://outside.invalid/joureikun/act/999.html">Outside</a></li>
</ul>
<script>const guessed = "/joureikun/act/99999.html";</script>
</body>
</html>
"""

ACT_HTML = """\
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>Omachi Sample Ordinance</title></head>
<body>
<div class="content">
<p>令和5年4月1日条例第1号</p>
<p>第1条　この条例は、サンプルについて定める。</p>
<p>第2条　町は、必要な措置を講ずる。</p>
<p>附則</p>
</div>
</body>
</html>
"""

ACT_NO_HEADING_HTML = """\
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>Simple Notice</title></head>
<body>
<p>これは条見出しのない文書です。前文のみ。</p>
</body>
</html>
"""


def result(
    url: str,
    body: str | bytes,
    cache_path: Path,
    *,
    encoding: str = "utf-8",
    content_type: str = "text/html",
) -> FetchResult:
    raw = body if isinstance(body, bytes) else body.encode(encoding)
    cache_path.write_bytes(raw)
    return FetchResult(
        url=url,
        final_url=url,
        body=raw,
        fetched_at="2026-08-21T00:00:00Z",
        content_type=content_type,
        encoding=encoding,
        cache_path=cache_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        from_cache=False,
    )


class JoureikunAdapterTests(unittest.TestCase):
    def test_ingested_database_works_with_existing_search_and_context_pack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: result(INDEX_URL, CATALOG_HTML, root / "catalog.cache"),
                ACT_1_URL: result(ACT_1_URL, ACT_HTML, root / "act1.cache"),
            }
            client = FakeHttpClient(responses)
            database = root / "regulations.db"
            with patch.object(
                vendor_joureikun,
                "HttpClient",
                return_value=client,
            ):
                report = vendor_joureikun.ingest_joureikun(
                    INDEX_URL,
                    database,
                    source_name="Omachi Joureikun",
                    limit=1,
                )
            with closing(sqlite3.connect(database)) as connection, connection:
                hits = search.search_database(connection, "サンプル", 5)
                pack = context_pack.build_context_pack(connection, "サンプル", 5, 1000)

        self.assertEqual(1, report["documents"])
        self.assertGreaterEqual(report["articles"], 2)
        self.assertEqual(
            report["fts_tokenizer"],
            report["fts_schema"]["tokenizer"],
        )
        self.assertFalse(report["fts_schema"]["rebuilt"])
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(ACT_1_URL, hits[0]["source_url"])
        self.assertGreaterEqual(len(pack["items"]), 1)
        self.assertEqual(ACT_1_URL, pack["items"][0]["source_url"])

    def test_discovers_only_real_host_act_links_and_honors_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: result(INDEX_URL, CATALOG_HTML, root / "catalog.cache"),
            }
            client = FakeHttpClient(responses)
            refs = vendor_joureikun.discover_documents(
                INDEX_URL, client=client, limit=2
            )

        self.assertEqual(
            [(INDEX_URL, CacheTier.INDEX)],
            client.calls,
        )
        self.assertEqual(2, len(refs))
        self.assertEqual(ACT_1_URL, refs[0]["source_url"])
        self.assertEqual(INDEX_URL, refs[0]["discovered_from"])
        self.assertNotIn("99999", json.dumps(refs))
        self.assertNotIn("outside.invalid", json.dumps(refs))

    def test_act_is_decoded_and_split_into_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fetched = result(ACT_1_URL, ACT_HTML, root / "act.cache")
            payload = vendor_joureikun.fetch_document(
                {
                    "document_id": "regdoc_fixture",
                    "source_url": ACT_1_URL,
                    "title": "索引上の題名",
                    "discovered_from": INDEX_URL,
                },
                index_url=INDEX_URL,
                client=FakeHttpClient({ACT_1_URL: fetched}),
                source_name="Omachi Joureikun",
            )

        document = payload["document"]
        articles = payload["articles"]
        numbered = [item for item in articles if item["article_no"]]
        self.assertEqual("Omachi Sample Ordinance", document["title"])
        self.assertEqual("2023-04-01", document["promulgated_on"])
        self.assertEqual(ACT_1_URL, document["source_url"])
        self.assertEqual("joureikun", document["adapter"])
        self.assertEqual(["第1条", "第2条"], [item["article_no"] for item in numbered])
        self.assertTrue(all(ACT_1_URL in item["locator"] for item in articles))
        self.assertEqual("utf-8", payload["provenance"]["transform"]["encoding"])
        self.assertEqual("2026-08-21T00:00:00Z", payload["provenance"]["fetched_at"])

    def test_document_fallback_when_no_article_headings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fetched = result(ACT_2_URL, ACT_NO_HEADING_HTML, root / "act.cache")
            payload = vendor_joureikun.fetch_document(
                {
                    "document_id": "regdoc_fallback",
                    "source_url": ACT_2_URL,
                    "title": "Simple",
                    "discovered_from": INDEX_URL,
                },
                index_url=INDEX_URL,
                client=FakeHttpClient({ACT_2_URL: fetched}),
            )

        self.assertEqual(1, len(payload["articles"]))
        self.assertIsNone(payload["articles"][0]["article_no"])
        self.assertIn("条見出し", payload["articles"][0]["text"][:200] + "前文")

    def test_deduplicates_act_links(self) -> None:
        dup_html = """\
<!doctype html><html><body>
<a href="../../act/1.html">One</a>
<a href="../../act/1.html">One duplicate</a>
<a href="../../act/2.html">Two</a>
</body></html>
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fetched = result(INDEX_URL, dup_html, root / "catalog.cache")
            refs = vendor_joureikun.discover_documents(
                INDEX_URL, client=FakeHttpClient({INDEX_URL: fetched})
            )
        self.assertEqual(2, len(refs))
        self.assertEqual([ACT_1_URL, ACT_2_URL], [r["source_url"] for r in refs])

    def test_host_drift_safety_stop_on_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drift_fetched = FetchResult(
                url=INDEX_URL,
                final_url="https://outside.invalid/joureikun/aggregate/catalog/index.html",
                body=CATALOG_HTML.encode("utf-8"),
                fetched_at="2026-08-21T00:00:00Z",
                content_type="text/html",
                encoding="utf-8",
                cache_path=root / "catalog.cache",
                sha256=hashlib.sha256(CATALOG_HTML.encode("utf-8")).hexdigest(),
                from_cache=False,
            )
            (root / "catalog.cache").write_bytes(CATALOG_HTML.encode("utf-8"))
            with self.assertRaisesRegex(
                vendor_joureikun.StructureMismatchError, "outside"
            ) as raised:
                vendor_joureikun.discover_documents(
                    INDEX_URL,
                    client=FakeHttpClient({INDEX_URL: drift_fetched}),
                )
            self.assertEqual("structure_mismatch", raised.exception.status)

            act_drift = FetchResult(
                url=ACT_1_URL,
                final_url="https://outside.invalid/joureikun/act/1.html",
                body=ACT_HTML.encode("utf-8"),
                fetched_at="2026-08-21T00:00:00Z",
                content_type="text/html",
                encoding="utf-8",
                cache_path=root / "act.cache",
                sha256=hashlib.sha256(ACT_HTML.encode("utf-8")).hexdigest(),
                from_cache=False,
            )
            (root / "act.cache").write_bytes(ACT_HTML.encode("utf-8"))
            with self.assertRaisesRegex(
                vendor_joureikun.StructureMismatchError, "outside"
            ):
                vendor_joureikun.fetch_document(
                    {
                        "document_id": "regdoc_drift",
                        "source_url": ACT_1_URL,
                        "title": "Drift",
                        "discovered_from": INDEX_URL,
                    },
                    index_url=INDEX_URL,
                    client=FakeHttpClient({ACT_1_URL: act_drift}),
                )

    def test_missing_expected_index_reports_structure_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fetched = result(
                INDEX_URL,
                "<html><body><p>No act links here</p></body></html>",
                Path(temporary) / "catalog.cache",
            )
            with self.assertRaisesRegex(
                vendor_joureikun.StructureMismatchError, "act links"
            ) as raised:
                vendor_joureikun.discover_documents(
                    INDEX_URL,
                    client=FakeHttpClient({INDEX_URL: fetched}),
                )
            self.assertEqual("structure_mismatch", raised.exception.status)

    def test_robots_refusal_stops_before_discovery(self) -> None:
        calls: list[str] = []
        client = FakeHttpClient({})

        def denied(
            url: str,
            *,
            tier: CacheTier,
            cache_key: str | None = None,
            sensitive_query_keys: set[str] | None = None,
        ) -> FetchResult:
            del tier, cache_key, sensitive_query_keys
            calls.append(url)
            raise RobotsDeniedError("robots.txt により取得できません")

        with patch.object(client, "fetch", side_effect=denied):
            with self.assertRaises(RobotsDeniedError):
                vendor_joureikun.discover_documents(INDEX_URL, client=client)
        self.assertEqual([INDEX_URL], calls)

        stderr = io.StringIO()
        with (
            patch(
                "modules.regulations.vendor_joureikun.ingest_joureikun",
                side_effect=RobotsDeniedError("robots.txt により取得できません"),
            ),
            redirect_stderr(stderr),
        ):
            status = vendor_joureikun.main(
                ["--index-url", INDEX_URL, "--db", "unused.db", "--limit", "1"]
            )
        self.assertEqual(1, status)
        self.assertEqual("robots_denied", json.loads(stderr.getvalue())["status"])

    def test_entry_point_builds_honest_client_and_declares_fetch_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: result(INDEX_URL, CATALOG_HTML, root / "catalog.cache"),
                ACT_1_URL: result(ACT_1_URL, ACT_HTML, root / "act1.cache"),
            }
            client = FakeHttpClient(responses)
            cache_dir = root / "joureikun-cache"
            with patch.object(
                vendor_joureikun,
                "HttpClient",
                return_value=client,
            ) as http_client:
                vendor_joureikun.ingest_joureikun(
                    INDEX_URL,
                    root / "regulations.db",
                    cache_dir=cache_dir,
                    limit=1,
                    timeout=12,
                )

        http_client.assert_called_once_with(
            cache_dir,
            user_agent=REGULATIONS_USER_AGENT,
            offline=False,
            refresh=False,
            timeout=12,
            min_interval_seconds=1.5,
        )
        self.assertGreaterEqual(
            float(http_client.call_args.kwargs["min_interval_seconds"]),
            1.5,
        )
        self.assertEqual(
            [
                (INDEX_URL, CacheTier.INDEX),
                (ACT_1_URL, CacheTier.DOCUMENT),
            ],
            client.calls,
        )

    def test_cli_runs_end_to_end_on_synthetic_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog.html"
            catalog.write_text(CATALOG_HTML, encoding="utf-8")
            # Use file-like via HttpClient fake? Test CLI via patched HttpClient.
            responses = {
                INDEX_URL: result(INDEX_URL, CATALOG_HTML, root / "catalog.cache"),
                ACT_1_URL: result(ACT_1_URL, ACT_HTML, root / "act1.cache"),
                ACT_2_URL: result(ACT_2_URL, ACT_NO_HEADING_HTML, root / "act2.cache"),
            }
            client = FakeHttpClient(responses)
            db_path = root / "out.db"
            stdout = io.StringIO()
            with (
                patch.object(vendor_joureikun, "HttpClient", return_value=client),
                redirect_stdout(stdout),
            ):
                status = vendor_joureikun.main(
                    [
                        "--index-url",
                        INDEX_URL,
                        "--db",
                        str(db_path),
                        "--source-name",
                        "Omachi Joureikun",
                        "--limit",
                        "2",
                    ]
                )
            self.assertEqual(0, status)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("ok", payload["status"])
            self.assertEqual(2, payload["documents"])
            self.assertGreaterEqual(payload["articles"], 2)
            with closing(sqlite3.connect(db_path)) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM regulation_documents"
                ).fetchone()[0]
                self.assertEqual(2, count)

    def test_manifest_distinguishes_cached_and_refreshed_retrieval(self) -> None:
        catalog_body = CATALOG_HTML.encode()
        act_body = ACT_HTML.encode()
        responses = {
            INDEX_URL: catalog_body,
            ACT_1_URL: act_body,
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
                    "--index-url",
                    INDEX_URL,
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
                    self.assertEqual(0, vendor_joureikun.main(argv))
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

        cached_retrieval = cached["retrieval"]  # type: ignore[assignment]
        refreshed_retrieval = refreshed["retrieval"]  # type: ignore[assignment]
        self.assertEqual(2, cached_retrieval["cache_hit_count"])  # type: ignore[index]
        self.assertEqual(0, cached_retrieval["live_request_count"])  # type: ignore[index]
        self.assertFalse(cached_retrieval["latestness_rechecked_this_run"])  # type: ignore[index]
        self.assertEqual(2, refreshed_retrieval["refresh_count"])  # type: ignore[index]
        self.assertEqual(2, refreshed_retrieval["live_request_count"])  # type: ignore[index]
        self.assertTrue(refreshed_retrieval["latestness_rechecked_this_run"])  # type: ignore[index]

    def test_discover_documents_resolves_landing_page(self) -> None:
        landing_url = "https://public.joureikun.jp/okoppe_town/reiki/"
        cat_url = "https://public.joureikun.jp/okoppe_town/reiki/aggregate/catalog/index.html"
        landing_html = """<html><body>
        <button onclick="location.href='aggregate/catalog/index.html'">例規一覧</button>
        </body></html>"""
        catalog_html = """<html><body>
        <ul><li><a href="../../act/119000078.html">興部町条例</a></li></ul>
        </body></html>"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            client = FakeHttpClient(
                {
                    landing_url: result(landing_url, landing_html, tmp / "landing.html"),
                    cat_url: result(cat_url, catalog_html, tmp / "cat.html"),
                }
            )
            refs = vendor_joureikun.discover_documents(landing_url, client=client)
            self.assertEqual(1, len(refs))
            self.assertEqual(
                "https://public.joureikun.jp/okoppe_town/reiki/act/119000078.html",
                refs[0]["source_url"],
            )
            self.assertEqual("興部町条例", refs[0]["title"])


if __name__ == "__main__":
    unittest.main()
