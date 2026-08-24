"""Synthetic tests for the D1-Law (d1w_reiki) regulations adapter."""

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
    FetchError,
    FetchResult,
    HttpClient,
    RobotsDeniedError,
    _RawResponse,
)
from lcaios.tests.http_fakes import FakeHttpClient
from modules.regulations import context_pack, search, vendor_d1law_reiki

INDEX_URL = "https://example.invalid/d1law/reiki.html"
MOKUJI_BUNYA_URL = "https://example.invalid/d1law/mokuji_bunya.html"
MOKUJI_CHILED_URL = "https://example.invalid/d1law/mokuji_bunya_chiled.html"
MOKUJI_INDEX_URL = "https://example.invalid/d1law/mokuji_bunya_index.html"
BUNYA_001_URL = "https://example.invalid/d1law/bunya_0010000.html"
BUNYA_002_URL = "https://example.invalid/d1law/bunya_0020000.html"
DOC_1_ID = "r0001"
DOC_2_ID = "r0002"
J_URL_1 = f"https://example.invalid/d1law/{DOC_1_ID}/{DOC_1_ID}_j.html"
J_URL_2 = f"https://example.invalid/d1law/{DOC_2_ID}/{DOC_2_ID}_j.html"


def _result(
    url: str, body: str | bytes, cache_path: Path, *, encoding: str = "utf-8"
) -> FetchResult:
    raw = body if isinstance(body, bytes) else body.encode(encoding)
    cache_path.write_bytes(raw)
    return FetchResult(
        url=url,
        final_url=url,
        body=raw,
        fetched_at="2026-07-23T00:00:00Z",
        content_type="text/html",
        encoding=encoding,
        cache_path=cache_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        from_cache=False,
    )


REIKI_HTML = """<html><head><title>Reiki</title></head>
<frameset cols="30%,70%">
<frame src="mokuji_bunya.html">
</frameset></html>"""

MOKUJI_BUNYA_HTML = """<html><head><title>Mokuji</title></head>
<frameset><frame src="mokuji_bunya_chiled.html"></frameset></html>"""

MOKUJI_CHILED_HTML = """<html><head><title>Chiled</title></head>
<frameset><frame src="mokuji_bunya_index.html"></frameset></html>"""

MOKUJI_INDEX_HTML = """<html><head><title>Bunya Index</title></head>
<body>
<a href="bunya_0010000.html">001</a>
<a href="bunya_0020000.html">002</a>
<a href="https://outside.invalid/bunya_evil.html">evil</a>
</body></html>"""

BUNYA_001_HTML = f"""<html><head><title>Bunya 001</title></head>
<body>
<A HREF="javascript:OpenResDataWin('{DOC_1_ID}')" title="令和5年4月1日 条例第1号">架空町あき地管理条例</A>
<A HREF="javascript:OpenResDataWin('{DOC_2_ID}')" title="令和5年5月1日 規則第2号">架空町安全規則</A>
<script>var guess = "javascript:OpenResDataWin('r9999')";</script>
</body></html>"""

BUNYA_002_HTML = """<html><head><title>Empty bunya</title></head>
<body><p>no entries here</p></body></html>"""

J_HTML = """<html><head><title>架空町あき地管理条例</title></head>
<body>
<p>令和5年4月1日条例第1号</p>
<p>第1条　この条例は、あき地の適正な管理について定める。</p>
<p>第2条　町は、必要な措置を講ずるものとする。</p>
</body></html>"""

J_HTML_2 = """<html><head><title>架空町安全規則</title></head>
<body><p>第1条　この規則は、安全について定める。</p></body></html>"""


class D1LawAdapterTests(unittest.TestCase):
    def test_frameset_traversal_and_openresdatawin_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, BUNYA_001_HTML, root / "d.cache"),
                BUNYA_002_URL: _result(BUNYA_002_URL, BUNYA_002_HTML, root / "e.cache"),
            }
            client = FakeHttpClient(responses)
            refs = vendor_d1law_reiki.discover_documents(INDEX_URL, client=client)
        self.assertEqual(2, len(refs))
        urls = {r["source_url"] for r in refs}
        self.assertIn(J_URL_1, urls)
        self.assertIn(J_URL_2, urls)
        # traversal follows frame src + bunya links; evil host not fetched
        called_urls = [u for u, _ in client.calls]
        self.assertIn(INDEX_URL, called_urls)
        self.assertIn(MOKUJI_BUNYA_URL, called_urls)
        self.assertIn(MOKUJI_CHILED_URL, called_urls)
        self.assertIn(MOKUJI_INDEX_URL, called_urls)
        self.assertIn(BUNYA_001_URL, called_urls)
        self.assertNotIn("https://outside.invalid/bunya_evil.html", called_urls)
        self.assertNotIn("https://outside.invalid/bunya_evil.html", json.dumps(refs))
        self.assertNotIn("r9999", json.dumps(refs))
        # limit
        with tempfile.TemporaryDirectory() as temporary2:
            root2 = Path(temporary2)
            responses2 = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root2 / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root2 / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root2 / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root2 / "c.cache"
                ),
                BUNYA_001_URL: _result(
                    BUNYA_001_URL, BUNYA_001_HTML, root2 / "d.cache"
                ),
            }
            client2 = FakeHttpClient(responses2)
            limited = vendor_d1law_reiki.discover_documents(
                INDEX_URL, client=client2, limit=1
            )
        self.assertEqual(1, len(limited))
        self.assertEqual(J_URL_1, limited[0]["source_url"])

    def test_ingested_database_works_with_existing_search_and_context_pack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            j_raw = J_HTML.encode("cp932")
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, BUNYA_001_HTML, root / "d.cache"),
                BUNYA_002_URL: _result(BUNYA_002_URL, BUNYA_002_HTML, root / "e.cache"),
                J_URL_1: _result(J_URL_1, j_raw, root / "j1.cache", encoding="cp932"),
                J_URL_2: _result(J_URL_2, J_HTML_2, root / "j2.cache"),
            }
            client = FakeHttpClient(responses)
            database = root / "regulations.db"
            with patch.object(vendor_d1law_reiki, "HttpClient", return_value=client):
                report = vendor_d1law_reiki.ingest_d1law(
                    INDEX_URL, database, source_name="架空町例規集", limit=1
                )
            with closing(sqlite3.connect(database)) as connection, connection:
                hits = search.search_database(connection, "あき地", 5)
                pack = context_pack.build_context_pack(connection, "あき地", 5, 1000)
        self.assertEqual(1, report["documents"])
        self.assertGreaterEqual(report["articles"], 2)
        self.assertEqual(report["fts_tokenizer"], report["fts_schema"]["tokenizer"])
        self.assertFalse(report["fts_schema"]["rebuilt"])
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(J_URL_1, hits[0]["source_url"])
        self.assertGreaterEqual(len(pack["items"]), 1)

    def test_shift_jis_j_html_is_decoded_and_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = J_HTML.encode("cp932")
            fetched = _result(J_URL_1, raw, root / "j.cache", encoding="utf-8")
            payload = vendor_d1law_reiki.fetch_document(
                {
                    "document_id": "regdoc_fixture",
                    "source_url": J_URL_1,
                    "title": "索引上の題名",
                    "discovered_from": BUNYA_001_URL,
                },
                index_url=INDEX_URL,
                client=FakeHttpClient({J_URL_1: fetched}),
                source_name="架空町例規集",
            )
        articles = payload["articles"]
        numbered = [item for item in articles if item["article_no"]]
        self.assertEqual("架空町あき地管理条例", payload["document"]["title"])
        self.assertEqual("2023-04-01", payload["document"]["promulgated_on"])
        self.assertEqual(J_URL_1, payload["document"]["source_url"])
        self.assertEqual(["第1条", "第2条"], [item["article_no"] for item in numbered])
        self.assertTrue(all(J_URL_1 in item["locator"] for item in articles))
        self.assertEqual("cp932", payload["provenance"]["transform"]["encoding"])

    def test_meta_declared_charset_regex_matches_bytes(self) -> None:
        match = vendor_d1law_reiki._META_CHARSET_RE.search(
            b'<meta charset="shift_jis">'
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(b"shift_jis", match.group(1))

    def test_euc_jp_body_with_meta_charset_decodes_correctly(self) -> None:
        # Synthetic fixture: page declares EUC-JP in <meta>, no HTTP header.
        text = "第一条　この条例は、町民の福祉の増進を目的とする。"
        raw = (
            '<html><head><meta charset="euc-jp">'
            f"<title>架空町例規</title></head><body><p>{text}</p></body></html>"
        ).encode("euc_jp")
        decoded, encoding = vendor_d1law_reiki.decode_html(raw, None)
        self.assertEqual("euc-jp", encoding)
        self.assertIn(text, decoded)

    def test_derived_url_failure_is_per_document_safe_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, BUNYA_001_HTML, root / "d.cache"),
                BUNYA_002_URL: _result(BUNYA_002_URL, BUNYA_002_HTML, root / "e.cache"),
                # Only j1 succeeds; j2 missing -> FetchError for that doc
                J_URL_1: _result(J_URL_1, J_HTML, root / "j1.cache"),
            }

            class FailingClient(FakeHttpClient):
                def fetch(
                    self,
                    url: str,
                    *,
                    tier: CacheTier,
                    cache_key: str | None = None,
                    sensitive_query_keys: set[str] | None = None,
                ) -> FetchResult:  # type: ignore[override]
                    if url == J_URL_2:
                        self.calls.append((url, tier))
                        raise FetchError("HTTP 404")
                    return super().fetch(
                        url,
                        tier=tier,
                        cache_key=cache_key,
                        sensitive_query_keys=sensitive_query_keys,
                    )

            client = FailingClient(responses)
            database = root / "regulations.db"
            with patch.object(vendor_d1law_reiki, "HttpClient", return_value=client):
                report = vendor_d1law_reiki.ingest_d1law(
                    INDEX_URL, database, source_name="架空町例規集"
                )
            self.assertEqual(1, report["documents"])
            self.assertEqual(1, report["statuses"].get("fetch_failed", 0))
            self.assertGreaterEqual(report["articles"], 1)
            # ensure j1 document stored, j2 not counted as document
            with closing(sqlite3.connect(database)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM regulation_documents"
                ).fetchone()[0]
            self.assertEqual(1, count)
            # no guess-retry: derived URL failure does not cause alternative fetch
            self.assertEqual(1, sum(1 for url, _ in client.calls if url == J_URL_2))

    def test_missing_openresdatawin_reports_structure_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty_bunya = (
                """<html><body><p>no regulation links here</p></body></html>"""
            )
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, empty_bunya, root / "d.cache"),
                BUNYA_002_URL: _result(BUNYA_002_URL, empty_bunya, root / "e.cache"),
            }
            client = FakeHttpClient(responses)
            with self.assertRaisesRegex(
                vendor_d1law_reiki.StructureMismatchError, "OpenResDataWin"
            ):
                vendor_d1law_reiki.discover_documents(INDEX_URL, client=client)
            try:
                vendor_d1law_reiki.discover_documents(
                    INDEX_URL, client=FakeHttpClient(responses)
                )
            except vendor_d1law_reiki.StructureMismatchError as exc:
                self.assertEqual("structure_mismatch", exc.status)

    def test_host_drift_raises_structure_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drift = _result(INDEX_URL, REIKI_HTML, root / "reiki.cache")
            drift = FetchResult(
                url=drift.url,
                final_url="https://evil.example.com/d1law/reiki.html",
                body=drift.body,
                fetched_at=drift.fetched_at,
                content_type=drift.content_type,
                encoding=drift.encoding,
                cache_path=drift.cache_path,
                sha256=drift.sha256,
                from_cache=False,
            )
            client = FakeHttpClient({INDEX_URL: drift})
            with self.assertRaisesRegex(
                vendor_d1law_reiki.StructureMismatchError, "host"
            ):
                vendor_d1law_reiki.discover_documents(INDEX_URL, client=client)

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
                vendor_d1law_reiki.discover_documents(INDEX_URL, client=client)
        self.assertEqual([INDEX_URL], calls)
        stderr = io.StringIO()
        with (
            patch(
                "modules.regulations.vendor_d1law_reiki.ingest_d1law",
                side_effect=RobotsDeniedError("robots.txt により取得できません"),
            ),
            redirect_stderr(stderr),
        ):
            status = vendor_d1law_reiki.main(
                ["--index-url", INDEX_URL, "--db", "unused.db", "--limit", "1"]
            )
        self.assertEqual(1, status)
        self.assertEqual("robots_denied", json.loads(stderr.getvalue())["status"])

    def test_entry_point_builds_honest_client_and_declares_fetch_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, BUNYA_001_HTML, root / "d.cache"),
                J_URL_1: _result(J_URL_1, J_HTML, root / "j1.cache"),
            }
            client = FakeHttpClient(responses)
            cache_dir = root / "d1law-cache"
            with patch.object(
                vendor_d1law_reiki, "HttpClient", return_value=client
            ) as http_client:
                vendor_d1law_reiki.ingest_d1law(
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
            float(http_client.call_args.kwargs["min_interval_seconds"]), 1.5
        )
        self.assertEqual(
            [
                (INDEX_URL, CacheTier.INDEX),
                (MOKUJI_BUNYA_URL, CacheTier.INDEX),
                (MOKUJI_CHILED_URL, CacheTier.INDEX),
                (MOKUJI_INDEX_URL, CacheTier.INDEX),
                (BUNYA_001_URL, CacheTier.INDEX),
                (J_URL_1, CacheTier.DOCUMENT),
            ],
            client.calls[:6],
        )

    def test_cli_end_to_end_on_synthetic_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: _result(INDEX_URL, REIKI_HTML, root / "reiki.cache"),
                MOKUJI_BUNYA_URL: _result(
                    MOKUJI_BUNYA_URL, MOKUJI_BUNYA_HTML, root / "a.cache"
                ),
                MOKUJI_CHILED_URL: _result(
                    MOKUJI_CHILED_URL, MOKUJI_CHILED_HTML, root / "b.cache"
                ),
                MOKUJI_INDEX_URL: _result(
                    MOKUJI_INDEX_URL, MOKUJI_INDEX_HTML, root / "c.cache"
                ),
                BUNYA_001_URL: _result(BUNYA_001_URL, BUNYA_001_HTML, root / "d.cache"),
                BUNYA_002_URL: _result(BUNYA_002_URL, BUNYA_002_HTML, root / "e.cache"),
                J_URL_1: _result(J_URL_1, J_HTML, root / "j1.cache"),
            }
            client = FakeHttpClient(responses)
            db = root / "cli.db"
            cache_dir = root / "cache"
            manifest_dir = root / "manifests"
            with patch.object(vendor_d1law_reiki, "HttpClient", return_value=client):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = vendor_d1law_reiki.main(
                        [
                            "--index-url",
                            INDEX_URL,
                            "--db",
                            str(db),
                            "--cache-dir",
                            str(cache_dir),
                            "--manifest-dir",
                            str(manifest_dir),
                            "--run-id",
                            "cli-test",
                            "--limit",
                            "1",
                        ]
                    )
            self.assertEqual(0, status)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("ok", payload["status"])
            self.assertEqual(1, payload["documents"])
            with closing(sqlite3.connect(db)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM regulation_articles"
                ).fetchone()[0]
            self.assertGreaterEqual(count, 2)

    def test_manifest_distinguishes_cached_and_refreshed(self) -> None:
        base_responses = {
            INDEX_URL: REIKI_HTML.encode(),
            MOKUJI_BUNYA_URL: MOKUJI_BUNYA_HTML.encode(),
            MOKUJI_CHILED_URL: MOKUJI_CHILED_HTML.encode(),
            MOKUJI_INDEX_URL: MOKUJI_INDEX_HTML.encode(),
            BUNYA_001_URL: BUNYA_001_HTML.encode(),
            J_URL_1: J_HTML.encode(),
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
                    body=base_responses[url],
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    fetched_at=datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                )

            def run(run_id: str, *, refresh: bool = False) -> dict:
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
                    self.assertEqual(0, vendor_d1law_reiki.main(argv))
                return json.loads(
                    (manifests / f"{run_id}.json").read_text(encoding="utf-8")
                )

            with (
                patch.object(HttpClient, "_assert_robots_allowed"),
                patch.object(
                    HttpClient, "_request_once", autospec=True, side_effect=request_once
                ),
            ):
                run("network")
                cached = run("cached")
                refreshed = run("refreshed", refresh=True)
        self.assertEqual(6, cached["retrieval"]["cache_hit_count"])
        self.assertEqual(0, cached["retrieval"]["live_request_count"])
        self.assertEqual(6, refreshed["retrieval"]["refresh_count"])


if __name__ == "__main__":
    unittest.main()
