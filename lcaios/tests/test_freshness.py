"""Tests for source-specific freshness evaluation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from lcaios.freshness import evaluate_bootstrap_freshness

ROWS = [
    {
        "indicator_key": "population_total",
        "as_of": "2020-10-01",
        "source_name": "e-Stat",
        "fetched_at": "2026-07-23T00:00:00Z",
    },
    {
        "indicator_key": "zaiseiryoku_shisuu",
        "as_of": "2024年度",
        "source_name": "総務省",
        "fetched_at": "2026-07-23T00:00:00Z",
    },
]
METADATA = {
    "census_selection": {
        "used_fallback": False,
        "reason": "latest shared survey date",
    },
    "fiscal_discovery": {
        "index_url": "https://example.invalid/fiscal-index",
    },
}


class FreshnessTests(unittest.TestCase):
    def test_recent_source_checks_are_fresh(self) -> None:
        result = evaluate_bootstrap_freshness(
            ROWS,
            METADATA,
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual("fresh", result["state"])
        self.assertEqual(
            {"estat-api-v3", "soumu-municipal-fiscal-overview"},
            {item["source_id"] for item in result["sources"]},
        )
        self.assertTrue(
            all(item["check_due_at"] for item in result["sources"])
        )

    def test_registry_interval_expiry_is_due(self) -> None:
        old_rows = [
            {**item, "fetched_at": "2024-01-01T00:00:00Z"}
            for item in ROWS
        ]
        result = evaluate_bootstrap_freshness(
            old_rows,
            METADATA,
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual("due", result["state"])

    def test_census_fallback_is_stale_even_when_recent(self) -> None:
        result = evaluate_bootstrap_freshness(
            ROWS,
            {
                **METADATA,
                "census_selection": {
                    "used_fallback": True,
                    "reason": "fallback",
                },
            },
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual("stale", result["state"])
        estat = next(
            item for item in result["sources"] if item["source_id"] == "estat-api-v3"
        )
        self.assertEqual("census_fallback_used", estat["reason"])

    def test_missing_source_rows_are_unknown(self) -> None:
        result = evaluate_bootstrap_freshness(
            ROWS[:1],
            METADATA,
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual("unknown", result["state"])

    def test_missing_discovery_metadata_is_unknown(self) -> None:
        result = evaluate_bootstrap_freshness(
            ROWS,
            {},
            now=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual("unknown", result["state"])
        reasons = {item["reason"] for item in result["sources"]}
        self.assertIn("latest_census_selection_not_recorded", reasons)
        self.assertIn("latest_fiscal_discovery_not_recorded", reasons)


if __name__ == "__main__":
    unittest.main()
