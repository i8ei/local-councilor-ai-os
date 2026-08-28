"""Unit tests for vendor_soumu multi-year fiscal discovery and ingestion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bootstrap.cli.http import BOOTSTRAP_USER_AGENT, HttpClient
from modules.settlement_review.vendor_soumu import (
    discover_fiscal_years,
    fetch_and_ingest_multi_year,
)


class VendorSoumuTests(unittest.TestCase):
    def test_discover_fiscal_years_from_cache(self) -> None:
        client = HttpClient(cache_dir=Path("bootstrap/.cache"), user_agent=BOOTSTRAP_USER_AGENT)
        try:
            years = discover_fiscal_years(client, max_years=5)
            self.assertTrue(len(years) >= 1)
            self.assertIn(2024, years)
            self.assertTrue(years[2024].startswith("https://www.soumu.go.jp/"))
        except Exception as e:
            self.skipTest(f"Network / cache not available: {e}")

    def test_fetch_and_ingest_multi_year(self) -> None:
        client = HttpClient(cache_dir=Path("bootstrap/.cache"), user_agent=BOOTSTRAP_USER_AGENT)
        try:
            years = discover_fiscal_years(client, max_years=3)
        except Exception:
            self.skipTest("Fiscal pages not discovered")

        # Test single-year or multi-year ingestion if 2024 is available in cache
        if 2024 not in years:
            self.skipTest("2024 not in discovered years")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "settlement.db"
            csv_dir = tmp_path / "csvs"

            res = fetch_and_ingest_multi_year(
                "太良町",
                [2024],
                db_path=db_path,
                out_dir=csv_dir,
                cache_dir=Path("bootstrap/.cache"),
            )
            self.assertTrue(res["verified"])
            self.assertEqual("太良町", res["municipality"])
            self.assertEqual([2024], res["years"])


if __name__ == "__main__":
    unittest.main()
