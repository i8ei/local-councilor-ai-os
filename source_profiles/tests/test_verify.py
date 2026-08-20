"""Tests for verify_profile (synthetic, no network)."""

from __future__ import annotations  # noqa: I001

import copy
import unittest
from dataclasses import replace

from lcaios.http import FetchError, RobotsDeniedError
from lcaios.tests.http_fakes import FakeHttpClient, make_fetch_result
from source_profiles.schema import validate_profile
from source_profiles.verify import verify_profile

VALID_AREA = "41441"
VALID_PREF = "佐賀県"
VALID_MUNI = "太良町"
VALID_HOME = "http://www.town.tara.lg.jp/"
BASE_URL = "https://www1.g-reiki.net/town.tara/"
ENTRY_URL = BASE_URL + "reiki_menu.html"
NOW = "2020-01-01T00:00:00Z"


def _base_greiki_needs_review() -> dict:
    return {
        "schema_version": 1,
        "area_code_5": VALID_AREA,
        "prefecture": VALID_PREF,
        "municipality": VALID_MUNI,
        "official_home_url": VALID_HOME,
        "sources": {
            "minutes": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "regulations": {
                "status": "needs_review",
                "adapter": "g_reiki",
                "base_url": BASE_URL,
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {"url": ENTRY_URL, "observed_on": VALID_HOME},
                ],
                "notes": "synthetic",
            },
            "budget": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "settlement": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
        },
    }


def _base_d1law() -> dict:
    data = _base_greiki_needs_review()
    data["sources"]["regulations"] = {
        "status": "unsupported",
        "adapter": "d1_law",
        "index_url": "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E",
        "verified_at": None,
        "verified_by": None,
        "evidence": [
            {
                "url": "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E",
                "observed_on": "https://www.town.miyaki.lg.jp/sitemap.html",
            }
        ],
        "notes": "d1",
    }
    # area mismatch: keep same area but ok, adapter d1_law doesn't require base_url
    return data


GREIKI_HTML = """
<html><head><title>例規集</title></head>
<body>
<div id=\"primary\">
<a href=\"reiki_honbun/h001.html\">条例</a>
<a href=\"reiki_kana/kana_default.html\">kana</a>
</div>
</body></html>
"""

NON_GREIKI_HTML = """
<html><head><title>Not regulations</title></head>
<body><p>Hello world, no markers here.</p></body></html>
"""


class VerifyTests(unittest.TestCase):
    def test_greiki_needs_review_promotes_to_ready(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        # Ensure final_url is same host (make_fetch_result already does)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        self.assertEqual("needs_review", report["status_before"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["regulations"]["status"])
        self.assertEqual(NOW, updated["sources"]["regulations"]["verified_at"])
        self.assertEqual(
            "verify --live", updated["sources"]["regulations"]["verified_by"]
        )
        # evidence appended with sha256/fetched_at
        ev = updated["sources"]["regulations"]["evidence"]
        self.assertTrue(any(e.get("sha256") == fetch.sha256 for e in ev))
        self.assertTrue(any(e.get("fetched_at") for e in ev))
        # schema passes
        self.assertEqual([], validate_profile(updated))

    def test_robots_denied_does_not_promote(self) -> None:
        profile = _base_greiki_needs_review()

        class DenyClient:
            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                raise RobotsDeniedError("robots.txt により取得できません")

        client = DenyClient()
        updated, report = verify_profile(profile, client=client, now=NOW)  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])
        self.assertIsNone(updated["sources"]["regulations"]["verified_at"])

    def test_host_drift_does_not_promote(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        # Simulate redirect to different host
        drift_url = "https://evil.example.com/reiki_menu.html"
        fetch = replace(fetch, final_url=drift_url)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("failed", report["result"])
        self.assertIn("host drift", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])

    def test_structure_mismatch_does_not_promote(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, NON_GREIKI_HTML)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("failed", report["result"])
        self.assertIn("structure_mismatch", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])

    def test_idempotent_second_verify_no_duplicate(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated1, report1 = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report1["result"])
        # Second verify with same sha
        updated2, report2 = verify_profile(updated1, client=client, now=NOW)
        self.assertEqual("verified", report2["result"])
        ev1 = updated1["sources"]["regulations"]["evidence"]
        ev2 = updated2["sources"]["regulations"]["evidence"]
        # Count entries with same sha
        cnt1 = sum(1 for e in ev1 if e.get("sha256") == fetch.sha256)
        cnt2 = sum(1 for e in ev2 if e.get("sha256") == fetch.sha256)
        self.assertEqual(cnt1, cnt2)
        self.assertEqual(len(ev1), len(ev2))

    def test_d1_law_verify_fails(self) -> None:
        profile = _base_d1law()
        fetch = make_fetch_result(
            "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E",
            GREIKI_HTML,
        )
        client = FakeHttpClient(
            {
                "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E": fetch
            }
        )
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("failed", report["result"])
        self.assertIn("unsupported", report["reason"])
        self.assertEqual("unsupported", updated["sources"]["regulations"]["status"])

    def test_promoted_profile_passes_schema(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        errs = validate_profile(updated)
        self.assertEqual([], errs, msg=str(errs))

    def test_original_not_mutated(self) -> None:
        profile = _base_greiki_needs_review()
        orig = copy.deepcopy(profile)
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, _ = verify_profile(profile, client=client, now=NOW)
        self.assertEqual(orig, profile)
        self.assertNotEqual(updated, profile)


MINUTES_INDEX_URL = "http://www.town.tara.lg.jp/chosei/_1010/_1414.html"


def _base_minutes_static_needs_review() -> dict:
    return {
        "schema_version": 1,
        "area_code_5": VALID_AREA,
        "prefecture": VALID_PREF,
        "municipality": VALID_MUNI,
        "official_home_url": VALID_HOME,
        "sources": {
            "minutes": {
                "status": "needs_review",
                "adapter": "static",
                "index_url": MINUTES_INDEX_URL,
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {"url": MINUTES_INDEX_URL, "observed_on": MINUTES_INDEX_URL}
                ],
                "notes": None,
                "config": {
                    "council_name": "太良町議会",
                    "link_include_regex": "(?i)(会議録|議事録)",
                    "link_exclude_regex": "(?i)(審議会)",
                    "pdf": True,
                },
            },
            "regulations": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "budget": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "settlement": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
        },
    }


def _minutes_council_html_with_pdf() -> str:
    return """
<html><head><title>太良町議会 会議録一覧</title></head>
<body>
<h1>太良町議会 会議録</h1>
<p>本会議</p>
<a href="/chosei/_1010/reiwa7.pdf">令和7年 定例会 会議録 (PDF)</a>
<a href="/chosei/_1010/other.html">令和7年 定例会 会議録 (HTML)</a>
</body></html>
"""


class MinutesStaticVerifyTests(unittest.TestCase):
    def test_minutes_static_promotes_to_ready(self) -> None:
        profile = _base_minutes_static_needs_review()
        fetch = make_fetch_result(MINUTES_INDEX_URL, _minutes_council_html_with_pdf())
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("needs_review", report["status_before"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        self.assertEqual(NOW, updated["sources"]["minutes"]["verified_at"])
        self.assertEqual("verify --live", updated["sources"]["minutes"]["verified_by"])
        ev = updated["sources"]["minutes"]["evidence"]
        self.assertTrue(any(e.get("sha256") == fetch.sha256 for e in ev))
        self.assertEqual([], validate_profile(updated))

    def test_minutes_robots_denied_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()

        class DenyClient:
            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                raise RobotsDeniedError("robots")

        client = DenyClient()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_404_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()

        class NotFoundClient:
            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                raise FetchError("HTTP 404")

        client = NotFoundClient()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("FetchError", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_host_drift_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()
        fetch = make_fetch_result(MINUTES_INDEX_URL, _minutes_council_html_with_pdf())
        fetch = replace(
            fetch, final_url="https://evil.example.com/chosei/_1010/_1414.html"
        )
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("host drift", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_council_scope_missing_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()
        html = '<html><head><title>町ホームページ</title></head><body><h1>お知らせ</h1><a href="/doc.pdf">資料</a></body></html>'
        fetch = make_fetch_result(MINUTES_INDEX_URL, html)
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_generic_title_with_council_pdf_promotes_to_ready(self) -> None:
        # New rule core: generic title "会議録" still ready if council pdf link exists
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="/gikai/2024/reiwa6-dai1-teireikai.pdf">令和6年第1回定例会会議録.pdf</a>
</body></html>
"""
        fetch = make_fetch_result(MINUTES_INDEX_URL, html)
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])

    def test_agri_committee_pdf_only_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="/docs/nougyou.pdf">農業委員会議事録.pdf</a>
</body></html>
"""
        fetch = make_fetch_result(MINUTES_INDEX_URL, html)
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_footer_council_nav_only_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>太良町ホーム</title></head>
<body><h1>太良町</h1>
<p>コンテンツ</p>
<footer><a href="/gikai/">町議会</a></footer>
</body></html>
"""
        fetch = make_fetch_result(MINUTES_INDEX_URL, html)
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_no_council_document_link_does_not_promote(self) -> None:
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>太良町議会 会議録</title></head>
<body><h1>太良町議会</h1><p>本会議</p><a href="/kiji0036603.pdf">まちづくり推進審議会 議事録</a></body></html>
"""
        fetch = make_fetch_result(MINUTES_INDEX_URL, html)
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_kaigiroku_unsupported(self) -> None:
        profile = _base_minutes_static_needs_review()
        profile["sources"]["minutes"]["adapter"] = "kaigiroku_net"  # type: ignore[index]
        profile["sources"]["minutes"].pop("index_url", None)  # type: ignore[attr-defined]
        profile["sources"]["minutes"]["tenant_url"] = (
            "https://ssp.kaigiroku.net/tenant/tara/SpTop.html"  # type: ignore[index]
        )
        fetch = make_fetch_result(
            "https://ssp.kaigiroku.net/tenant/tara/SpTop.html",
            _minutes_council_html_with_pdf(),
        )
        client = FakeHttpClient(
            {"https://ssp.kaigiroku.net/tenant/tara/SpTop.html": fetch}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("未対応", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_minutes_idempotent(self) -> None:
        profile = _base_minutes_static_needs_review()
        fetch = make_fetch_result(MINUTES_INDEX_URL, _minutes_council_html_with_pdf())
        client = FakeHttpClient({MINUTES_INDEX_URL: fetch})
        updated1, report1 = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report1["result"])
        updated2, report2 = verify_profile(
            updated1, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report2["result"])
        ev1 = updated1["sources"]["minutes"]["evidence"]
        ev2 = updated2["sources"]["minutes"]["evidence"]
        cnt1 = sum(1 for e in ev1 if e.get("sha256") == fetch.sha256)
        cnt2 = sum(1 for e in ev2 if e.get("sha256") == fetch.sha256)
        self.assertEqual(cnt1, cnt2)
        self.assertEqual(len(ev1), len(ev2))


if __name__ == "__main__":
    unittest.main()
