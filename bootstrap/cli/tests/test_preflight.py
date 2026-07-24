"""Tests for bounded municipality source preflight."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from bootstrap.cli.http import FetchResult
from bootstrap.cli.preflight import (
    _write_new,
    preflight_municipality,
)


def fetched(url: str, html: str) -> FetchResult:
    """Build an in-memory official HTML response."""

    body = html.encode()
    return FetchResult(
        url=url,
        final_url=url,
        body=body,
        fetched_at="2026-07-24T00:00:00Z",
        content_type="text/html",
        encoding="utf-8",
        cache_path=Path("/tmp/unused-preflight-fixture"),
        sha256=hashlib.sha256(body).hexdigest(),
        from_cache=True,
    )


class FakeClient:
    """Serve only explicitly declared pages."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def fetch(self, url: str, **_: object) -> FetchResult:
        self.urls.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected fetch: {url}")
        return fetched(url, self.pages[url])


MUNICIPALITY = {
    "municipality_name": "架空町",
    "prefecture_name": "架空県",
    "area_code_5": "12345",
    "local_government_code_6": "123457",
    "official_home_url": "https://www.town.example.jp/",
}


class MunicipalityPreflightTests(unittest.TestCase):
    def test_discovers_supported_sources_without_fetching_documents_or_vendors(
        self,
    ) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <title>架空町</title>
                <a href="/council/">町議会</a>
                <a href="/finance/">財政</a>
                <a href="https://www1.g-reiki.net/example/reiki_menu.html">
                  例規集
                </a>
            """,
            "https://www.town.example.jp/council/": """
                <title>町議会</title>
                <a href="https://ssp.kaigiroku.net/tenant/example/">
                  会議録検索
                </a>
            """,
            "https://www.town.example.jp/finance/": """
                <title>予算・決算</title>
                <h1>予算・決算</h1>
                <a href="/files/r8-budget.pdf">令和8年度予算書</a>
                <a href="/files/r7-settlement.pdf">令和7年度決算書</a>
            """,
        }
        client = FakeClient(pages)
        report = preflight_municipality(MUNICIPALITY, client, max_pages=4)  # type: ignore[arg-type]
        self.assertEqual("ready", report["status"])
        self.assertEqual("kaigiroku_net", report["sources"]["minutes"]["adapter"])
        self.assertEqual("g_reiki", report["sources"]["regulations"]["adapter"])
        self.assertEqual(
            "official_document_index",
            report["sources"]["budget"]["adapter"],
        )
        self.assertEqual(
            "official_document_index",
            report["sources"]["settlement"]["adapter"],
        )
        self.assertEqual(3, len(client.urls))
        self.assertNotIn(
            "https://ssp.kaigiroku.net/tenant/example/",
            client.urls,
        )
        self.assertEqual(0, report["documents_downloaded"])
        self.assertFalse(report["database_created"])

    def test_detected_but_unsupported_minutes_vendor_is_not_ready(self) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <a href="https://example.gijiroku.com/voices/">
                  会議録検索
                </a>
            """
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(pages),
            max_pages=1,
        )
        self.assertEqual(
            "unsupported_vendor",
            report["sources"]["minutes"]["status"],
        )
        self.assertEqual("voices", report["sources"]["minutes"]["adapter"])

    def test_unobserved_sources_are_not_guessed(self) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <title>架空町</title>
                <a href="/tourism/">観光</a>
            """
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(pages),
            max_pages=8,
        )
        self.assertEqual("needs_attention", report["status"])
        for source in report["sources"].values():
            self.assertEqual("source_not_found", source["status"])

    def test_official_sitemap_is_followed_but_unrelated_pages_are_not(self) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <a href="/sitemap.html">サイトマップ</a>
                <a href="/tourism.html">観光</a>
            """,
            "https://www.town.example.jp/sitemap.html": """
                <a href="/council/minutes.html">会議録</a>
            """,
            "https://www.town.example.jp/council/minutes.html": """
                <title>会議録</title>
                <a href="/files/minutes.pdf">令和8年第1回会議録</a>
            """,
        }
        client = FakeClient(pages)
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            client,
            max_pages=4,
        )
        self.assertEqual("ready", report["sources"]["minutes"]["status"])
        self.assertEqual(
            [
                "https://www.town.example.jp/",
                "https://www.town.example.jp/sitemap.html",
                "https://www.town.example.jp/council/minutes.html",
            ],
            client.urls,
        )

    def test_report_writer_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            _write_new(path, {"status": "first"})
            with self.assertRaises(FileExistsError):
                _write_new(path, {"status": "second"})
            self.assertIn("first", path.read_text(encoding="utf-8"))

    def test_script_only_navigation_is_unknown_not_source_not_found(self) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <title>架空町</title>
                <script src="/assets/app.js"></script>
                <script src="/assets/menu.js"></script>
                <a href="/about/">町の紹介</a>
            """
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(pages),
            max_pages=2,
        )
        self.assertTrue(report["dynamic_navigation_detected"])
        for source in report["sources"].values():
            self.assertEqual("unknown_structure", source["status"])


if __name__ == "__main__":
    unittest.main()
