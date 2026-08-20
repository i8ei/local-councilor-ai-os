"""Tests for source profile validator (synthetic data only)."""

from __future__ import annotations  # noqa: I001

import datetime
import unittest

from source_profiles.schema import (
    validate_profile,  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
)

# Use a real registry entry for valid base (avoid guessing)
VALID_AREA = "41441"
VALID_PREFECTURE = "佐賀県"
VALID_MUNICIPALITY = "太良町"
VALID_HOME = "http://www.town.tara.lg.jp/"


def _base_valid() -> dict:
    """Return a minimal valid profile (not_evaluated except one g_reiki needs_review)."""
    return {
        "schema_version": 1,
        "area_code_5": VALID_AREA,
        "prefecture": VALID_PREFECTURE,
        "municipality": VALID_MUNICIPALITY,
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
                "base_url": "https://www1.g-reiki.net/town.tara/",
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {
                        "url": "https://www1.g-reiki.net/town.tara/reiki_menu.html",
                        "observed_on": VALID_HOME,
                    }
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


class SchemaTests(unittest.TestCase):
    def test_valid_profile_passes(self) -> None:
        errs = validate_profile(_base_valid())
        self.assertEqual([], errs)

    def test_missing_required_key(self) -> None:
        data = _base_valid()
        del data["schema_version"]
        errs = validate_profile(data)
        self.assertTrue(any("schema_version" in e for e in errs))

    def test_area_code_format_invalid(self) -> None:
        data = _base_valid()
        data["area_code_5"] = "1234"
        errs = validate_profile(data)
        self.assertTrue(any("area_code_5" in e for e in errs))
        data["area_code_5"] = "abcde"
        errs = validate_profile(data)
        self.assertTrue(any("area_code_5" in e for e in errs))

    def test_enum_violation_status(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "invalid_status"
        errs = validate_profile(data)
        self.assertTrue(any("status" in e for e in errs))

    def test_enum_violation_adapter(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["adapter"] = "unknown_adapter"
        errs = validate_profile(data)
        self.assertTrue(any("adapter" in e for e in errs))

    def test_entry_exclusivity_violation(self) -> None:
        data = _base_valid()
        # g_reiki requires base_url, adding index_url should violate exclusivity
        data["sources"]["regulations"]["index_url"] = "https://example.com/index.html"
        errs = validate_profile(data)
        self.assertTrue(any("mutually exclusive" in e for e in errs))

    def test_ready_missing_verified_at(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        data["sources"]["regulations"]["verified_at"] = None
        data["sources"]["regulations"]["verified_by"] = "tester"
        errs = validate_profile(data)
        self.assertTrue(any("verified_at" in e for e in errs))

    def test_ready_missing_verified_by(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        data["sources"]["regulations"]["verified_at"] = "2026-08-19T00:00:00Z"
        data["sources"]["regulations"]["verified_by"] = None
        errs = validate_profile(data)
        self.assertTrue(any("verified_by" in e for e in errs))

    def test_ready_missing_evidence(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        data["sources"]["regulations"]["verified_at"] = "2026-08-19T00:00:00Z"
        data["sources"]["regulations"]["verified_by"] = "tester"
        data["sources"]["regulations"]["evidence"] = []
        errs = validate_profile(data)
        self.assertTrue(any("evidence" in e for e in errs))

    def test_ready_missing_entry_url(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        data["sources"]["regulations"]["verified_at"] = "2026-08-19T00:00:00Z"
        data["sources"]["regulations"]["verified_by"] = "tester"
        # remove base_url
        del data["sources"]["regulations"]["base_url"]
        errs = validate_profile(data)
        self.assertTrue(any("entry URL" in e or "requires base_url" in e for e in errs))

    def test_ready_future_verified_at(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        future = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["sources"]["regulations"]["verified_at"] = future
        data["sources"]["regulations"]["verified_by"] = "tester"
        errs = validate_profile(data)
        self.assertTrue(any("future" in e for e in errs))

    def test_ready_valid_passes(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["status"] = "ready"
        data["sources"]["regulations"]["verified_at"] = "2026-08-19T00:00:00Z"
        data["sources"]["regulations"]["verified_by"] = "tester"
        errs = validate_profile(data)
        self.assertEqual([], errs)

    def test_registry_mismatch_official_home(self) -> None:
        data = _base_valid()
        data["official_home_url"] = "https://example.com/"
        errs = validate_profile(data)
        self.assertTrue(any("official_home_url mismatch" in e for e in errs))

    def test_registry_mismatch_prefecture(self) -> None:
        data = _base_valid()
        data["prefecture"] = "福岡県"
        errs = validate_profile(data)
        self.assertTrue(any("prefecture mismatch" in e for e in errs))

    def test_registry_mismatch_municipality(self) -> None:
        data = _base_valid()
        data["municipality"] = "架空町"
        errs = validate_profile(data)
        self.assertTrue(any("municipality mismatch" in e for e in errs))

    def test_registry_unknown_area(self) -> None:
        data = _base_valid()
        data["area_code_5"] = "99999"
        errs = validate_profile(data)
        self.assertTrue(any("not found in registry" in e for e in errs))

    def test_host_mismatch(self) -> None:
        data = _base_valid()
        # evidence host differs from entry host
        data["sources"]["regulations"]["evidence"] = [
            {
                "url": "https://example.com/reiki_menu.html",
                "observed_on": "https://example.com/",
            }
        ]
        errs = validate_profile(data)
        self.assertTrue(any("host must match" in e for e in errs))

    def test_adapter_null_with_entry_is_error(self) -> None:
        data = _base_valid()
        data["sources"]["minutes"]["adapter"] = None
        data["sources"]["minutes"]["status"] = "needs_review"
        # add entry with null adapter
        data["sources"]["minutes"]["base_url"] = "https://example.com/"
        data["sources"]["minutes"]["evidence"] = [
            {"url": "https://example.com/", "observed_on": "https://example.com/"}
        ]
        errs = validate_profile(data)
        self.assertTrue(any("adapter is null but entry" in e for e in errs))

    def test_static_requires_index_url(self) -> None:
        data = _base_valid()
        data["sources"]["minutes"]["adapter"] = "static"
        data["sources"]["minutes"]["status"] = "needs_review"
        data["sources"]["minutes"]["evidence"] = []
        # missing index_url
        errs = validate_profile(data)
        self.assertTrue(any("requires index_url" in e for e in errs))

    def test_g_reiki_requires_base_url(self) -> None:
        data = _base_valid()
        data["sources"]["regulations"]["adapter"] = "g_reiki"
        del data["sources"]["regulations"]["base_url"]
        # keep evidence but entry missing
        errs = validate_profile(data)
        self.assertTrue(any("requires base_url" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
