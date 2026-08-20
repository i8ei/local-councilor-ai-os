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
        # After implementation, kaigiroku with non-kaigiroku HTML should fail with structure_mismatch, not "未対応"
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
        self.assertIn("structure_mismatch", report["reason"])
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


# -------------------------------------------------------------------
# kaigiroku_net synthetic tests (no network)
# -------------------------------------------------------------------

KAI_TENANT_URL = "https://ssp.kaigiroku.net/tenant/karatsu/SpTop.html"
KAI_TENANT_SLUG = "karatsu"


def _kaigiroku_entrance_html(tenant: str = KAI_TENANT_SLUG) -> str:
    return f"""
<html><head><title>{tenant} 会議録</title></head>
<body>
<h1>{tenant}市議会 会議録</h1>
<p>kaigiroku</p>
<a href="/tenant/{tenant}/SpTop.html">SpTop</a>
<div>ssp.kaigiroku.net tenant {tenant}</div>
</body></html>
"""


def _base_kaigiroku_needs_review(tenant_url: str = KAI_TENANT_URL) -> dict:
    return {
        "schema_version": 1,
        "area_code_5": VALID_AREA,
        "prefecture": VALID_PREF,
        "municipality": VALID_MUNI,
        "official_home_url": VALID_HOME,
        "sources": {
            "minutes": {
                "status": "needs_review",
                "adapter": "kaigiroku_net",
                "tenant_url": tenant_url,
                "verified_at": None,
                "verified_by": None,
                "evidence": [{"url": tenant_url, "observed_on": VALID_HOME}],
                "notes": None,
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


class KaigirokuNetVerifyTests(unittest.TestCase):
    def test_kaigiroku_promotes_to_ready(self) -> None:
        profile = _base_kaigiroku_needs_review()
        fetch = make_fetch_result(KAI_TENANT_URL, _kaigiroku_entrance_html())
        client = FakeHttpClient({KAI_TENANT_URL: fetch})
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
        self.assertTrue(
            any(
                e.get("url") == KAI_TENANT_URL
                and e.get("observed_on") == KAI_TENANT_URL
                for e in ev
            )
        )
        self.assertEqual([], validate_profile(updated))
        # robots forbidden areas must not be fetched
        fetched = [u for u, _ in client.calls]
        self.assertEqual([KAI_TENANT_URL], fetched)
        self.assertFalse(any("/tenant/js/" in u for u in fetched))
        self.assertFalse(any("/dnp/search/" in u for u in fetched))

    def test_invalid_tenant_url_host_not_kaigiroku(self) -> None:
        orig = _base_kaigiroku_needs_review(
            tenant_url="https://example.com/tenant/karatsu/SpTop.html"
        )
        profile = copy.deepcopy(orig)
        # Even if fetch would succeed, verifier must reject before fetching
        fetch = make_fetch_result(
            "https://example.com/tenant/karatsu/SpTop.html", _kaigiroku_entrance_html()
        )
        client = FakeHttpClient(
            {"https://example.com/tenant/karatsu/SpTop.html": fetch}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_tenant_url", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        self.assertEqual(orig, profile)  # original not mutated
        # gai host profile must remain unchanged (deepcopy returned for invalid)
        self.assertEqual(
            updated["sources"]["minutes"]["tenant_url"],
            orig["sources"]["minutes"]["tenant_url"],
        )
        self.assertEqual(0, len(client.calls))

    def test_invalid_tenant_url_path_not_tenant(self) -> None:
        bad_url = "https://ssp.kaigiroku.net/notenant/karatsu/SpTop.html"
        profile = _base_kaigiroku_needs_review(tenant_url=bad_url)
        fetch = make_fetch_result(bad_url, _kaigiroku_entrance_html())
        client = FakeHttpClient({bad_url: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_tenant_url", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        self.assertEqual(0, len(client.calls))

    def test_robots_denied_does_not_promote(self) -> None:
        profile = _base_kaigiroku_needs_review()

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

    def test_404_does_not_promote(self) -> None:
        profile = _base_kaigiroku_needs_review()

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

    def test_host_drift_does_not_promote(self) -> None:
        profile = _base_kaigiroku_needs_review()
        fetch = make_fetch_result(KAI_TENANT_URL, _kaigiroku_entrance_html())
        fetch = replace(
            fetch, final_url="https://evil.example.com/tenant/karatsu/SpTop.html"
        )
        client = FakeHttpClient({KAI_TENANT_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("host drift", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_structure_mismatch_does_not_promote(self) -> None:
        profile = _base_kaigiroku_needs_review()
        html = "<html><head><title>hello</title></head><body><p>hello world no markers</p></body></html>"
        fetch = make_fetch_result(KAI_TENANT_URL, html)
        client = FakeHttpClient({KAI_TENANT_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("structure_mismatch", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_idempotent(self) -> None:
        profile = _base_kaigiroku_needs_review()
        fetch = make_fetch_result(KAI_TENANT_URL, _kaigiroku_entrance_html())
        client = FakeHttpClient({KAI_TENANT_URL: fetch})
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

    def test_does_not_fetch_robots_forbidden_urls(self) -> None:
        profile = _base_kaigiroku_needs_review()
        fetch = make_fetch_result(KAI_TENANT_URL, _kaigiroku_entrance_html())
        client = FakeHttpClient({KAI_TENANT_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn("https://ssp.kaigiroku.net/tenant/js/app.js", fetched)
        self.assertNotIn("https://ssp.kaigiroku.net/dnp/search/councils/index", fetched)
        self.assertEqual(1, len(fetched))


# -------------------------------------------------------------------
# Follow 1-level synthetic tests (Task C, no network)
# -------------------------------------------------------------------

FOLLOW_INDEX_URL = MINUTES_INDEX_URL
FOLLOW_YEAR_URL = "http://www.town.tara.lg.jp/chosei/_1010/_1414/_7097.html"
FOLLOW_YEAR_URL_2 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/_6753.html"
FOLLOW_YEAR_URL_3 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/_5163.html"
FOLLOW_YEAR_URL_4 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/_4489.html"


def _base_follow_profile(follow_regex: str = "(令和|平成)(元|[0-9]+)年") -> dict:
    p = _base_minutes_static_needs_review()
    cfg = p["sources"]["minutes"].get("config")
    if not isinstance(cfg, dict):
        cfg = {}
        p["sources"]["minutes"]["config"] = cfg
    cfg["follow_link_regex"] = follow_regex
    return p


def _root_html_with_year_links(extra: str = "") -> str:
    # Index has no direct PDF, only year page links
    return f"""
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="/chosei/_1010/_1414/_7097.html">令和7年</a>
<a href="/chosei/_1010/_1414/_6753.html">令和6年</a>
<a href="/chosei/_1010/_1414/_1454.html">決算審査特別委員会会議録</a>
{extra}
</body></html>
"""


def _year_html_with_council_pdf(
    label: str = "令和7年第1回定例会 1日目 会議録.pdf",
) -> str:
    return f"""
<html><head><title>令和7年</title></head>
<body><h1>令和7年</h1><h2>定例会</h2>
<a href="/var/rev0/0021/4208/1265717119.pdf">{label}</a>
</body></html>
"""


class FollowLinkVerifyTests(unittest.TestCase):
    def test_follow_success_root_no_pdf_follow_has_doc(self) -> None:
        profile = _base_follow_profile()
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_pdf())
        client = FakeHttpClient(
            {FOLLOW_INDEX_URL: root_fetch, FOLLOW_YEAR_URL: year_fetch}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        # evidence must contain both root and follow entries with sha256
        ev = updated["sources"]["minutes"]["evidence"]
        self.assertTrue(
            any(e.get("url") == FOLLOW_INDEX_URL and "sha256" in e for e in ev)
        )
        self.assertTrue(
            any(e.get("url") == FOLLOW_YEAR_URL and "sha256" in e for e in ev)
        )
        # follow evidence observed_on is index_url
        self.assertTrue(
            any(
                e.get("url") == FOLLOW_YEAR_URL
                and e.get("observed_on") == FOLLOW_INDEX_URL
                for e in ev
            )
        )
        self.assertEqual([], validate_profile(updated))
        # no guessed URL: only fetched the two observed links
        fetched_urls = [u for u, _ in client.calls]
        self.assertIn(FOLLOW_INDEX_URL, fetched_urls)
        self.assertIn(FOLLOW_YEAR_URL, fetched_urls)

    def test_non_matching_link_not_fetched(self) -> None:
        profile = _base_follow_profile()
        root_html = _root_html_with_year_links(
            extra='<a href="/chosei/_1010/_1414/_9999.html">お知らせ</a>'
        )
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_pdf())
        client = FakeHttpClient(
            {FOLLOW_INDEX_URL: root_fetch, FOLLOW_YEAR_URL: year_fetch}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn(
            "https://www.town.tara.lg.jp/chosei/_1010/_1414/_9999.html", fetched
        )
        # kessan link must also not be fetched (does not match regex)
        self.assertNotIn(
            "https://www.town.tara.lg.jp/chosei/_1010/_1414/_1454.html", fetched
        )

    def test_external_host_link_not_fetched(self) -> None:
        profile = _base_follow_profile()
        root_html = """
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="https://evil.example.com/chosei/_1010/_1414/_7097.html">令和7年</a>
<a href="/chosei/_1010/_1414/_7097.html">令和7年</a>
</body></html>
"""
        # need to ensure evil link would otherwise match but host mismatch prevents fetch
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_pdf())
        client = FakeHttpClient(
            {FOLLOW_INDEX_URL: root_fetch, FOLLOW_YEAR_URL: year_fetch}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn(
            "https://evil.example.com/chosei/_1010/_1414/_7097.html", fetched
        )
        self.assertIn(FOLLOW_YEAR_URL, fetched)

    def test_max_three_pages(self) -> None:
        profile = _base_follow_profile()
        root_html = """
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="/chosei/_1010/_1414/_7097.html">令和7年</a>
<a href="/chosei/_1010/_1414/_6753.html">令和6年</a>
<a href="/chosei/_1010/_1414/_5163.html">令和5年</a>
<a href="/chosei/_1010/_1414/_4489.html">令和4年</a>
</body></html>
"""
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        empty_year = "<html><head><title>令和7年</title></head><body><h1>令和7年</h1><p>no pdf</p></body></html>"
        fetch1 = make_fetch_result(FOLLOW_YEAR_URL, empty_year)
        fetch2 = make_fetch_result(FOLLOW_YEAR_URL_2, empty_year)
        fetch3 = make_fetch_result(FOLLOW_YEAR_URL_3, empty_year)
        fetch4 = make_fetch_result(FOLLOW_YEAR_URL_4, _year_html_with_council_pdf())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: fetch1,
                FOLLOW_YEAR_URL_2: fetch2,
                FOLLOW_YEAR_URL_3: fetch3,
                FOLLOW_YEAR_URL_4: fetch4,
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertIn(FOLLOW_YEAR_URL, fetched)
        self.assertIn(FOLLOW_YEAR_URL_3, fetched)
        self.assertNotIn(FOLLOW_YEAR_URL_4, fetched)
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_depth_one_only(self) -> None:
        profile = _base_follow_profile()
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        nested_url = "http://www.town.tara.lg.jp/chosei/_1010/_1414/nested.html"
        year_html = """
<html><head><title>令和7年</title></head>
<body><h1>令和7年</h1>
<a href="/chosei/_1010/_1414/nested.html">令和7年 詳細</a>
</body></html>
"""
        nested_html = _year_html_with_council_pdf()
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, year_html)
        nested_fetch = make_fetch_result(nested_url, nested_html)
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                nested_url: nested_fetch,
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn(nested_url, fetched)

    def test_invalid_regex_safe_fail(self) -> None:
        profile = _base_follow_profile(follow_regex="[")
        orig = copy.deepcopy(profile)
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid follow_link_regex", report["reason"])
        self.assertEqual(orig, profile)
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        # also schema validation must fail for invalid regex
        errs = validate_profile(profile)
        self.assertTrue(any("follow_link_regex" in e for e in errs))

    def test_follow_candidate_exists_but_no_doc_stays_needs_review(self) -> None:
        profile = _base_follow_profile()
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        empty_html = "<html><head><title>令和7年</title></head><body><h1>令和7年</h1><p>no document</p></body></html>"
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, empty_html)
        second_fetch = make_fetch_result(FOLLOW_YEAR_URL_2, empty_html)
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                FOLLOW_YEAR_URL_2: second_fetch,
            }
        )
        # root has 2 candidates: _7097 and _6753 (kessan excluded). Both empty.
        # Provide second year url link in root? Our root already has _6753 as second.
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("no_council_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_idempotent_follow(self) -> None:
        profile = _base_follow_profile()
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_pdf())
        client = FakeHttpClient(
            {FOLLOW_INDEX_URL: root_fetch, FOLLOW_YEAR_URL: year_fetch}
        )
        updated1, _ = verify_profile(profile, client=client, now=NOW, kind="minutes")
        self.assertEqual("ready", updated1["sources"]["minutes"]["status"])
        ev1_len = len(updated1["sources"]["minutes"]["evidence"])
        updated2, report2 = verify_profile(
            updated1, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report2["result"])
        ev2_len = len(updated2["sources"]["minutes"]["evidence"])
        self.assertEqual(ev1_len, ev2_len)

    def test_percent_decode_match(self) -> None:
        # URL encoded label still matches
        profile = _base_follow_profile(follow_regex="令和7年")
        # href is percent encoded
        root_html = '<html><head><title>会議録</title></head><body><h1>会議録</h1><a href="/chosei/_1010/_1414/%E4%BB%A4%E5%92%8C7%E5%B9%B4.html">令和7年</a></body></html>'
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        encoded_url = "http://www.town.tara.lg.jp/chosei/_1010/_1414/%E4%BB%A4%E5%92%8C7%E5%B9%B4.html"
        # Fake client expects the resolved canonical url (which keeps encoded form)
        year_fetch = make_fetch_result(encoded_url, _year_html_with_council_pdf())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch, encoded_url: year_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        # Should match via decoded URL
        self.assertEqual("verified", report["result"])


# -------------------------------------------------------------------
# dbsr synthetic tests (no network)
# -------------------------------------------------------------------

DBSR_INDEX_URL = "https://www.city.kanzaki.saga.dbsr.jp/index.php/"
DBSR_DETAIL_URL = "https://www.city.kanzaki.saga.dbsr.jp/index.php/1001"


def _dbsr_index_html() -> str:
    return (
        "<html><head><title>神埼市議会 会議録検索</title></head><body>"
        "<h1>神埼市議会 会議録</h1>"
        '<a href="/index.php/1001">令和7年6月定例会 会議録</a>'
        '<a href="/index.php/1002">令和7年9月定例会 会議録</a>'
        "</body></html>"
    )


def _base_dbsr_needs_review(index_url: str = DBSR_INDEX_URL) -> dict:
    return {
        "schema_version": 1,
        "area_code_5": VALID_AREA,
        "prefecture": VALID_PREF,
        "municipality": VALID_MUNI,
        "official_home_url": VALID_HOME,
        "sources": {
            "minutes": {
                "status": "needs_review",
                "adapter": "dbsr",
                "index_url": index_url,
                "verified_at": None,
                "verified_by": None,
                "evidence": [{"url": index_url, "observed_on": VALID_HOME}],
                "notes": None,
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


class DbsrVerifyTests(unittest.TestCase):
    def test_dbsr_promotes_to_ready(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())
        client = FakeHttpClient({DBSR_INDEX_URL: fetch})
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
        self.assertTrue(
            any(
                e.get("url") == DBSR_INDEX_URL
                and e.get("observed_on") == DBSR_INDEX_URL
                for e in ev
            )
        )
        self.assertEqual([], validate_profile(updated))
        self.assertEqual([DBSR_INDEX_URL], [u for u, _ in client.calls])

    def test_dbsr_evidence_idempotent(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())
        client = FakeHttpClient({DBSR_INDEX_URL: fetch})
        once, _ = verify_profile(profile, client=client, now=NOW, kind="minutes")
        twice, _ = verify_profile(once, client=client, now=NOW, kind="minutes")
        self.assertEqual(
            len(once["sources"]["minutes"]["evidence"]),
            len(twice["sources"]["minutes"]["evidence"]),
        )

    def test_dbsr_invalid_index_host(self) -> None:
        bad_url = "https://example.com/index.php/"
        orig = _base_dbsr_needs_review(index_url=bad_url)
        profile = copy.deepcopy(orig)
        client = FakeHttpClient({bad_url: make_fetch_result(bad_url, _dbsr_index_html())})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_index_url", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        self.assertEqual(0, len(client.calls))

    def test_dbsr_invalid_index_path(self) -> None:
        bad_url = "https://www.city.kanzaki.saga.dbsr.jp/other.html"
        profile = _base_dbsr_needs_review(index_url=bad_url)
        client = FakeHttpClient({bad_url: make_fetch_result(bad_url, _dbsr_index_html())})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_index_url", report["reason"])
        self.assertEqual(0, len(client.calls))

    def test_dbsr_structure_mismatch_does_not_promote(self) -> None:
        # dbsr host but no minutes hint / no detail link -> must not promote
        html = "<html><head><title>メンテナンス中</title></head><body>準備中</body></html>"
        profile = _base_dbsr_needs_review()
        client = FakeHttpClient({DBSR_INDEX_URL: make_fetch_result(DBSR_INDEX_URL, html)})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("structure_mismatch", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_dbsr_host_drift_does_not_promote(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())
        fetch = replace(fetch, final_url="https://evil.example.com/index.php/")
        client = FakeHttpClient({DBSR_INDEX_URL: fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("host drift", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_dbsr_robots_denied_does_not_promote(self) -> None:
        profile = _base_dbsr_needs_review()

        class DenyClient:
            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                raise RobotsDeniedError("robots")

        updated, report = verify_profile(
            profile, client=DenyClient(), now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_dbsr_missing_index_url(self) -> None:
        profile = _base_dbsr_needs_review()
        del profile["sources"]["minutes"]["index_url"]
        updated, report = verify_profile(
            profile, client=FakeHttpClient({}), now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_index_url", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])


if __name__ == "__main__":
    unittest.main()
