"""Tests for verify_profile (synthetic, no network)."""

from __future__ import annotations  # noqa: I001

import copy
import json
import subprocess
import unittest
from dataclasses import replace
from unittest import mock
from urllib.parse import urlencode

from lcaios.http import CacheTier, FetchError, RobotsDeniedError
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

KANA_INDEX_URL = BASE_URL + "reiki_kana/kana_default.html"
GREIKI_DOC_URL = BASE_URL + "reiki_honbun/h001.html"

# Fifty-sound index page linking one regulation document (real discovery path
# of modules.regulations.vendor_greiki.discover_documents).
KANA_INDEX_HTML = """
<html><head><title>例規集 五十音順</title></head>
<body>
<a href="../reiki_honbun/h001.html">例規集条例</a>
</body></html>
"""

# One regulation body extractable by vendor_greiki (div#primary + 第N条).
GREIKI_DOC_HTML = """
<html><head><title>太良町例規集条例</title></head>
<body><div id="primary">
<h2>第一条</h2><p>この条例は町の例規について定める。</p>
</div></body></html>
"""


def _greiki_client(**kwargs: object) -> FakeHttpClient:
    responses: dict[str, object] = {
        ENTRY_URL: make_fetch_result(ENTRY_URL, GREIKI_HTML),
        KANA_INDEX_URL: make_fetch_result(KANA_INDEX_URL, KANA_INDEX_HTML),
        GREIKI_DOC_URL: make_fetch_result(GREIKI_DOC_URL, GREIKI_DOC_HTML),
    }
    responses.update(kwargs)  # type: ignore[arg-type]
    return FakeHttpClient(responses)  # type: ignore[arg-type]


class VerifyTests(unittest.TestCase):
    def test_greiki_needs_review_promotes_to_ready(self) -> None:
        profile = _base_greiki_needs_review()
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        client = _greiki_client()
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
        # Simulate redirect to completely unrelated host
        drift_url = "https://evil.example.com/reiki_menu.html"
        fetch = replace(fetch, final_url=drift_url)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("failed", report["result"])
        self.assertIn("host drift", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])

    def test_allowed_host_drift_www_normalization(self) -> None:
        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["base_url"] = "http://town.tara.lg.jp/reiki/"
        raw_entry = "http://town.tara.lg.jp/reiki/reiki_menu.html"
        www_entry = "https://www.town.tara.lg.jp/reiki/reiki_menu.html"
        www_kana = "https://www.town.tara.lg.jp/reiki/reiki_kana/kana_default.html"
        www_doc = "https://www.town.tara.lg.jp/reiki/reiki_honbun/h001.html"
        fetch_entry = replace(make_fetch_result(raw_entry, GREIKI_HTML), final_url=www_entry)
        client = FakeHttpClient({
            raw_entry: fetch_entry,
            www_entry: make_fetch_result(www_entry, GREIKI_HTML),
            www_kana: make_fetch_result(www_kana, KANA_INDEX_HTML),
            www_doc: make_fetch_result(www_doc, GREIKI_DOC_HTML),
        })
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["regulations"]["status"])
        self.assertEqual("https://www.town.tara.lg.jp/reiki/", updated["sources"]["regulations"]["base_url"])

    def test_allowed_host_drift_vendor_redirection(self) -> None:
        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["base_url"] = "http://www.town.tara.lg.jp/reiki/"
        raw_entry = "http://www.town.tara.lg.jp/reiki/reiki_menu.html"
        vendor_entry = "https://www1.g-reiki.net/town.tara/reiki_menu.html"
        vendor_kana = "https://www1.g-reiki.net/town.tara/reiki_kana/kana_default.html"
        vendor_doc = "https://www1.g-reiki.net/town.tara/reiki_honbun/h001.html"
        fetch_entry = replace(make_fetch_result(raw_entry, GREIKI_HTML), final_url=vendor_entry)
        client = FakeHttpClient({
            raw_entry: fetch_entry,
            vendor_entry: make_fetch_result(vendor_entry, GREIKI_HTML),
            vendor_kana: make_fetch_result(vendor_kana, KANA_INDEX_HTML),
            vendor_doc: make_fetch_result(vendor_doc, GREIKI_DOC_HTML),
        })
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["regulations"]["status"])
        self.assertEqual("https://www1.g-reiki.net/town.tara/", updated["sources"]["regulations"]["base_url"])

    def test_host_with_explicit_port_is_not_host_drift(self) -> None:
        profile = _base_greiki_needs_review()
        # Simulate final_url with explicit port 443 on the same host
        port_entry = "https://www1.g-reiki.net:443/town.tara/reiki_menu.html"
        port_kana = "https://www1.g-reiki.net:443/town.tara/reiki_kana/kana_default.html"
        port_doc = "https://www1.g-reiki.net:443/town.tara/reiki_honbun/h001.html"
        fetch_entry = replace(make_fetch_result(ENTRY_URL, GREIKI_HTML), final_url=port_entry)
        client = FakeHttpClient({
            ENTRY_URL: fetch_entry,
            port_kana: make_fetch_result(port_kana, KANA_INDEX_HTML),
            port_doc: make_fetch_result(port_doc, GREIKI_DOC_HTML),
        })
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["regulations"]["status"])

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
        client = _greiki_client()
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

    def test_unknown_adapter_verify_fails(self) -> None:
        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["adapter"] = "unknown_vendor"
        fetch = make_fetch_result(ENTRY_URL, GREIKI_HTML)
        client = FakeHttpClient({ENTRY_URL: fetch})
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("failed", report["result"])
        self.assertIn("unsupported", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])

    def test_promoted_profile_passes_schema(self) -> None:
        profile = _base_greiki_needs_review()
        client = _greiki_client()
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        errs = validate_profile(updated)
        self.assertEqual([], errs, msg=str(errs))

    def test_original_not_mutated(self) -> None:
        profile = _base_greiki_needs_review()
        orig = copy.deepcopy(profile)
        client = _greiki_client()
        updated, _ = verify_profile(profile, client=client, now=NOW)
        self.assertEqual(orig, profile)
        self.assertNotEqual(updated, profile)

    def test_not_evaluated_promotes_to_ready(self) -> None:
        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["status"] = "not_evaluated"  # type: ignore[index]
        updated, report = verify_profile(
            profile, client=_greiki_client(), now=NOW
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("not_evaluated", report["status_before"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["regulations"]["status"])

    def test_blocked_entry_passing_all_checks_stays_blocked(self) -> None:
        # R1: a human-recorded `blocked` judgement must survive verify --live.
        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["status"] = "blocked"  # type: ignore[index]
        updated, report = verify_profile(
            profile, client=_greiki_client(), now=NOW
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("blocked", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertIn("withheld", report["reason"])
        self.assertEqual("blocked", updated["sources"]["regulations"]["status"])

    def test_unsupported_and_not_found_passing_checks_stay_unchanged(self) -> None:
        for prior in ("unsupported", "not_found"):
            with self.subTest(prior=prior):
                profile = _base_minutes_static_needs_review()
                profile["sources"]["minutes"]["status"] = prior  # type: ignore[index]
                client = FakeHttpClient(
                    {
                        MINUTES_INDEX_URL: make_fetch_result(
                            MINUTES_INDEX_URL, _minutes_council_html_with_pdf()
                        ),
                        MINUTES_DOC_URL: make_fetch_result(
                            MINUTES_DOC_URL, MINUTES_DOC_HTML
                        ),
                    }
                )
                updated, report = verify_profile(
                    profile, client=client, now=NOW, kind="minutes"
                )
                self.assertEqual("verified", report["result"])
                self.assertEqual(prior, report["status_before"])
                self.assertEqual(prior, report["status_after"])
                self.assertIn("withheld", report["reason"])
                self.assertEqual(
                    prior, updated["sources"]["minutes"]["status"]
                )

    def test_probe_robots_denied_sets_blocked(self) -> None:
        # R3: robots denial on the extraction probe -> blocked.
        def make_deny_doc_client() -> FakeHttpClient:
            class DenyDocClient(FakeHttpClient):
                def __init__(self) -> None:
                    super().__init__(
                        {
                            ENTRY_URL: make_fetch_result(ENTRY_URL, GREIKI_HTML),
                            KANA_INDEX_URL: make_fetch_result(
                                KANA_INDEX_URL, KANA_INDEX_HTML
                            ),
                        }
                    )

                def fetch(
                    self, url: str, *, tier: CacheTier, **_: object
                ) -> object:
                    self.calls.append((url, tier))
                    if url == GREIKI_DOC_URL:
                        raise RobotsDeniedError("robots.txt disallows reiki_honbun")
                    return self.responses[url]

            return DenyDocClient()

        profile = _base_greiki_needs_review()
        updated, report = verify_profile(
            profile, client=make_deny_doc_client(), now=NOW
        )  # type: ignore[arg-type]
        self.assertEqual("blocked", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual("needs_review", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["regulations"]["status"])
        notes = updated["sources"]["regulations"].get("notes") or ""
        self.assertIn("robots", notes.lower())
        self.assertEqual([], validate_profile(updated))

    def test_probe_robots_denied_downgrades_ready_to_blocked(self) -> None:
        def make_deny_doc_client() -> FakeHttpClient:
            class DenyDocClient(FakeHttpClient):
                def __init__(self) -> None:
                    super().__init__(
                        {
                            ENTRY_URL: make_fetch_result(ENTRY_URL, GREIKI_HTML),
                            KANA_INDEX_URL: make_fetch_result(
                                KANA_INDEX_URL, KANA_INDEX_HTML
                            ),
                        }
                    )

                def fetch(
                    self, url: str, *, tier: CacheTier, **_: object
                ) -> object:
                    self.calls.append((url, tier))
                    if url == GREIKI_DOC_URL:
                        raise RobotsDeniedError("robots.txt disallows reiki_honbun")
                    return self.responses[url]

            return DenyDocClient()

        profile = _base_greiki_needs_review()
        profile["sources"]["regulations"]["status"] = "ready"  # type: ignore[index]

        updated, report = verify_profile(
            profile, client=make_deny_doc_client(), now=NOW
        )
        self.assertEqual("blocked", report["result"])
        self.assertEqual("ready", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["regulations"]["status"])

    def test_probe_unreachable_leaves_status_unchanged(self) -> None:
        # R5: 404/network error on the probe document -> failed, status kept.
        for prior in ("needs_review", "not_evaluated", "ready"):
            with self.subTest(prior=prior):
                profile = _base_greiki_needs_review()
                profile["sources"]["regulations"]["status"] = prior  # type: ignore[index]

                class ErrorDocClient(FakeHttpClient):
                    def __init__(self) -> None:
                        super().__init__(
                            {
                                ENTRY_URL: make_fetch_result(
                                    ENTRY_URL, GREIKI_HTML
                                ),
                                KANA_INDEX_URL: make_fetch_result(
                                    KANA_INDEX_URL, KANA_INDEX_HTML
                                ),
                            }
                        )

                    def fetch(
                        self, url: str, *, tier: CacheTier, **_: object
                    ) -> object:
                        self.calls.append((url, tier))
                        if url == GREIKI_DOC_URL:
                            raise FetchError("HTTP 404")
                        return self.responses[url]

                updated, report = verify_profile(
                    profile, client=ErrorDocClient(), now=NOW
                )  # type: ignore[arg-type]
                self.assertEqual("failed", report["result"])
                self.assertIn("FetchError", report["reason"])
                self.assertEqual(prior, report["status_after"])
                self.assertEqual(
                    prior, updated["sources"]["regulations"]["status"]
                )

    def test_probe_fetches_exactly_one_document(self) -> None:        # R2: the probe fetches exactly ONE document (DOCUMENT tier);
        # index navigation stays on INDEX tier. (The menu is fetched twice:
        # once for verify's own structure check, once by discover_documents.)
        profile = _base_greiki_needs_review()
        client = _greiki_client()
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("verified", report["result"])
        doc_calls = [u for u, t in client.calls if t == CacheTier.DOCUMENT]
        self.assertEqual([GREIKI_DOC_URL], doc_calls)
        self.assertEqual(4, len(client.calls))

    def test_regulations_without_numbered_articles_stays_needs_review(self) -> None:
        # C4 regression guard (regulations side): a g-reiki page whose body
        # is plain prose yields only fallback articles with article_no=None;
        # that is not structural evidence of a regulation. Never ready.
        profile = _base_greiki_needs_review()
        no_article_doc = (
            '<html><head><title>お知らせ</title></head>'
            '<body><div id="primary">'
            "<p>お知らせ</p><p>今月の行事予定をお伝えします。</p>"
            "</div></body></html>"
        )
        client = FakeHttpClient(
            {
                ENTRY_URL: make_fetch_result(ENTRY_URL, GREIKI_HTML),
                KANA_INDEX_URL: make_fetch_result(KANA_INDEX_URL, KANA_INDEX_HTML),
                GREIKI_DOC_URL: make_fetch_result(
                    GREIKI_DOC_URL, no_article_doc
                ),
            }
        )
        updated, report = verify_profile(profile, client=client, now=NOW)
        self.assertEqual("needs_review", report["result"])
        self.assertIn("probe_found_no_identifiable_records", report["reason"])
        self.assertIn("numbered articles", report["reason"])
        self.assertEqual("needs_review", report["status_after"])
        self.assertEqual("needs_review", updated["sources"]["regulations"]["status"])
        notes = updated["sources"]["regulations"].get("notes") or ""
        self.assertIn("no numbered articles", notes)


MINUTES_INDEX_URL = "http://www.town.tara.lg.jp/chosei/_1010/_1414.html"
MINUTES_DOC_URL = "http://www.town.tara.lg.jp/chosei/_1010/other.html"

# HTML minutes body extractable by StaticHtmlAdapter.segment_speeches.
MINUTES_DOC_HTML = """
<html><head><title>令和7年 定例会 会議録</title></head>
<body>
<p>○ 議長 開会を宣告します。</p>
<p>○ 太良太郎 一般質問を行います。</p>
</body></html>
"""


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
                    "pdf": False,
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


def _static_minutes_client() -> FakeHttpClient:
    # Index page + the extractable HTML minutes document the probe fetches.
    return FakeHttpClient(
        {
            MINUTES_INDEX_URL: make_fetch_result(
                MINUTES_INDEX_URL, _minutes_council_html_with_pdf()
            ),
            MINUTES_DOC_URL: make_fetch_result(MINUTES_DOC_URL, MINUTES_DOC_HTML),
        }
    )


class MinutesStaticVerifyTests(unittest.TestCase):
    def test_minutes_static_promotes_to_ready(self) -> None:
        profile = _base_minutes_static_needs_review()
        fetch = make_fetch_result(MINUTES_INDEX_URL, _minutes_council_html_with_pdf())
        client = _static_minutes_client()
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

    def test_minutes_human_downgrade_is_not_promoted_back(self) -> None:
        # src-06 regression: the 2026-08-20 hand downgrades (有田=needs_review,
        # 白石=blocked) must survive verify --live even when the entry fetches
        # and probes cleanly. Prior-ready-only promotion is the gate; 会議録系
        # minutes では blocked/unsupported/not_found は据え置き。
        for prior in ("blocked", "unsupported", "not_found"):
            with self.subTest(prior=prior):
                profile = _base_minutes_static_needs_review()
                profile["sources"]["minutes"]["status"] = prior  # type: ignore[index]
                client = _static_minutes_client()
                updated, report = verify_profile(
                    profile, client=client, now=NOW, kind="minutes"
                )
                self.assertEqual("verified", report["result"])
                self.assertEqual(prior, report["status_before"])
                self.assertEqual(prior, report["status_after"])
                self.assertIn("withheld", report["reason"])
                self.assertEqual(
                    prior, updated["sources"]["minutes"]["status"]
                )

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

    def test_vendor_host_index_is_not_host_drift(self) -> None:
        # src-04 regression: the drift reference is the fetched entry URL
        # (index_url), not official_home_url. A legitimate vendor-CMS index
        # on a different host from the municipal home must NOT fail the
        # drift check when it serves its own pages.
        vendor_index = "https://cms.example.net/tara/gikai.html"
        vendor_doc = "https://cms.example.net/tara/meeting.pdf"
        html = (
            '<html><head><title>太良町議会 会議録</title></head><body>'
            "<h1>太良町議会 会議録</h1>"
            f'<a href="{vendor_doc}">令和7年 定例会 会議録 (PDF)</a>'
            "</body></html>"
        )
        profile = _base_minutes_static_needs_review()
        profile["sources"]["minutes"]["index_url"] = vendor_index  # type: ignore[index]
        profile["sources"]["minutes"]["config"]["pdf"] = True  # type: ignore[index]
        client = FakeHttpClient(
            {
                vendor_index: make_fetch_result(vendor_index, html),
                vendor_doc: make_fetch_result(
                    vendor_doc,
                    "%PDF-1.4 synthetic",
                    content_type="application/pdf",
                ),
            }
        )
        with mock.patch(
            "modules.minutes_db.adapters.static_html.shutil.which",
            return_value="/usr/bin/pdftotext",
        ), mock.patch(
            "modules.minutes_db.adapters.static_html.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="○議長　開会します。○太良太郎　質問します。".encode(),
                stderr=b"",
            ),
        ):
            updated, report = verify_profile(
                profile, client=client, now=NOW, kind="minutes"
            )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", report["status_after"])

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
        # Generic title "会議録" still ready if a council document exists AND
        # the extraction probe yields >=1 speech record.
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="/gikai/2024/reiwa6-dai1-teireikai.pdf">令和6年第1回定例会会議録.pdf</a>
<a href="/gikai/2024/reiwa6-dai1-teireikai.html">令和6年第1回定例会会議録</a>
</body></html>
"""
        doc_url = "http://www.town.tara.lg.jp/gikai/2024/reiwa6-dai1-teireikai.html"
        client = FakeHttpClient(
            {
                MINUTES_INDEX_URL: make_fetch_result(MINUTES_INDEX_URL, html),
                doc_url: make_fetch_result(doc_url, MINUTES_DOC_HTML),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])

    def test_gikaidayori_newsletter_prose_stays_needs_review(self) -> None:
        # 議会だより / 有田 regression guard (C4): an index link labelled
        # 会議録 pointing at a newsletter with plain prose but NO speakers
        # is not verbatim minutes. Fallback paragraph chunks carry no
        # speaker, so this must never be promoted to ready.
        profile = _base_minutes_static_needs_review()
        html = """
<html><head><title>太良町議会 会議録</title></head>
<body><h1>太良町議会</h1>
<a href="/gikai/gikaidayori.html">令和7年 会議録</a>
</body></html>
"""
        doc_url = "http://www.town.tara.lg.jp/gikai/gikaidayori.html"
        newsletter_doc = (
            '<html><head><title>議会だより 第120号</title></head><body>'
            "<p>今号の主な内容をお伝えします。</p>"
            "<p>先月の町内イベントの様子をご紹介します。</p>"
            "<p>来月の予定は添付資料をご覧ください。</p>"
            "</body></html>"
        )
        client = FakeHttpClient(
            {
                MINUTES_INDEX_URL: make_fetch_result(MINUTES_INDEX_URL, html),
                doc_url: make_fetch_result(doc_url, newsletter_doc),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("needs_review", report["result"])
        self.assertIn("probe_found_no_identifiable_records", report["reason"])
        self.assertIn("speaker-attributed speeches", report["reason"])
        self.assertEqual("needs_review", report["status_after"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        notes = updated["sources"]["minutes"].get("notes") or ""
        self.assertIn("no speaker-attributed speeches", notes)
        self.assertNotEqual("ready", updated["sources"]["minutes"]["status"])

    def test_static_probe_robots_denied_downgrades_ready_to_blocked(self) -> None:
        profile = _base_minutes_static_needs_review()
        profile["sources"]["minutes"]["status"] = "ready"  # type: ignore[index]

        class DenyStaticDocClient(FakeHttpClient):
            def __init__(self) -> None:
                super().__init__(
                    {
                        MINUTES_INDEX_URL: make_fetch_result(
                            MINUTES_INDEX_URL, _minutes_council_html_with_pdf()
                        ),
                    }
                )

            def fetch(self, url: str, *, tier: CacheTier, **_: object) -> object:
                self.calls.append((url, tier))
                if url == MINUTES_DOC_URL:
                    raise RobotsDeniedError("robots.txt disallows /chosei/")
                return self.responses[url]

        updated, report = verify_profile(
            profile, client=DenyStaticDocClient(), now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("blocked", report["result"])
        self.assertEqual("ready", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["minutes"]["status"])
        self.assertIn(
            "robots", (updated["sources"]["minutes"].get("notes") or "").lower()
        )
        self.assertEqual([], validate_profile(updated))

    def test_static_probe_unreachable_leaves_status_unchanged(self) -> None:
        prior_status = "not_evaluated"
        profile = _base_minutes_static_needs_review()
        profile["sources"]["minutes"]["status"] = prior_status  # type: ignore[index]

        class ErrorStaticDocClient(FakeHttpClient):
            def __init__(self) -> None:
                super().__init__(
                    {
                        MINUTES_INDEX_URL: make_fetch_result(
                            MINUTES_INDEX_URL, _minutes_council_html_with_pdf()
                        ),
                    }
                )

            def fetch(self, url: str, *, tier: CacheTier, **_: object) -> object:
                self.calls.append((url, tier))
                if url == MINUTES_DOC_URL:
                    raise FetchError("HTTP 404")
                return self.responses[url]

        updated, report = verify_profile(
            profile, client=ErrorStaticDocClient(), now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("FetchError", report["reason"])
        self.assertEqual(prior_status, report["status_after"])
        self.assertEqual(prior_status, updated["sources"]["minutes"]["status"])

    def test_static_probe_fetches_exactly_one_document(self) -> None:
        profile = _base_minutes_static_needs_review()
        client = _static_minutes_client()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        doc_calls = [u for u, t in client.calls if t == CacheTier.DOCUMENT]
        self.assertEqual([MINUTES_DOC_URL], doc_calls)
        self.assertEqual(2, len(client.calls))

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
        client = _static_minutes_client()
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


class StaticPdfProbeTests(unittest.TestCase):
    """PDF probe contrast: pdftotext present vs absent (C1-C3).

    Same municipality, same PDF document; only the local tooling differs.
    A missing pdftotext must be INCONCLUSIVE (status untouched, result
    failed), never a "yielded no records" demotion.
    """

    PDF_DOC_URL = "http://www.town.tara.lg.jp/chosei/_1010/reiwa7.pdf"

    def _pdf_profile(self, prior_status: str) -> dict:
        profile = _base_minutes_static_needs_review()
        profile["sources"]["minutes"]["status"] = prior_status  # type: ignore[index]
        profile["sources"]["minutes"]["config"]["pdf"] = True  # type: ignore[index]
        return profile

    def _pdf_client(self) -> FakeHttpClient:
        html = (
            '<html><head><title>太良町議会 会議録</title></head><body>'
            "<h1>太良町議会 会議録</h1>"
            f'<a href="{self.PDF_DOC_URL}">令和7年 定例会 会議録 (PDF)</a>'
            "</body></html>"
        )
        return FakeHttpClient(
            {
                MINUTES_INDEX_URL: make_fetch_result(MINUTES_INDEX_URL, html),
                self.PDF_DOC_URL: make_fetch_result(
                    self.PDF_DOC_URL,
                    "%PDF-1.4 synthetic minutes pdf",
                    content_type="application/pdf",
                ),
            }
        )

    def _patch_pdftotext(self, available: bool) -> tuple[mock._patch, ...]:  # type: ignore[name-defined]
        which_target = "modules.minutes_db.adapters.static_html.shutil.which"
        run_target = "modules.minutes_db.adapters.static_html.subprocess.run"
        if not available:
            return (mock.patch(which_target, return_value=None),)
        text = "○ 議長 開会を宣告します。\n○ 太良太郎 一般質問を行います。\n"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=text.encode("utf-8"), stderr=b""
        )
        return (
            mock.patch(which_target, return_value="/usr/bin/pdftotext"),
            mock.patch(run_target, return_value=completed),
        )

    def test_pdf_probe_without_pdftotext_is_inconclusive_not_demoted(self) -> None:
        # C2/C3: the adapter could not read the document, so the probe says
        # nothing about the source. Status must stay EXACTLY as it was and
        # the reason must name the adapter status.
        for prior in ("needs_review", "ready", "not_evaluated"):
            with self.subTest(prior=prior):
                profile = self._pdf_profile(prior)
                (which_patch,) = self._patch_pdftotext(available=False)
                with which_patch:
                    updated, report = verify_profile(
                        profile,
                        client=self._pdf_client(),
                        now=NOW,
                        kind="minutes",
                    )
                self.assertEqual("failed", report["result"])
                self.assertIn("probe_inconclusive", report["reason"])
                self.assertIn("pdf_cached_pdftotext_unavailable", report["reason"])
                self.assertEqual(prior, report["status_after"])
                self.assertEqual(prior, updated["sources"]["minutes"]["status"])
                notes = updated["sources"]["minutes"].get("notes") or ""
                self.assertNotIn("yielded no records", notes)

    def test_pdf_probe_with_pdftotext_promotes_to_ready(self) -> None:
        # A PDF document that extracts successfully promotes to ready.
        profile = self._pdf_profile("needs_review")
        which_patch, run_patch = self._patch_pdftotext(available=True)
        with which_patch, run_patch:
            updated, report = verify_profile(
                profile, client=self._pdf_client(), now=NOW, kind="minutes"
            )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])


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


KAI_CALLBACK = "lcaoMinutesCallback"
KAI_API_ROOT = "https://ssp.kaigiroku.net/dnp/search/"


def _kaigiroku_api_url(endpoint: str, **params: str) -> str:
    # Same request shape as KaigirokuNetAdapter._api.
    query = urlencode(
        {"tenant_id": KAI_TENANT_SLUG, **params, "callback": KAI_CALLBACK}
    )
    return f"{KAI_API_ROOT}{endpoint}?{query}"


KAI_COUNCILS_URL = _kaigiroku_api_url("councils/index")
KAI_YEARS_URL = _kaigiroku_api_url("councils/get_view_years", council_id="c1")
KAI_INDEX_API_URL = _kaigiroku_api_url(
    "minutes/get_index", council_id="c1", year="2026"
)
KAI_MINUTE_URL = _kaigiroku_api_url(
    "minutes/get_minute", council_id="c1", schedule_id="s1", minute_id="m1"
)


def _jsonp(payload: object) -> str:
    return f"{KAI_CALLBACK}({json.dumps(payload, ensure_ascii=False)});"


def _kaigiroku_client() -> FakeHttpClient:
    """Tenant page + the real JSONP API chain used by KaigirokuNetAdapter."""
    return FakeHttpClient(
        {
            KAI_TENANT_URL: make_fetch_result(
                KAI_TENANT_URL, _kaigiroku_entrance_html()
            ),
            KAI_COUNCILS_URL: make_fetch_result(
                KAI_COUNCILS_URL,
                _jsonp([{"council_id": "c1", "council_name": "唐津市議会"}]),
            ),
            KAI_YEARS_URL: make_fetch_result(
                KAI_YEARS_URL, _jsonp([{"view_year": "2026"}])
            ),
            KAI_INDEX_API_URL: make_fetch_result(
                KAI_INDEX_API_URL,
                _jsonp(
                    [
                        {
                            "council_id": "c1",
                            "schedule_id": "s1",
                            "minute_id": "m1",
                            "meeting_name": "令和8年3月定例会",
                            "date": "2026-03-01",
                        }
                    ]
                ),
            ),
            KAI_MINUTE_URL: make_fetch_result(
                KAI_MINUTE_URL,
                _jsonp(
                    [
                        {
                            "speech_no": 1,
                            "speaker_name": "議長",
                            "speech_text": "開会を宣告します。",
                        },
                        {
                            "speech_no": 2,
                            "speaker_name": "市長",
                            "speech_text": "提案理由を説明します。",
                        },
                    ]
                ),
            ),
        }
    )


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
        client = _kaigiroku_client()
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
        # The extraction probe goes through the real adapter's JSONP API
        # chain (that is where the speeches come from); the shared JS asset
        # linked from the page is never fetched.
        fetched = [u for u, _ in client.calls]
        self.assertFalse(any("/tenant/js/" in u for u in fetched))
        self.assertIn(KAI_MINUTE_URL, fetched)

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
        client = _kaigiroku_client()
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

    def test_probe_goes_through_real_api_exactly_one_document(self) -> None:
        # R2: the kaigiroku probe lists one meeting via the real adapter and
        # extracts its speeches; the speeches endpoint (the document body) is
        # fetched exactly once. The shared JS asset linked from the page is
        # never fetched.
        profile = _base_kaigiroku_needs_review()
        client = _kaigiroku_client()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn("https://ssp.kaigiroku.net/tenant/js/app.js", fetched)
        minute_calls = [u for u in fetched if u == KAI_MINUTE_URL]
        self.assertEqual([KAI_MINUTE_URL], minute_calls)

    def _api_robots_denied_client(self) -> FakeHttpClient:
        """Tenant index fetches fine; every /dnp/search/ API call is robots-denied.

        This is the real-world shape of kaigiroku.net: robots.txt allows the
        tenant index and forbids the /dnp/search/ API paths.
        """

        class KaigirokuApiRobotsClient(FakeHttpClient):
            def __init__(self) -> None:
                super().__init__(
                    {
                        KAI_TENANT_URL: make_fetch_result(
                            KAI_TENANT_URL, _kaigiroku_entrance_html()
                        )
                    }
                )

            def fetch(self, url: str, *, tier: CacheTier, **_: object) -> object:
                self.calls.append((url, tier))
                if url.startswith(KAI_API_ROOT):
                    raise RobotsDeniedError("robots.txt disallows /dnp/search/")
                return self.responses[url]

        return KaigirokuApiRobotsClient()

    def test_kaigiroku_api_robots_denied_sets_blocked(self) -> None:
        profile = _base_kaigiroku_needs_review()
        updated, report = verify_profile(
            profile,
            client=self._api_robots_denied_client(),
            now=NOW,
            kind="minutes",
        )  # type: ignore[arg-type]
        self.assertEqual("blocked", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual("needs_review", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["minutes"]["status"])
        notes = updated["sources"]["minutes"].get("notes") or ""
        self.assertIn("robots", notes.lower())
        self.assertEqual([], validate_profile(updated))

    def test_kaigiroku_api_robots_denied_downgrades_ready_to_blocked(self) -> None:
        profile = _base_kaigiroku_needs_review()
        profile["sources"]["minutes"]["status"] = "ready"  # type: ignore[index]
        updated, report = verify_profile(
            profile,
            client=self._api_robots_denied_client(),
            now=NOW,
            kind="minutes",
        )
        self.assertEqual("blocked", report["result"])
        self.assertEqual("ready", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["minutes"]["status"])
        notes = updated["sources"]["minutes"].get("notes") or ""
        self.assertIn("robots", notes.lower())
        self.assertEqual([], validate_profile(updated))


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


FOLLOW_DOC_URL = "http://www.town.tara.lg.jp/var/rev0/0021/4208/1265717119.html"
DEPTH2_DOC_URL = (
    "http://www.town.tara.lg.jp/shisei/shigikai/R8gikai/202609/202609teirei.html"
)


def _year_html_with_council_doc(
    label: str = "令和7年第1回定例会 1日目 会議録",
) -> str:
    return f"""
<html><head><title>令和7年</title></head>
<body><h1>令和7年</h1><h2>定例会</h2>
<a href="/var/rev0/0021/4208/1265717119.html">{label}</a>
</body></html>
"""


class FollowLinkVerifyTests(unittest.TestCase):
    def test_follow_success_root_no_pdf_follow_has_doc(self) -> None:
        profile = _base_follow_profile()
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _root_html_with_year_links())
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
            }
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
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
            }
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
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
            }
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
        fetch4 = make_fetch_result(FOLLOW_YEAR_URL_4, _year_html_with_council_doc())
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

    def test_level1_candidates_do_not_starve_level2(self) -> None:
        # Regression: an index with 8 year links must not consume the whole
        # follow_max_pages budget at depth 1; depth-2 month pages must still
        # be reachable (Takeo-style year -> month -> PDF chain).
        profile = _base_follow_profile()
        cfg = profile["sources"]["minutes"]["config"]
        cfg["follow_max_depth"] = 2
        cfg["follow_max_pages"] = 8
        year_links = "\n".join(
            f'<a href="/chosei/_1010/_1414/_70{i:02d}.html">令和{i}年</a>'
            for i in range(7, -1, -1)
        )
        root_html = f"""
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
{year_links}
</body></html>
"""
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_urls = {
            f"http://www.town.tara.lg.jp/chosei/_1010/_1414/_70{i:02d}.html"
            for i in range(8)
        }
        month_url = "http://www.town.tara.lg.jp/chosei/_1010/_1414/_7097m.html"
        year_html = """
<html><head><title>令和7年</title></head>
<body><h1>令和7年</h1>
<a href="/chosei/_1010/_1414/_7097m.html">令和7年3月</a>
</body></html>
"""
        month_fetch = make_fetch_result(
            month_url, _year_html_with_council_doc()
        )
        responses = {
            FOLLOW_INDEX_URL: root_fetch,
            month_url: month_fetch,
            FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
        }
        for u in year_urls:
            responses[u] = make_fetch_result(u, year_html)
        client = FakeHttpClient(responses)
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        fetched = [u for u, _ in client.calls]
        self.assertIn(month_url, fetched)
        self.assertLessEqual(len(fetched) - 1, 8)

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
        nested_html = _year_html_with_council_doc()
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
        year_fetch = make_fetch_result(FOLLOW_YEAR_URL, _year_html_with_council_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                FOLLOW_YEAR_URL: year_fetch,
                FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
            }
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
        year_fetch = make_fetch_result(encoded_url, _year_html_with_council_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                encoded_url: year_fetch,
                FOLLOW_DOC_URL: make_fetch_result(FOLLOW_DOC_URL, MINUTES_DOC_HTML),
            }
        )
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


def _dbsr_meeting_html() -> str:
    # Meeting detail page with speaker-attributed speeches (C4: fallback
    # paragraph chunks without speakers do not count as identification).
    return (
        "<html><head><title>令和7年6月定例会 本会議</title></head><body>"
        "<p>○議長（山田太郎）　開会します。</p>"
        "<p>○市長　提案理由を説明します。</p>"
        "</body></html>"
    )


def _dbsr_query_index_html() -> str:
    return (
        "<html><head>"
        '<meta name="author" content="上峰町議会事務局">'
        '<meta name="keywords" content="議会,会議録,議事録,検索">'
        "<title>上峰町議会 会議録検索</title></head><body>"
        '<a href="?QueryType=New&Template=List&ListOrder=ASC'
        '&Cabinet=1&TermStart=2026-06-05&TermEnd=2026-06-12">'
        "第２回定例会（６月）</a>"
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
        detail_fetch = make_fetch_result(DBSR_DETAIL_URL, _dbsr_meeting_html())
        client = FakeHttpClient({DBSR_INDEX_URL: fetch, DBSR_DETAIL_URL: detail_fetch})
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
        self.assertEqual(
            [DBSR_INDEX_URL, DBSR_DETAIL_URL], [u for u, _ in client.calls]
        )

    def test_dbsr_query_list_variant_promotes_to_ready(self) -> None:
        index_url = "http://www.town.kamimine.saga.dbsr.jp/index.php/"
        profile = _base_dbsr_needs_review(index_url=index_url)
        fetch = make_fetch_result(index_url, _dbsr_query_index_html())
        query_url = "http://www.town.kamimine.saga.dbsr.jp/index.php/?QueryType=New&Template=List&ListOrder=ASC&Cabinet=1&TermStart=2026-06-05&TermEnd=2026-06-12"
        query_fetch = make_fetch_result(query_url, _dbsr_meeting_html())
        client = FakeHttpClient({index_url: fetch, query_url: query_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        self.assertEqual([index_url, query_url], [u for u, _ in client.calls])

    def test_dbsr_evidence_idempotent(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())
        detail_fetch = make_fetch_result(DBSR_DETAIL_URL, _dbsr_meeting_html())
        client = FakeHttpClient({DBSR_INDEX_URL: fetch, DBSR_DETAIL_URL: detail_fetch})
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
        client = FakeHttpClient(
            {bad_url: make_fetch_result(bad_url, _dbsr_index_html())}
        )
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
        client = FakeHttpClient(
            {bad_url: make_fetch_result(bad_url, _dbsr_index_html())}
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid_index_url", report["reason"])
        self.assertEqual(0, len(client.calls))

    def test_dbsr_structure_mismatch_does_not_promote(self) -> None:
        # dbsr host but no minutes hint / no detail link -> must not promote
        html = (
            "<html><head><title>メンテナンス中</title></head><body>準備中</body></html>"
        )
        profile = _base_dbsr_needs_review()
        client = FakeHttpClient(
            {DBSR_INDEX_URL: make_fetch_result(DBSR_INDEX_URL, html)}
        )
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

    def test_dbsr_body_robots_denied_becomes_blocked(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())

        class DenyBodyClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                self.calls.append((url, tier))  # type: ignore[arg-type]
                if url == DBSR_DETAIL_URL:
                    raise RobotsDeniedError("robots.txt disallows")
                if url == DBSR_INDEX_URL:
                    return fetch
                raise AssertionError(f"unexpected url {url}")

        client = DenyBodyClient()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("blocked", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertIn("robots", report["reason"].lower())
        self.assertEqual("needs_review", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["minutes"]["status"])
        self.assertEqual(NOW, updated["sources"]["minutes"]["verified_at"])
        self.assertEqual("verify --live", updated["sources"]["minutes"]["verified_by"])
        ev = updated["sources"]["minutes"]["evidence"]
        self.assertTrue(
            any(
                e.get("url") == DBSR_INDEX_URL and e.get("sha256") == fetch.sha256
                for e in ev
            )
        )
        notes = updated["sources"]["minutes"].get("notes") or ""
        self.assertIn("robots", notes.lower())
        self.assertEqual([], validate_profile(updated))
        self.assertEqual(
            [DBSR_INDEX_URL, DBSR_DETAIL_URL], [u for u, _ in client.calls]
        )

    def test_dbsr_body_robots_denied_downgrades_ready_to_blocked(self) -> None:
        profile = _base_dbsr_needs_review()
        profile["sources"]["minutes"]["status"] = "ready"
        profile["sources"]["minutes"]["verified_at"] = "2020-01-01T00:00:00Z"
        profile["sources"]["minutes"]["verified_by"] = "verify --live"
        profile["sources"]["minutes"]["evidence"] = [
            {
                "url": DBSR_INDEX_URL,
                "observed_on": DBSR_INDEX_URL,
                "sha256": "a" * 64,
                "fetched_at": "2020-01-01T00:00:00Z",
            }
        ]
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())

        class DenyBodyClient2:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                self.calls.append((url, tier))  # type: ignore[arg-type]
                if url == DBSR_DETAIL_URL:
                    raise RobotsDeniedError("robots.txt disallows")
                if url == DBSR_INDEX_URL:
                    return fetch
                raise AssertionError(f"unexpected url {url}")

        client = DenyBodyClient2()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("blocked", report["result"])
        self.assertEqual("ready", report["status_before"])
        self.assertEqual("blocked", report["status_after"])
        self.assertEqual("blocked", updated["sources"]["minutes"]["status"])
        self.assertIn(
            "robots", (updated["sources"]["minutes"].get("notes") or "").lower()
        )
        self.assertEqual([], validate_profile(updated))

    def test_dbsr_body_fetch_error_does_not_change_status(self) -> None:
        profile = _base_dbsr_needs_review()
        fetch = make_fetch_result(DBSR_INDEX_URL, _dbsr_index_html())

        class FailBodyClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def fetch(self, url: str, *, tier: object, **_: object):  # type: ignore[no-untyped-def]
                self.calls.append((url, tier))  # type: ignore[arg-type]
                if url == DBSR_DETAIL_URL:
                    raise FetchError("HTTP 500")
                if url == DBSR_INDEX_URL:
                    return fetch
                raise AssertionError(f"unexpected url {url}")

        client = FailBodyClient()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )  # type: ignore[arg-type]
        self.assertEqual("failed", report["result"])
        self.assertIn("FetchError", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        self.assertEqual("needs_review", report["status_after"])


# -------------------------------------------------------------------
# Depth-2/3 BFS follow synthetic tests (T-301, no network)
# -------------------------------------------------------------------

DEPTH2_YEAR_URL = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai.html"
DEPTH2_MONTH_URL = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai/202609.html"
DEPTH2_MONTH_URL_2 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai/202610.html"


def _base_depth_profile(
    follow_regex: str = "R8gikai|2026",
    depth: int | None = None,
    pages: int | None = None,
) -> dict:
    p = _base_follow_profile(follow_regex=follow_regex)
    cfg = p["sources"]["minutes"].get("config")
    if not isinstance(cfg, dict):
        cfg = {}
        p["sources"]["minutes"]["config"] = cfg
    if depth is not None:
        cfg["follow_max_depth"] = depth  # type: ignore[assignment]
    if pages is not None:
        cfg["follow_max_pages"] = pages  # type: ignore[assignment]
    return p


def _index_html_depth2() -> str:
    return f"""
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="{DEPTH2_YEAR_URL}">R8gikai</a>
</body></html>
"""


def _year_html_depth2(month_url: str = DEPTH2_MONTH_URL) -> str:
    return f"""
<html><head><title>R8</title></head>
<body><h1>R8gikai</h1>
<a href="{month_url}">202609</a>
</body></html>
"""


def _month_html_with_doc() -> str:
    return """
<html><head><title>202609</title></head>
<body><h1>202609</h1><h2>定例会</h2>
<a href="/shisei/shigikai/R8gikai/202609/202609teirei.html">令和7年 定例会 会議録</a>
</body></html>
"""


class Depth2VerifyTests(unittest.TestCase):
    def test_depth2_chain_success(self) -> None:
        profile = _base_depth_profile(depth=2)
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        year_fetch = make_fetch_result(DEPTH2_YEAR_URL, _year_html_depth2())
        month_fetch = make_fetch_result(DEPTH2_MONTH_URL, _month_html_with_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                DEPTH2_YEAR_URL: year_fetch,
                DEPTH2_MONTH_URL: month_fetch,
                DEPTH2_DOC_URL: make_fetch_result(
                    DEPTH2_DOC_URL, MINUTES_DOC_HTML
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        ev = updated["sources"]["minutes"]["evidence"]
        # bounded evidence: index + successful month page (year not stored)
        self.assertTrue(any(e.get("url") == FOLLOW_INDEX_URL for e in ev))
        self.assertTrue(any(e.get("url") == DEPTH2_MONTH_URL for e in ev))
        self.assertEqual(2, len([e for e in ev if "sha256" in e]))
        self.assertTrue(
            any(
                e.get("observed_on") == FOLLOW_INDEX_URL
                for e in ev
                if e.get("url") == DEPTH2_MONTH_URL
            )
        )
        # intermediate year page must not create evidence
        self.assertFalse(any(e.get("url") == DEPTH2_YEAR_URL for e in ev))
        fetched = [u for u, _ in client.calls]
        self.assertIn(FOLLOW_INDEX_URL, fetched)
        self.assertIn(DEPTH2_YEAR_URL, fetched)
        self.assertIn(DEPTH2_MONTH_URL, fetched)
        self.assertEqual([], validate_profile(updated))

    def test_depth1_default_does_not_reach_depth2(self) -> None:
        profile = _base_depth_profile()  # default depth 1
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        year_fetch = make_fetch_result(DEPTH2_YEAR_URL, _year_html_depth2())
        month_fetch = make_fetch_result(DEPTH2_MONTH_URL, _month_html_with_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                DEPTH2_YEAR_URL: year_fetch,
                DEPTH2_MONTH_URL: month_fetch,
                DEPTH2_DOC_URL: make_fetch_result(
                    DEPTH2_DOC_URL, MINUTES_DOC_HTML
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertIn(DEPTH2_YEAR_URL, fetched)
        self.assertNotIn(DEPTH2_MONTH_URL, fetched)

    def test_depth3_chain_success(self) -> None:
        # index -> year -> month -> day? Actually 3 levels: index->L1->L2->L3(pdf)
        l1 = DEPTH2_YEAR_URL
        l2 = DEPTH2_MONTH_URL
        l3 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai/202609/day.html"
        profile = _base_depth_profile(depth=3)
        root_html = f'<html><head><title>会議録</title></head><body><h1>会議録</h1><a href="{l1}">R8gikai</a></body></html>'
        year_html = f'<html><head><title>y</title></head><body><h1>R8</h1><a href="{l2}">202609</a></body></html>'
        month_html = f'<html><head><title>m</title></head><body><h1>2026</h1><a href="{l3}">2026-09-01</a></body></html>'
        day_html = '<html><head><title>2026</title></head><body><h1>2026-09-01</h1><a href="202609teirei.html">令和7年 定例会 会議録</a></body></html>'
        day_doc_url = (
            "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai/"
            "202609/202609teirei.html"
        )
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: make_fetch_result(FOLLOW_INDEX_URL, root_html),
                l1: make_fetch_result(l1, year_html),
                l2: make_fetch_result(l2, month_html),
                l3: make_fetch_result(l3, day_html),
                day_doc_url: make_fetch_result(day_doc_url, MINUTES_DOC_HTML),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", updated["sources"]["minutes"]["status"])
        ev = updated["sources"]["minutes"]["evidence"]
        self.assertTrue(any(e.get("url") == l3 for e in ev))

    def test_host_drift_not_followed(self) -> None:
        profile = _base_depth_profile(depth=2)
        root_html = f"""
<html><head><title>会議録</title></head>
<body><h1>会議録</h1>
<a href="https://evil.example.com/chosei/2026.html">2026</a>
<a href="{DEPTH2_YEAR_URL}">R8gikai</a>
</body></html>
"""
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_fetch = make_fetch_result(DEPTH2_YEAR_URL, _year_html_depth2())
        month_fetch = make_fetch_result(DEPTH2_MONTH_URL, _month_html_with_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                DEPTH2_YEAR_URL: year_fetch,
                DEPTH2_MONTH_URL: month_fetch,
                DEPTH2_DOC_URL: make_fetch_result(
                    DEPTH2_DOC_URL, MINUTES_DOC_HTML
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn("https://evil.example.com/chosei/2026.html", fetched)
        self.assertIn(DEPTH2_YEAR_URL, fetched)

    def test_pdf_link_never_followed(self) -> None:
        # A PDF link stops navigation (it counts as a document), but with an
        # HTML-only config the extraction probe cannot use it: it is never
        # fetched and the entry is never promoted on it.
        profile = _base_depth_profile(follow_regex="R8gikai", depth=2)
        pdf_url = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai.pdf"
        root_html = f'<html><head><title>会議録</title></head><body><h1>会議録</h1><a href="{DEPTH2_YEAR_URL}">R8gikai</a></body></html>'
        year_html = (
            '<html><head><title>y</title></head><body><h1>R8</h1>'
            f'<a href="{pdf_url}">令和7年定例会 会議録</a>'
            "</body></html>"
        )
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_fetch = make_fetch_result(DEPTH2_YEAR_URL, year_html)
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                DEPTH2_YEAR_URL: year_fetch,
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("needs_review", report["result"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])
        fetched = [u for u, _ in client.calls]
        self.assertNotIn(pdf_url, fetched)

    def test_page_cap_bfs_order(self) -> None:
        # index has 4 year links, cap 3 -> only first 3 fetched, 4th never
        y1 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai1.html"
        y2 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai2.html"
        y3 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai3.html"
        y4 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai4.html"
        profile = _base_depth_profile(depth=1, pages=3)
        root_html = f"""
<html><head><title>会議録</title></head><body><h1>会議録</h1>
<a href="{y1}">R8gikai1</a>
<a href="{y2}">R8gikai2</a>
<a href="{y3}">R8gikai3</a>
<a href="{y4}">R8gikai4</a>
</body></html>
"""
        empty = "<html><head><title>y</title></head><body><h1>R8</h1><p>no doc</p></body></html>"
        pdf_html = _month_html_with_doc()
        # y4 would have the doc but should not be fetched due to cap
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: make_fetch_result(FOLLOW_INDEX_URL, root_html),
                y1: make_fetch_result(y1, empty),
                y2: make_fetch_result(y2, empty),
                y3: make_fetch_result(y3, empty),
                y4: make_fetch_result(y4, pdf_html),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertIn(y1, fetched)
        self.assertIn(y3, fetched)
        self.assertNotIn(y4, fetched)

    def test_page_cap_across_depths(self) -> None:
        # BFS with cap 3: index-> y1,y2 (2), y1 expands to m1,m2 but cap limits to 1 new
        y1 = DEPTH2_YEAR_URL
        y2 = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai2.html"
        m1 = DEPTH2_MONTH_URL
        m2 = DEPTH2_MONTH_URL_2
        profile = _base_depth_profile(depth=2, pages=3)
        root_html = f'<html><head><title>会議録</title></head><body><h1>会議録</h1><a href="{y1}">R8gikai</a><a href="{y2}">R8gikai2</a></body></html>'
        y1_html = f'<html><head><title>y1</title></head><body><h1>R8</h1><a href="{m1}">202609</a><a href="{m2}">202610</a></body></html>'
        y2_html = "<html><head><title>y2</title></head><body><h1>R8</h1><p>no doc</p></body></html>"
        # m2 has doc but should be beyond cap if BFS respects global cap
        m1_html = "<html><head><title>m1</title></head><body><h1>2026</h1><p>no doc</p></body></html>"
        m2_html = _month_html_with_doc()
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: make_fetch_result(FOLLOW_INDEX_URL, root_html),
                y1: make_fetch_result(y1, y1_html),
                y2: make_fetch_result(y2, y2_html),
                m1: make_fetch_result(m1, m1_html),
                m2: make_fetch_result(m2, m2_html),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        # With cap 3, total discovered pages = y1,y2,m1 (3). m2 not discovered -> not fetched -> fails
        self.assertEqual("failed", report["result"])
        fetched = [u for u, _ in client.calls]
        self.assertIn(y1, fetched)
        self.assertIn(y2, fetched)
        self.assertIn(m1, fetched)
        self.assertNotIn(m2, fetched)

    def test_dedupe_and_percent_decode(self) -> None:
        profile = _base_depth_profile(follow_regex="R8gikai", depth=2)
        dup_url = DEPTH2_YEAR_URL
        root_html = f'<html><head><title>会議録</title></head><body><h1>会議録</h1><a href="{dup_url}">R8gikai</a><a href="{dup_url}">R8gikai</a><a href="/chosei/_1010/_1414/%52%38gikai.html">R8gikai</a></body></html>'
        # Second encoded form resolves to same URL deduplicated
        encoded_url = "http://www.town.tara.lg.jp/chosei/_1010/_1414/R8gikai.html"
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, root_html)
        year_fetch = make_fetch_result(encoded_url, _year_html_depth2())
        month_fetch = make_fetch_result(DEPTH2_MONTH_URL, _month_html_with_doc())
        client = FakeHttpClient(
            {
                FOLLOW_INDEX_URL: root_fetch,
                encoded_url: year_fetch,
                DEPTH2_MONTH_URL: month_fetch,
                DEPTH2_DOC_URL: make_fetch_result(
                    DEPTH2_DOC_URL, MINUTES_DOC_HTML
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("verified", report["result"])
        fetched_year = [u for u, _ in client.calls if u == encoded_url]
        self.assertEqual(1, len(fetched_year))


class Depth2ConfigValidationTests(unittest.TestCase):
    def test_invalid_depth_string_fails(self) -> None:
        profile = _base_depth_profile(depth=2)  # type: ignore[arg-type]
        profile["sources"]["minutes"]["config"]["follow_max_depth"] = "2"  # type: ignore[index]
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid follow_max_depth", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["minutes"]["status"])

    def test_invalid_depth_out_of_range(self) -> None:
        for bad in (0, 4, 10):
            with self.subTest(bad=bad):
                profile = _base_depth_profile(depth=bad)
                root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
                client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
                updated, report = verify_profile(
                    profile, client=client, now=NOW, kind="minutes"
                )
                self.assertEqual("failed", report["result"])
                self.assertIn("invalid follow_max_depth", report["reason"])

    def test_invalid_depth_bool_fails(self) -> None:
        profile = _base_depth_profile(depth=1)
        profile["sources"]["minutes"]["config"]["follow_max_depth"] = True  # type: ignore[index]
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid follow_max_depth", report["reason"])

    def test_invalid_pages_string_fails(self) -> None:
        profile = _base_depth_profile(pages=2)  # type: ignore[arg-type]
        profile["sources"]["minutes"]["config"]["follow_max_pages"] = "3"  # type: ignore[index]
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid follow_max_pages", report["reason"])

    def test_invalid_pages_out_of_range(self) -> None:
        for bad in (0, 11, 100):
            with self.subTest(bad=bad):
                profile = _base_depth_profile(pages=bad)
                root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
                client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
                updated, report = verify_profile(
                    profile, client=client, now=NOW, kind="minutes"
                )
                self.assertEqual("failed", report["result"])
                self.assertIn("invalid follow_max_pages", report["reason"])

    def test_invalid_pages_bool_fails(self) -> None:
        profile = _base_depth_profile(pages=3)
        profile["sources"]["minutes"]["config"]["follow_max_pages"] = False  # type: ignore[index]
        root_fetch = make_fetch_result(FOLLOW_INDEX_URL, _index_html_depth2())
        client = FakeHttpClient({FOLLOW_INDEX_URL: root_fetch})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="minutes"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("invalid follow_max_pages", report["reason"])


# ---------------------------------------------------------------------------
# budget / settlement synthetic tests (no network)
# ---------------------------------------------------------------------------

BUDGET_INDEX_URL = "https://www.town.tara.lg.jp/chosei/_1726/_2042.html"
BUDGET_DOC_URL = "https://www.town.tara.lg.jp/var/rev0/0019/4460/12622610125.pdf"
SETTLEMENT_DOC_URL = "https://www.town.tara.lg.jp/var/rev0/0020/5006/12633110918.pdf"


def _base_budget_ready(kind: str = "budget") -> dict:
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
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "budget": {
                "status": "ready",
                "adapter": "official_document_index",
                "index_url": BUDGET_INDEX_URL,
                "verified_at": "2026-08-23T01:43:49Z",
                "verified_by": "pi-pilot-scout",
                # preflight-derived deepest document record:
                "evidence": [
                    {"url": BUDGET_INDEX_URL, "observed_on": BUDGET_INDEX_URL},
                    {"url": BUDGET_DOC_URL, "observed_on": BUDGET_INDEX_URL},
                ],
                "notes": "preflight-derived",
            },
            "settlement": {
                **({"status": "ready"} if kind == "settlement" else {"status": "not_evaluated"}),
                "adapter": (
                    "official_document_index" if kind == "settlement" else None
                ),
                "index_url": BUDGET_INDEX_URL if kind == "settlement" else None,
                "verified_at": (
                    "2026-08-23T01:43:49Z" if kind == "settlement" else None
                ),
                "verified_by": (
                    "pi-pilot-scout" if kind == "settlement" else None
                ),
                "evidence": (
                    [{"url": SETTLEMENT_DOC_URL, "observed_on": BUDGET_INDEX_URL}]
                    if kind == "settlement"
                    else []
                ),
                "notes": "preflight-derived" if kind == "settlement" else None,
            },
        },
    }


BUDGET_INDEX_HTML = (
    "<html><head><title>財政（予算・決算）一覧</title></head><body>"
    "<h1>予算のページ</h1>"
    f'<a href="{BUDGET_DOC_URL}">令和8年度 当初予算書 (PDF)</a>'
    "</body></html>"
)


class BudgetSettlementVerifyTests(unittest.TestCase):
    """budget/settlement: document presence + structural markers.

    Boundary: no generic extractor exists, so verify NEVER grants ready here;
    the reachable outcomes are needs_review (evidence recorded) and blocked.
    """

    def _patch_pdftotext(self, text: str) -> tuple[mock._patch, ...]:  # type: ignore[name-defined]
        which_target = "source_profiles.verify.shutil.which"
        run_target = "source_profiles.verify.subprocess.run"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=text.encode("utf-8"), stderr=b""
        )
        return (
            mock.patch(which_target, return_value="/usr/bin/pdftotext"),
            mock.patch(run_target, return_value=completed),
        )

    def _budget_client(self, doc_url: str = BUDGET_DOC_URL) -> FakeHttpClient:
        return FakeHttpClient(
            {
                BUDGET_INDEX_URL: make_fetch_result(BUDGET_INDEX_URL, BUDGET_INDEX_HTML),
                doc_url: make_fetch_result(
                    doc_url, "%PDF-1.4 synthetic", content_type="application/pdf"
                ),
            }
        )

    def test_budget_structure_confirmed_is_needs_review_not_ready(self) -> None:
        # preflight-derived ready must be re-verified down to needs_review with
        # structural evidence; verify cannot re-grant ready (no extractor).
        profile = _base_budget_ready("budget")
        which_patch, run_patch = self._patch_pdftotext(
            "歳入歳出の款項明細。予算総額 12億円。"
        )
        with which_patch, run_patch:
            updated, report = verify_profile(
                profile, client=self._budget_client(), now=NOW, kind="budget"
            )
        self.assertEqual("needs_review", report["result"])
        self.assertEqual("ready", report["status_before"])
        self.assertEqual("needs_review", report["status_after"])
        self.assertIn("document_structure_confirmed", report["reason"])
        entry = updated["sources"]["budget"]
        self.assertEqual("needs_review", entry["status"])
        notes = entry.get("notes") or ""
        self.assertIn("構造マーカー", notes)
        self.assertEqual([], validate_profile(updated))

    def test_settlement_structure_confirmed_is_needs_review(self) -> None:
        profile = _base_budget_ready("settlement")
        which_patch, run_patch = self._patch_pdftotext(
            "歳入歳出の決算額。決算総額 10億円。款項明細。"
        )
        client = self._budget_client(doc_url=SETTLEMENT_DOC_URL)
        with which_patch, run_patch:
            updated, report = verify_profile(
                profile, client=client, now=NOW, kind="settlement"
            )
        self.assertEqual("needs_review", report["result"])
        self.assertEqual("needs_review", report["status_after"])
        self.assertIn("document_structure_confirmed", report["reason"])
        notes = updated["sources"]["settlement"].get("notes") or ""
        self.assertIn("構造マーカー", notes)

    def test_budget_document_without_markers_stays_needs_review(self) -> None:
        # Document reached but no 歳入/歳出/款/項 markers: not structure-confirmed.
        profile = _base_budget_ready("budget")
        which_patch, run_patch = self._patch_pdftotext("広報お知らせ 予算の説明会を開催します。")
        with which_patch, run_patch:
            updated, report = verify_profile(
                profile, client=self._budget_client(), now=NOW, kind="budget"
            )
        self.assertEqual("needs_review", report["result"])
        self.assertIn("document_reached_without_markers", report["reason"])
        notes = updated["sources"]["budget"].get("notes") or ""
        self.assertIn("構造マーカー", notes)

    def test_budget_no_document_link_reports_needs_review(self) -> None:
        html = (
            "<html><head><title>財政一覧</title></head><body>"
            "<a href=\"https://www.town.tara.lg.jp/notice.pdf\">お知らせ (PDF)</a>"
            "</body></html>"
        )
        profile = _base_budget_ready("budget")
        profile["sources"]["budget"]["evidence"] = []  # type: ignore[index]
        client = FakeHttpClient({BUDGET_INDEX_URL: make_fetch_result(BUDGET_INDEX_URL, html)})
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="budget"
        )
        self.assertEqual("needs_review", report["result"])
        self.assertIn("no_document_link", report["reason"])
        self.assertEqual("needs_review", updated["sources"]["budget"]["status"])

    def test_budget_probe_pdftotext_missing_is_inconclusive_untouched(self) -> None:
        # Local tooling absence must not demote preflight-derived ready.
        profile = _base_budget_ready("budget")
        which_patch_off = mock.patch(
            "source_profiles.verify.shutil.which", return_value=None
        )
        with which_patch_off:
            updated, report = verify_profile(
                profile, client=self._budget_client(), now=NOW, kind="budget"
            )
        self.assertEqual("failed", report["result"])
        self.assertIn("probe_inconclusive", report["reason"])
        self.assertIn("pdf_cached_pdftotext_unavailable", report["reason"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["budget"]["status"])

    def test_budget_probe_unprobeable_media_is_inconclusive_untouched(self) -> None:
        # A non-PDF/non-HTML document (e.g., xlsx) is unprobeable by this
        # verifier: status stays untouched rather than guessing.
        profile = _base_budget_ready("budget")
        doc_url = "https://www.town.tara.lg.jp/var/yosan.xlsx"
        profile["sources"]["budget"]["evidence"] = [  # type: ignore[index]
            {"url": doc_url, "observed_on": BUDGET_INDEX_URL}
        ]
        client = FakeHttpClient(
            {
                BUDGET_INDEX_URL: make_fetch_result(BUDGET_INDEX_URL, BUDGET_INDEX_HTML),
                doc_url: make_fetch_result(
                    doc_url, "PK\x03\x04 binary", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="budget"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("probe_inconclusive", report["reason"])
        self.assertIn("unprobeable_media_type", report["reason"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual("ready", updated["sources"]["budget"]["status"])

    def test_budget_doc_robots_denied_sets_blocked(self) -> None:
        class DenyDocClient(FakeHttpClient):
            def fetch(self, url: str, *, tier: CacheTier, **_: object) -> object:
                self.calls.append((url, tier))
                if url == BUDGET_DOC_URL:
                    raise RobotsDeniedError("robots.txt disallows /var/")
                return self.responses[url]

        profile = _base_budget_ready("budget")
        client = DenyDocClient(
            {
                BUDGET_INDEX_URL: make_fetch_result(BUDGET_INDEX_URL, BUDGET_INDEX_HTML),
                BUDGET_DOC_URL: make_fetch_result(
                    BUDGET_DOC_URL, "%PDF-1.4 synthetic", content_type="application/pdf"
                ),
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="budget"  # type: ignore[arg-type]
        )
        self.assertEqual("blocked", report["result"])
        self.assertEqual("blocked", updated["sources"]["budget"]["status"])
        notes = updated["sources"]["budget"].get("notes") or ""
        self.assertIn("robots", notes.lower())

    def test_budget_unsupported_adapter_fails(self) -> None:
        profile = _base_budget_ready("budget")
        profile["sources"]["budget"]["adapter"] = "vendor_custom"  # type: ignore[index]
        updated, report = verify_profile(
            profile, client=FakeHttpClient({}), now=NOW, kind="budget"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("unsupported", report["reason"])


# ---------------------------------------------------------------------------
# joureikun regulations synthetic tests (no network)
# ---------------------------------------------------------------------------

JOUREIKUN_INDEX_URL = "https://www.town.omachi.lg.jp/joureikun/index.html"
JOUREIKUN_ACT_URL = "https://www.town.omachi.lg.jp/joureikun/act/1.html"

JOUREIKUN_INDEX_HTML = (
    "<html><head><title>大町町例規集</title></head><body>"
    '<a href="act/1.html">大町町例規集条例</a>'
    "</body></html>"
)

JOUREIKUN_ACT_HTML = (
    "<html><head><title>大町町例規集条例</title></head><body>"
    "<h2>第一条</h2><p>この条例は町の例規について定める。</p>"
    "<h2>第二条</h2><p>施行に関して必要な事項は規則で定める。</p>"
    "</body></html>"
)


def _base_joureikun_needs_review() -> dict:
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
                "adapter": "joureikun",
                "index_url": JOUREIKUN_INDEX_URL,
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {"url": JOUREIKUN_INDEX_URL, "observed_on": VALID_HOME}
                ],
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


def _joureikun_client() -> FakeHttpClient:
    return FakeHttpClient(
        {
            JOUREIKUN_INDEX_URL: make_fetch_result(
                JOUREIKUN_INDEX_URL, JOUREIKUN_INDEX_HTML
            ),
            JOUREIKUN_ACT_URL: make_fetch_result(
                JOUREIKUN_ACT_URL, JOUREIKUN_ACT_HTML
            ),
        }
    )


class JoureikunRegulationsVerifyTests(unittest.TestCase):
    def test_joureikun_promotes_to_ready(self) -> None:
        # 大町型: joureikun catalog + act pages with numbered articles.
        profile = _base_joureikun_needs_review()
        updated, report = verify_profile(
            profile, client=_joureikun_client(), now=NOW, kind="regulations"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual(
            "ready", updated["sources"]["regulations"]["status"]
        )
        self.assertEqual([], validate_profile(updated))

    def test_joureikun_robots_denied_does_not_promote(self) -> None:
        class DenyClient(FakeHttpClient):
            def fetch(
                self, url: str, *, tier: CacheTier, **_: object
            ) -> object:
                self.calls.append((url, tier))
                raise RobotsDeniedError("robots.txt disallows /joureikun/")

        profile = _base_joureikun_needs_review()
        client = DenyClient(
            {
                JOUREIKUN_INDEX_URL: make_fetch_result(
                    JOUREIKUN_INDEX_URL, JOUREIKUN_INDEX_HTML
                )
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="regulations"  # type: ignore[arg-type]
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual(
            "needs_review", updated["sources"]["regulations"]["status"]
        )

    def test_joureikun_catalog_without_act_links_is_structure_mismatch(
        self,
    ) -> None:
        # Catalog reachable but no act-pattern links: the vendor structure
        # check raises, which is a safe stop (failed, status untouched) —
        # same semantics as g_reiki's structure_mismatch.
        html = (
            "<html><head><title>例規集</title></head>"
            '<body><a href="/other/page.html">案内</a></body></html>'
        )
        client = FakeHttpClient(
            {JOUREIKUN_INDEX_URL: make_fetch_result(JOUREIKUN_INDEX_URL, html)}
        )
        profile = _base_joureikun_needs_review()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="regulations"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("structure", report["reason"].lower())
        self.assertEqual(
            "needs_review", updated["sources"]["regulations"]["status"]
        )


# ---------------------------------------------------------------------------
# d1_law regulations synthetic tests (no network)
# ---------------------------------------------------------------------------

D1LAW_INDEX_URL = "https://example.invalid/d1law/reiki.html"
D1LAW_MOKUJI_URL = "https://example.invalid/d1law/mokuji_bunya.html"
D1LAW_BUNYA_URL = "https://example.invalid/d1law/bunya_0010000.html"
D1LAW_DOC_ID = "r0001"
D1LAW_J_URL = f"https://example.invalid/d1law/{D1LAW_DOC_ID}/{D1LAW_DOC_ID}_j.html"

D1LAW_REIKI_HTML = """<html><head><title>Reiki</title></head>
<frameset cols="30%,70%">
<frame src="mokuji_bunya.html">
<frame src="bunya_0010000.html">
</frameset></html>"""

D1LAW_MOKUJI_HTML = """<html><body>
<a href="bunya_0010000.html">総規</a>
</body></html>"""

D1LAW_BUNYA_HTML = f"""<html><body>
<a href="javascript:OpenResDataWin('{D1LAW_DOC_ID}')" title="例規1">テスト例規</a>
</body></html>"""

D1LAW_J_HTML = """<html><head><title>テスト例規</title></head><body>
<div class="main-text">
<h2>第1条</h2>
<p>この条例は、テストのために定める。</p>
<h2>第2条</h2>
<p>この条例は、公布の日から施行する。</p>
</div></body></html>"""


def _base_d1law_needs_review() -> dict:
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
                "adapter": "d1_law",
                "index_url": D1LAW_INDEX_URL,
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {"url": D1LAW_INDEX_URL, "observed_on": VALID_HOME}
                ],
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


def _d1law_client() -> FakeHttpClient:
    return FakeHttpClient(
        {
            D1LAW_INDEX_URL: make_fetch_result(D1LAW_INDEX_URL, D1LAW_REIKI_HTML),
            D1LAW_MOKUJI_URL: make_fetch_result(D1LAW_MOKUJI_URL, D1LAW_MOKUJI_HTML),
            D1LAW_BUNYA_URL: make_fetch_result(D1LAW_BUNYA_URL, D1LAW_BUNYA_HTML),
            D1LAW_J_URL: make_fetch_result(D1LAW_J_URL, D1LAW_J_HTML),
        }
    )


class D1LawRegulationsVerifyTests(unittest.TestCase):
    def test_d1law_promotes_to_ready(self) -> None:
        profile = _base_d1law_needs_review()
        updated, report = verify_profile(
            profile, client=_d1law_client(), now=NOW, kind="regulations"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual(
            "ready", updated["sources"]["regulations"]["status"]
        )
        self.assertEqual([], validate_profile(updated))

    def test_d1law_robots_denied_does_not_promote(self) -> None:
        class DenyClient(FakeHttpClient):
            def fetch(
                self, url: str, *, tier: CacheTier, **_: object
            ) -> object:
                self.calls.append((url, tier))
                raise RobotsDeniedError("robots.txt disallows /d1law/")

        profile = _base_d1law_needs_review()
        client = DenyClient(
            {
                D1LAW_INDEX_URL: make_fetch_result(
                    D1LAW_INDEX_URL, D1LAW_REIKI_HTML
                )
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="regulations"  # type: ignore[arg-type]
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual(
            "needs_review", updated["sources"]["regulations"]["status"]
        )

    def test_d1law_catalog_without_open_links_is_structure_mismatch(
        self,
    ) -> None:
        html = (
            "<html><head><title>例規集</title></head>"
            "<body><p>案内</p></body></html>"
        )
        client = FakeHttpClient(
            {D1LAW_INDEX_URL: make_fetch_result(D1LAW_INDEX_URL, html)}
        )
        profile = _base_d1law_needs_review()
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="regulations"
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("structure", report["reason"].lower())
        self.assertEqual(
            "needs_review", updated["sources"]["regulations"]["status"]
        )


# ---------------------------------------------------------------------------
# d1_law opensearch regulations synthetic tests (no network)
# ---------------------------------------------------------------------------

D1LAW_OPENSEARCH_INDEX_URL = "https://ops-jg.d1-law.com/opensearch/?jctcd=8A7FF95853"
D1LAW_OPENSEARCH_INIT_URL = (
    "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A7FF95853"
)
D1LAW_OPENSEARCH_SEARCH_URL = (
    "https://ops-jg.d1-law.com/opensearch/SrMjF01/search?typeSearch=SrMj_Genko&mokujicd=001:00:00"
)
D1LAW_OPENSEARCH_DOC_URL = (
    "https://ops-jg.d1-law.com/opensearch/SrJbF01/init?jctcd=8A7FF95853&houcd=H417901010001&fromJsp=SrMj"
)

D1LAW_OPENSEARCH_INIT_HTML = """<html><head><title>春日部市例規集</title></head><body>
<div id="mokujiSearch"><ul class="treeview"><li id="treeGenko">
<a title="第１ 例規" href="javascript:void(0)" onclick="mkjG('001:00:00');">第１ 例規</a>
</li></ul></div></body></html>"""

D1LAW_OPENSEARCH_SEARCH_HTML = """<html><head><title>検索結果</title></head><body>
<table><tr><td>
<a href="javascript:void(0)" onClick="doViewJobunFromJsp('8A7FF95853', 'H417901010001', null, null, '1', '1', null, 'SrMj'); return false;">
春日部市役所の位置を定める条例</a>（平成17年10月１日条例第１号）
</td></tr></table></body></html>"""

D1LAW_OPENSEARCH_DOC_HTML = """<html><head><title>春日部市役所の位置を定める条例 春日部市例規集</title></head><body>
<div id="honbunArea">
<div class="contents-lineheight-2">春日部市役所の位置を定める条例</div>
<div class="contents-lineheight-2">第１条 この条例は、市役所の位置を定める。</div>
</div></body></html>"""


def _base_d1law_opensearch_needs_review() -> dict:
    profile = _base_d1law_needs_review()
    profile["sources"]["regulations"]["index_url"] = D1LAW_OPENSEARCH_INDEX_URL
    profile["sources"]["regulations"]["evidence"] = [
        {"url": D1LAW_OPENSEARCH_INDEX_URL, "observed_on": VALID_HOME}
    ]
    return profile


def _d1law_opensearch_client() -> FakeHttpClient:
    return FakeHttpClient(
        {
            D1LAW_OPENSEARCH_INDEX_URL: make_fetch_result(
                D1LAW_OPENSEARCH_INDEX_URL, D1LAW_OPENSEARCH_INIT_HTML
            ),
            D1LAW_OPENSEARCH_INIT_URL: make_fetch_result(
                D1LAW_OPENSEARCH_INIT_URL, D1LAW_OPENSEARCH_INIT_HTML
            ),
            D1LAW_OPENSEARCH_SEARCH_URL: make_fetch_result(
                D1LAW_OPENSEARCH_SEARCH_URL, D1LAW_OPENSEARCH_SEARCH_HTML
            ),
            D1LAW_OPENSEARCH_DOC_URL: make_fetch_result(
                D1LAW_OPENSEARCH_DOC_URL, D1LAW_OPENSEARCH_DOC_HTML
            ),
        }
    )


class D1LawOpenSearchRegulationsVerifyTests(unittest.TestCase):
    def test_d1law_opensearch_promotes_to_ready(self) -> None:
        profile = _base_d1law_opensearch_needs_review()
        updated, report = verify_profile(
            profile, client=_d1law_opensearch_client(), now=NOW, kind="regulations"
        )
        self.assertEqual("verified", report["result"])
        self.assertEqual("ready", report["status_after"])
        self.assertEqual(
            "ready", updated["sources"]["regulations"]["status"]
        )
        self.assertEqual([], validate_profile(updated))

    def test_d1law_opensearch_robots_denied_does_not_promote(self) -> None:
        class DenyClient(FakeHttpClient):
            def fetch(
                self, url: str, *, tier: CacheTier, **_: object
            ) -> object:
                self.calls.append((url, tier))
                raise RobotsDeniedError("robots.txt disallows /opensearch/")

        profile = _base_d1law_opensearch_needs_review()
        client = DenyClient(
            {
                D1LAW_OPENSEARCH_INDEX_URL: make_fetch_result(
                    D1LAW_OPENSEARCH_INDEX_URL, D1LAW_OPENSEARCH_INIT_HTML
                )
            }
        )
        updated, report = verify_profile(
            profile, client=client, now=NOW, kind="regulations"  # type: ignore[arg-type]
        )
        self.assertEqual("failed", report["result"])
        self.assertIn("RobotsDenied", report["reason"])
        self.assertEqual(
            "needs_review", updated["sources"]["regulations"]["status"]
        )


if __name__ == "__main__":
    unittest.main()
