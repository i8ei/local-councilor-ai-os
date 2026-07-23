"""Synthetic tests for the g-reiki regulations adapter."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

MODULE_DIR = Path(__file__).resolve().parents[1]
MINUTES_DIR = MODULE_DIR.parent / "minutes-db"
for path in (str(MINUTES_DIR), str(MODULE_DIR)):
    while path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(MINUTES_DIR))
sys.path.insert(0, str(MODULE_DIR))

import vendor_greiki  # noqa: E402
import context_pack  # noqa: E402
import search  # noqa: E402
from adapters.base import FetchResult, RobotsDeniedError  # type: ignore  # noqa: E402


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

            def fetcher(url: str, **_: object) -> FetchResult:
                return responses[url]

            database = root / "regulations.db"
            report = vendor_greiki.ingest_greiki(
                BASE_URL,
                database,
                source_name="架空町例規集",
                limit=1,
                fetcher=fetcher,
            )
            with sqlite3.connect(database) as connection:
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
            calls: list[str] = []

            def fetcher(url: str, **_: object) -> FetchResult:
                calls.append(url)
                return responses[url]

            refs = vendor_greiki.discover_documents(
                BASE_URL, limit=2, fetcher=fetcher
            )

        self.assertEqual([ENTRY_URL, KANA_DEFAULT_URL, KANA_A_URL], calls)
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
                source_name="架空町例規集",
                fetcher=lambda _url, **_kwargs: fetched,
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
                    fetcher=lambda _url, **_kwargs: fetched,
                )

        self.assertEqual("structure_mismatch", raised.exception.status)

    def test_robots_refusal_stops_before_index_discovery(self) -> None:
        calls: list[str] = []

        def denied(url: str, **_: object) -> FetchResult:
            calls.append(url)
            raise RobotsDeniedError("robots.txt により取得できません")

        with self.assertRaises(RobotsDeniedError):
            vendor_greiki.discover_documents(BASE_URL, fetcher=denied)
        self.assertEqual([ENTRY_URL], calls)

        stderr = io.StringIO()
        with patch(
            "vendor_greiki.ingest_greiki",
            side_effect=RobotsDeniedError("robots.txt により取得できません"),
        ), redirect_stderr(stderr):
            status = vendor_greiki.main(
                ["--base-url", BASE_URL, "--db", "unused.db", "--limit", "1"]
            )
        self.assertEqual(1, status)
        self.assertEqual("robots_denied", json.loads(stderr.getvalue())["status"])

    def test_fetcher_sets_honest_user_agent_and_minimum_interval(self) -> None:
        observed: dict[str, object] = {}

        def fake_polite_fetch(
            url: str, *, cache_dir: Path, timeout: float
        ) -> object:
            observed["url"] = url
            observed["cache_dir"] = cache_dir
            observed["timeout"] = timeout
            observed["user_agent"] = vendor_greiki.fetch_base.USER_AGENT
            observed["interval"] = (
                vendor_greiki.fetch_base.MIN_REQUEST_INTERVAL_SECONDS
            )
            return object()

        with patch.object(
            vendor_greiki.fetch_base, "polite_fetch", side_effect=fake_polite_fetch
        ):
            vendor_greiki.fetch_url(
                ENTRY_URL, cache_dir=Path("/tmp/greiki-test-cache"), timeout=12
            )

        self.assertEqual(vendor_greiki.USER_AGENT, observed["user_agent"])
        self.assertGreaterEqual(float(observed["interval"]), 1.5)


if __name__ == "__main__":
    unittest.main()
