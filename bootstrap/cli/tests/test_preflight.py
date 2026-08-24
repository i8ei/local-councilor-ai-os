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
        self.assertEqual("needs_attention", report["status"])
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
        self.assertEqual("needs_review", report["sources"]["minutes"]["status"])
        self.assertEqual(
            [
                "https://www.town.example.jp/",
                "https://www.town.example.jp/sitemap.html",
                "https://www.town.example.jp/council/minutes.html",
            ],
            client.urls,
        )

    def test_preflight_never_grants_ready_discovery_only_proposes(self) -> None:
        """Structural guard: no code path may emit status="ready"."""

        scenarios = [
            # kaigiroku_net vendor linked from official page
            {
                "https://www.town.example.jp/": "<a href='/council/'>町議会</a>",
                "https://www.town.example.jp/council/": (
                    "<a href='https://ssp.kaigiroku.net/tenant/example/'>会議録検索</a>"
                )
            },
            # g_reiki vendor linked from official page
            {
                "https://www.town.example.jp/": (
                    "<a href='https://www1.g-reiki.net/example/reiki_menu.html'>例規集</a>"
                )
            },
            # council-scope static PDF minutes
            {
                "https://www.town.example.jp/": "<a href='/gikai/minutes.html'>町議会</a>",
                "https://www.town.example.jp/gikai/minutes.html": (
                    "<title>町議会 会議録</title>"
                    "<a href='/files/r7.pdf'>令和7年第1回定例会会議録</a>"
                )
            },
            # budget/settlement document links and matching title context
            {
                "https://www.town.example.jp/": "<a href='/finance/'>財政</a>",
                "https://www.town.example.jp/finance/": (
                    "<title>予算・決算</title><h1>予算・決算</h1>"
                    "<a href='/files/r8-budget.pdf'>令和8年度予算書</a>"
                )
            },
        ]
        for pages in scenarios:
            client = FakeClient(pages)
            report = preflight_municipality(  # type: ignore[arg-type]
                MUNICIPALITY, client, max_pages=2
            )
            self._assert_no_ready_status(report)

    def _assert_no_ready_status(self, node: object) -> None:
        if isinstance(node, dict):
            self.assertNotEqual("ready", node.get("status"))
            for value in node.values():
                self._assert_no_ready_status(value)
        elif isinstance(node, list):
            for value in node:
                self._assert_no_ready_status(value)

    def test_supported_vendor_minutes_proposes_needs_review_with_evidence(
        self,
    ) -> None:
        pages = {
            "https://www.town.example.jp/": "<a href='/council/'>町議会</a>",
            "https://www.town.example.jp/council/": (
                "<a href='https://ssp.kaigiroku.net/tenant/example/'>会議録検索</a>"
            )
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        minutes = report["sources"]["minutes"]
        self.assertEqual("needs_review", minutes["status"])
        self.assertEqual("kaigiroku_net", minutes["adapter"])
        self.assertEqual(
            "https://ssp.kaigiroku.net/tenant/example/",
            minutes["index_url"],
        )
        self.assertEqual(
            "supported_vendor_linked_from_official_page", minutes["reason"]
        )
        self.assertEqual(1, len(minutes["evidence"]))

    def test_budget_and_settlement_propose_needs_review_keeping_evidence(
        self,
    ) -> None:
        pages = {
            "https://www.town.example.jp/": "<a href='/finance/'>財政</a>",
            "https://www.town.example.jp/finance/": """
                <title>予算・決算</title>
                <h1>予算・決算</h1>
                <a href="/files/r8-budget.pdf">令和8年度予算書</a>
                <a href="/files/r7-settlement.pdf">令和7年度決算書</a>
            """
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        budget = report["sources"]["budget"]
        settlement = report["sources"]["settlement"]
        for source in (budget, settlement):
            self.assertEqual("needs_review", source["status"])
            self.assertEqual("official_document_index", source["adapter"])
            self.assertEqual(
                "https://www.town.example.jp/finance/",
                source["index_url"],
            )
            self.assertEqual(
                "official_page_links_matching_document", source["reason"]
            )
            self.assertEqual(1, len(source["evidence"]))

    def test_matching_title_without_documents_proposes_needs_review(self) -> None:
        pages = {
            "https://www.town.example.jp/": "<a href='/newsletter.html'>予算</a>",
            "https://www.town.example.jp/newsletter.html": (
                "<title>予算書・決算書</title><h1>予算書・決算書</h1>"
            )
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        budget = report["sources"]["budget"]
        self.assertEqual("needs_review", budget["status"])
        self.assertEqual("official_index", budget["adapter"])
        self.assertEqual(
            "https://www.town.example.jp/newsletter.html",
            budget["index_url"],
        )
        self.assertEqual(
            "fetched_official_page_has_matching_title_or_heading",
            budget["reason"],
        )
        self.assertEqual(1, len(budget["evidence"]))

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

    def test_observatory_candidate_is_fetched_after_official_home(self) -> None:
        pages = {
            "https://www.town.example.jp/": """
                <title>架空町</title>
                <a href="/tourism/">観光</a>
            """,
            "https://www.town.example.jp/finance/": """
                <title>予算書・決算書</title>
                <h1>予算書・決算書</h1>
            """,
        }
        hint = {
            "lane": "covered",
            "navigation_mode": "static",
            "source_kinds": ["budget", "settlement"],
            "vendor_signals": [],
            "observed_at": {"home": "2026-07-24T00:00:00Z"},
            "candidate_pages": ["https://www.town.example.jp/finance/"],
            "source_urls": {},
        }
        client = FakeClient(pages)

        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            client,
            max_pages=2,
            observatory_hint=hint,
        )

        self.assertEqual(
            [
                "https://www.town.example.jp/",
                "https://www.town.example.jp/finance/",
            ],
            client.urls,
        )
        self.assertEqual("needs_review", report["sources"]["budget"]["status"])
        self.assertEqual(
            "needs_review", report["sources"]["settlement"]["status"]
        )
        self.assertEqual(
            "bundled_observatory",
            report["pages"][1]["discovered_from"],
        )

    def test_observatory_vendor_never_becomes_ready_without_live_link(self) -> None:
        hint = {
            "lane": "covered",
            "navigation_mode": "static",
            "source_kinds": ["minutes"],
            "vendor_signals": ["voices"],
            "observed_at": {"home": "2026-07-24T00:00:00Z"},
            "candidate_pages": [],
            "source_urls": {
                "minutes": ["https://example.gijiroku.com/voices/"],
            },
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(
                {
                    "https://www.town.example.jp/": (
                        "<title>架空町</title><a href='/tourism/'>観光</a>"
                    )
                }
            ),
            max_pages=1,
            observatory_hint=hint,
        )

        minutes = report["sources"]["minutes"]
        self.assertEqual("human_confirmation_required", minutes["status"])
        self.assertEqual("voices", minutes["adapter"])
        self.assertEqual(
            "observatory_candidate_requires_live_confirmation",
            minutes["reason"],
        )

    def test_observatory_kind_without_url_requests_live_discovery(self) -> None:
        hint = {
            "lane": "covered",
            "navigation_mode": "static",
            "source_kinds": ["regulations"],
            "vendor_signals": [],
            "observed_at": {"home": "2026-07-24T00:00:00Z"},
            "candidate_pages": [],
            "source_urls": {},
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(
                {
                    "https://www.town.example.jp/": (
                        "<title>架空町</title><a href='/tourism/'>観光</a>"
                    )
                }
            ),
            max_pages=1,
            observatory_hint=hint,
        )

        regulations = report["sources"]["regulations"]
        self.assertEqual(
            "human_confirmation_required",
            regulations["status"],
        )
        self.assertEqual(
            "observatory_source_kind_requires_live_discovery",
            regulations["reason"],
        )

    def test_observed_https_on_same_official_host_upgrades_http_root(self) -> None:
        municipality = {
            **MUNICIPALITY,
            "official_home_url": "http://www.town.example.jp/",
        }
        hint = {
            "lane": "covered",
            "navigation_mode": "static",
            "source_kinds": [],
            "vendor_signals": [],
            "observed_at": {"home": "2026-07-24T00:00:00Z"},
            "candidate_pages": ["https://www.town.example.jp/finance/"],
            "source_urls": {},
        }
        client = FakeClient(
            {
                "https://www.town.example.jp/": (
                    "<title>架空町</title><a href='/tourism/'>観光</a>"
                )
            }
        )

        report = preflight_municipality(  # type: ignore[arg-type]
            municipality,
            client,
            max_pages=1,
            observatory_hint=hint,
        )

        self.assertEqual(["https://www.town.example.jp/"], client.urls)
        self.assertEqual(
            "https://www.town.example.jp/",
            report["official_home_fetch_url"],
        )

    def test_non_council_committee_pdf_is_not_ready(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/kiji0036603/index.html">議事録</a>',
            "https://www.town.example.jp/kiji0036603/index.html": """
                <title>令和8年度第1回架空町まちづくり推進審議会議事録</title>
                <a href="doc.pdf">令和8年度第2回まちづくり推進審議会 議事録（PDF）</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY,
            FakeClient(pages),
            max_pages=2,
        )
        minutes = report["sources"]["minutes"]
        self.assertNotEqual("ready", minutes["status"])
        self.assertIsNone(minutes["adapter"])

    def test_education_board_pdf_is_not_ready(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/kiji0033035/index.html">教育委員会</a>',
            "https://www.town.example.jp/kiji0033035/index.html": """
                <title>令和8年6月定例教育委員会（会議録）</title>
                <a href="doc.pdf">令和8年6月定例教育委員会会議録（PDF）</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertNotEqual("ready", report["sources"]["minutes"]["status"])

    def test_agriculture_committee_pdf_with_context_is_not_ready(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/main/13235.html">農業委員会議事録</a>',
            "https://www.town.example.jp/main/13235.html": """
                <title>架空町：農業委員会議事録</title>
                <a href="file.pdf">令和7年4月定例会議事録</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertNotEqual("ready", report["sources"]["minutes"]["status"])

    def test_council_gikai_pdf_proposes_needs_review(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/gikai/minutes.html">町議会</a>',
            "https://www.town.example.jp/gikai/minutes.html": """
                <title>町議会 会議録</title>
                <a href="/files/r7.pdf">令和7年第1回定例会会議録</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertEqual("needs_review", report["sources"]["minutes"]["status"])
        self.assertEqual("static_html_pdf", report["sources"]["minutes"]["adapter"])

    def test_council_url_council_keeps_sitemap_case_as_needs_review(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/council/minutes.html">会議録</a>',
            "https://www.town.example.jp/council/minutes.html": """
                <title>会議録</title>
                <a href="/files/minutes.pdf">令和8年第1回会議録</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertEqual("needs_review", report["sources"]["minutes"]["status"])

    def test_gijiroku_without_voices_path_still_voices(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="https://example.gijiroku.com/other/">会議録</a>',
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=1
        )
        self.assertEqual("unsupported_vendor", report["sources"]["minutes"]["status"])
        self.assertEqual("voices", report["sources"]["minutes"]["adapter"])

    def test_dbsr_vendor_is_unsupported(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="https://sample.dbsr.jp/index.php">会議録</a>',
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=1
        )
        self.assertEqual("unsupported_vendor", report["sources"]["minutes"]["status"])
        self.assertEqual("dbsr", report["sources"]["minutes"]["adapter"])

    def test_d1_law_regulations_is_unsupported(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="https://www.d1-law.com/example/">例規集</a>',
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=1
        )
        self.assertEqual(
            "unsupported_vendor", report["sources"]["regulations"]["status"]
        )
        self.assertEqual("d1_law", report["sources"]["regulations"]["adapter"])

    def test_joureikun_path_is_unsupported(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/joureikun/aggregate/whatsnew/index.html">例規集</a>',
            "https://www.town.example.jp/joureikun/aggregate/whatsnew/index.html": "<title>例規集</title>",
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertEqual(
            "unsupported_vendor", report["sources"]["regulations"]["status"]
        )
        self.assertEqual("joureikun", report["sources"]["regulations"]["adapter"])

    def test_zenin_kyogikai_under_gikai_is_council(self) -> None:
        pages = {
            "https://www.town.example.jp/": '<a href="/gikai/kyogikai.html">全員協議会</a>',
            "https://www.town.example.jp/gikai/kyogikai.html": """
                <title>全員協議会 会議録</title>
                <a href="/files/kyogikai.pdf">令和7年全員協議会議事録</a>
            """,
        }
        report = preflight_municipality(  # type: ignore[arg-type]
            MUNICIPALITY, FakeClient(pages), max_pages=2
        )
        self.assertEqual("needs_review", report["sources"]["minutes"]["status"])
        self.assertEqual("static_html_pdf", report["sources"]["minutes"]["adapter"])


if __name__ == "__main__":
    unittest.main()
