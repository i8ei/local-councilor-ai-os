"""Tests for stdlib XLSX parsing and label-based fiscal extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bootstrap.cli.fiscal import _display_raw_value, parse_overview_xlsx
from bootstrap.cli.tests.fixtures import build_minimal_xlsx
from bootstrap.cli.xlsx import read_workbook


class XlsxFiscalTests(unittest.TestCase):
    def test_raw_value_shortens_only_obvious_float_noise(self) -> None:
        self.assertEqual("0.3", _display_raw_value("0.30000000000000004"))
        self.assertEqual("15.4", _display_raw_value("15.399999999999999"))
        self.assertEqual(
            "1.2345678901234567",
            _display_raw_value("1.2345678901234567"),
        )
        self.assertEqual("1,234.50", _display_raw_value("1,234.50"))

    def test_shared_strings_and_header_alias_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            build_minimal_xlsx(path)
            workbook = read_workbook(path)
            self.assertEqual("概況", workbook[0].name)
            records, parse_info = parse_overview_xlsx(
                path,
                {
                    "name": "架空町",
                    "local_government_code_6": "123457",
                },
                2024,
                "https://example.invalid/fiscal-year.html",
                {
                    "xlsx_url": "https://example.invalid/opaque.xlsx",
                    "xlsx_sha256": "fixture-sha",
                    "xlsx_fetched_at": "2026-01-02T03:04:05Z",
                },
            )
        self.assertEqual(6, len(records))
        self.assertTrue(
            parse_info["header_fallbacks"]["local_government_code_6"][
                "fallback_used"
            ]
        )
        future_burden = next(
            item
            for item in records
            if item["indicator"] == "shourai_futan_hiritsu"
        )
        self.assertIsNone(future_burden["value"])
        self.assertEqual("-", future_burden["raw_value"])


if __name__ == "__main__":
    unittest.main()
