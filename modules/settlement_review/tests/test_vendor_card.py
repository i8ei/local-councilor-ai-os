"""Unit tests for vendor_card XLSX to settlement review CSV converter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modules.settlement_review.ingest_csv import main as ingest_main
from modules.settlement_review.vendor_card import extract_settlement_csvs
from modules.settlement_review.verify_totals import verify


class VendorCardTests(unittest.TestCase):
    def test_extract_settlement_csvs_and_verify(self) -> None:
        cache_dir = Path("bootstrap/.cache")
        xlsx_files = list(cache_dir.glob("*.body"))
        if not xlsx_files:
            self.skipTest("No cached XLSX found")

        # Find the XLSX body among cache files
        xlsx_path = None
        for p in xlsx_files:
            try:
                from bootstrap.cli.xlsx import read_workbook
                wb = read_workbook(p)
                if wb:
                    xlsx_path = p
                    break
            except Exception:
                continue

        if not xlsx_path:
            self.skipTest("No valid XLSX found in cache")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_dir = tmp_path / "csvs"
            db_path = tmp_path / "settlement.db"

            paths = extract_settlement_csvs(
                xlsx_path,
                "太良町",
                2024,
                csv_dir,
            )
            self.assertTrue(paths["summary"].is_file())
            self.assertTrue(paths["revenue"].is_file())
            self.assertTrue(paths["expenditure"].is_file())

            # Ingest all 3 CSVs
            ret_s = ingest_main(["summary", str(paths["summary"]), "--db", str(db_path)])
            ret_r = ingest_main(["revenue", str(paths["revenue"]), "--db", str(db_path)])
            ret_e = ingest_main(["expenditure", str(paths["expenditure"]), "--db", str(db_path)])
            self.assertEqual(ret_s, 0)
            self.assertEqual(ret_r, 0)
            self.assertEqual(ret_e, 0)

            # Verify totals (Must pass with 0 exit code)
            exit_code = verify(db_path)
            self.assertEqual(0, exit_code)


if __name__ == "__main__":
    unittest.main()
