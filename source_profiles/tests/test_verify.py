"""Tests for verify_profile (synthetic, no network)."""

from __future__ import annotations  # noqa: I001

import copy
import unittest
from dataclasses import replace

from lcaios.http import RobotsDeniedError
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


if __name__ == "__main__":
    unittest.main()
