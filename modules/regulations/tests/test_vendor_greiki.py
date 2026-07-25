"""Synthetic tests for the g-reiki regulations adapter."""

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
from modules.regulations import context_pack, search, vendor_greiki

BASE_URL = "https://example.invalid/fake-town/"
ENTRY_URL = BASE_URL + "reiki_menu.html"
KANA_DEFAULT_URL = BASE_URL + "reiki_kana/kana_default.html"
KANA_A_URL = BASE_URL + "reiki_kana/r_50_a.html"
DOC_1_URL = BASE_URL + "reiki_honbun/x001RG00000001.html"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "greiki"


def fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def result(
    url: str,
    body: str | bytes,
    cache_path: Path,
    *,
    encoding: str = "utf-8",
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


class GreikiAdapterTests(unittest.TestCase):
    def test_ingested_database_works_with_existing_search_and_context_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                ENTRY_URL: result(
                    ENTRY_URL, fixture("reiki_menu.html"), root / "menu.cache"
                ),
                KANA_DEFAULT_URL: result(
                    KANA_DEFAULT_URL,
                    fixture("kana_default.html"),
                    root / "kana-default.cache",
                ),
                KANA_A_URL: result(
                    KANA_A_URL, fixture("r_50_a.html"), root / "kana-a.cache"
                ),
                DOC_1_URL: result(
                    DOC_1_URL,
                    fixture("regulation.html").encode("cp932"),
                    root / "document.cache",
                    encoding="cp932",
                ),
            }

            client = FakeHttpClient(responses)
            database = root / "regulations.db"
            with patch.object(
                vendor_greiki,
                "HttpClient",
                return_value=client,
            ):
                report = vendor_greiki.ingest_greiki(
                    BASE_URL,
                    database,
                    source_name="架空町例規集",
                    limit=1,
                )
            with closing(sqlite3.connect(database)) as connection, connection:
                hits = search.search_database(connection, "あき地", 5)
                pack = context_pack.build_context_pack(
                    connection, "あき地", 5, 1000
                )

        self.assertEqual(1, report["documents"])
        self.assertGreaterEqual(report["articles"], 2)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(DOC_1_URL, hits[0]["source_url"])
        self.assertGreaterEqual(len(pack["items"]), 1)
        self.assertEqual(DOC_1_URL, pack["items"][0]["source_url"])

    def test_discovers_only_real_tenant_links_and_honors_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                ENTRY_URL: result(
                    ENTRY_URL, fixture("reiki_menu.html"), root / "menu.cache"
                ),
                KANA_DEFAULT_URL: result(
                    KANA_DEFAULT_URL,
                    fixture("kana_default.html"),
                    root / "kana-default.cache",
                ),
                KANA_A_URL: result(
                    KANA_A_URL, fixture("r_50_a.html"), root / "kana-a.cache"
                ),
            }
            client = FakeHttpClient(responses)
            refs = vendor_greiki.discover_documents(
                BASE_URL, client=client, limit=2
            )

        self.assertEqual(
            [
                (ENTRY_URL, CacheTier.INDEX),
                (KANA_DEFAULT_URL, CacheTier.INDEX),
                (KANA_A_URL, CacheTier.INDEX),
            ],
            client.calls,
        )
        self.assertEqual(2, len(refs))
        self.assertEqual(DOC_1_URL, refs[0]["source_url"])
        self.assertEqual(KANA_A_URL, refs[0]["discovered_from"])
        self.assertNotIn("x001RG99999999", json.dumps(refs))
        self.assertNotIn("outside.invalid", json.dumps(refs))

    def test_shift_jis_document_is_decoded_and_split_into_articles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = fixture("regulation.html").encode("cp932")
            fetched = result(
                DOC_1_URL,
                raw,
                root / "document.cache",
                encoding="utf-8",
            )
            payload = vendor_greiki.fetch_document(
                {
                    "document_id": "regdoc_fixture",
                    "source_url": DOC_1_URL,
                    "title": "索引上の題名",
                    "discovered_from": KANA_A_URL,
                },
                base_url=BASE_URL,
                client=FakeHttpClient({DOC_1_URL: fetched}),
                source_name="架空町例規集",
            )

        document = payload["document"]
        articles = payload["articles"]
        numbered = [item for item in articles if item["article_no"]]
        self.assertEqual("架空町あき地管理条例", document["title"])
        self.assertEqual("2023-04-01", document["promulgated_on"])
        self.assertEqual("第8編 生活環境", document["category"])
        self.assertEqual(DOC_1_URL, document["source_url"])
        self.assertEqual(["第1条", "第2条"], [item["article_no"] for item in numbered])
        self.assertNotIn("第99条", "\n".join(item["text"] for item in articles))
        self.assertTrue(all(DOC_1_URL in item["locator"] for item in articles))
        self.assertEqual("cp932", payload["provenance"]["transform"]["encoding"])
        self.assertEqual("2026-07-23T00:00:00Z", payload["provenance"]["fetched_at"])

    def test_missing_expected_index_reports_structure_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            help_url = BASE_URL + "help.html"
            fetched = result(
                ENTRY_URL,
                "<html><body><a href='help.html'>ヘルプ</a></body></html>",
                Path(temporary) / "menu.cache",
            )

            with self.assertRaisesRegex(
                vendor_greiki.StructureMismatchError, "五十音 index"
            ) as raised:
                vendor_greiki.discover_documents(
                    BASE_URL,
                    client=FakeHttpClient(
                        {
                            ENTRY_URL: fetched,
                            help_url: fetched,
                        }
                    ),
                )

        self.assertEqual("structure_mismatch", raised.exception.status)

    def test_robots_refusal_stops_before_index_discovery(self) -> None:
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
                vendor_greiki.discover_documents(BASE_URL, client=client)
        self.assertEqual([ENTRY_URL], calls)

        stderr = io.StringIO()
        with patch(
            "modules.regulations.vendor_greiki.ingest_greiki",
            side_effect=RobotsDeniedError("robots.txt により取得できません"),
        ), redirect_stderr(stderr):
            status = vendor_greiki.main(
                ["--base-url", BASE_URL, "--db", "unused.db", "--limit", "1"]
            )
        self.assertEqual(1, status)
        self.assertEqual("robots_denied", json.loads(stderr.getvalue())["status"])

    def test_entry_point_builds_honest_client_and_declares_fetch_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                ENTRY_URL: result(
                    ENTRY_URL, fixture("reiki_menu.html"), root / "menu.cache"
                ),
                KANA_DEFAULT_URL: result(
                    KANA_DEFAULT_URL,
                    fixture("kana_default.html"),
                    root / "kana-default.cache",
                ),
                KANA_A_URL: result(
                    KANA_A_URL, fixture("r_50_a.html"), root / "kana-a.cache"
                ),
                DOC_1_URL: result(
                    DOC_1_URL,
                    fixture("regulation.html").encode("cp932"),
                    root / "document.cache",
                    encoding="cp932",
                ),
            }
            client = FakeHttpClient(responses)
            cache_dir = root / "greiki-cache"
            with patch.object(
                vendor_greiki,
                "HttpClient",
                return_value=client,
            ) as http_client:
                vendor_greiki.ingest_greiki(
                    BASE_URL,
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
                (ENTRY_URL, CacheTier.INDEX),
                (KANA_DEFAULT_URL, CacheTier.INDEX),
                (KANA_A_URL, CacheTier.INDEX),
                (DOC_1_URL, CacheTier.DOCUMENT),
            ],
            client.calls,
        )

    def test_manifest_distinguishes_cached_and_refreshed_retrieval(self) -> None:
        responses = {
            ENTRY_URL: fixture("reiki_menu.html").encode(),
            KANA_DEFAULT_URL: fixture("kana_default.html").encode(),
            KANA_A_URL: fixture("r_50_a.html").encode(),
            DOC_1_URL: fixture("regulation.html").encode("cp932"),
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            manifests = root / "manifests"

            def request_once(client: HttpClient, url: str) -> _RawResponse:
                client.request_count += 1
                content_type = (
                    "text/html; charset=Shift_JIS"
                    if url == DOC_1_URL
                    else "text/html; charset=utf-8"
                )
                return _RawResponse(
                    url=url,
                    status=200,
                    body=responses[url],
                    headers={"Content-Type": content_type},
                    fetched_at=datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                )

            def run(run_id: str, *, refresh: bool = False) -> dict[str, object]:
                argv = [
                    "--base-url",
                    BASE_URL,
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
                    self.assertEqual(0, vendor_greiki.main(argv))
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
        self.assertEqual(4, cached_retrieval["cache_hit_count"])
        self.assertEqual(0, cached_retrieval["live_request_count"])
        self.assertFalse(cached_retrieval["latestness_rechecked_this_run"])
        self.assertEqual(
            "cache_hit",
            next(
                item["status"]
                for item in cached_retrieval["accesses"]
                if item["url"] == ENTRY_URL
            ),
        )
        self.assertEqual(4, cached_retrieval["sources"][0]["cache_hits"])
        self.assertEqual(0, cached_retrieval["sources"][0]["network_fetches"])
        self.assertEqual(0, cached_retrieval["sources"][0]["refreshes"])
        self.assertEqual(0, cached_retrieval["sources"][0]["cache_misses"])
        self.assertFalse(
            cached_retrieval["sources"][0]["latestness_rechecked_this_run"]
        )
        self.assertEqual(4, refreshed_retrieval["refresh_count"])
        self.assertEqual(4, refreshed_retrieval["live_request_count"])
        self.assertTrue(refreshed_retrieval["latestness_rechecked_this_run"])
        self.assertEqual(
            "refreshed",
            next(
                item["status"]
                for item in refreshed_retrieval["accesses"]
                if item["url"] == ENTRY_URL
            ),
        )
        self.assertEqual(0, refreshed_retrieval["sources"][0]["cache_hits"])
        self.assertEqual(4, refreshed_retrieval["sources"][0]["network_fetches"])
        self.assertEqual(4, refreshed_retrieval["sources"][0]["refreshes"])
        self.assertEqual(0, refreshed_retrieval["sources"][0]["cache_misses"])
        self.assertTrue(
            refreshed_retrieval["sources"][0][
                "latestness_rechecked_this_run"
            ]
        )


if __name__ == "__main__":
    unittest.main()
