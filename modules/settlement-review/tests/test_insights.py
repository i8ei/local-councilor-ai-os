"""Tests for settlement review candidate generation."""

from __future__ import annotations

import csv
import io
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import csv_templates  # noqa: E402
import insights  # noqa: E402
import tests.create_fixtures as create_fixtures  # noqa: E402


class InsightTests(unittest.TestCase):
    def setUp(self) -> None:
        create_fixtures.main()
        self.passing = MODULE_DIR / "tests" / "passing.db"
        self.failing = MODULE_DIR / "tests" / "failing.db"

    def test_generate_review_candidates_from_verified_database(self) -> None:
        with sqlite3.connect(self.passing) as connection:
            result = insights.generate_insights(connection)
        kinds = {item["analysis_kind"] for item in result["candidates"]}
        self.assertIn("large_unused", kinds)
        self.assertIn("large_outstanding_revenue", kinds)
        candidate = result["candidates"][0]
        self.assertIn("value", candidate)
        self.assertIn("definition", candidate)
        self.assertIn("source", candidate)
        self.assertIn("unresolved", candidate)

    def test_cli_stops_when_verification_fails(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_DIR / "insights.py"), str(self.failing)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("verify_totals failed", completed.stderr)

    def test_csv_templates_have_required_headers(self) -> None:
        output = io.StringIO()
        csv_templates.write_template("expenditure", output)
        headers = next(csv.reader(io.StringIO(output.getvalue())))
        self.assertIn("source_locator", headers)
        self.assertIn("item_budget_current_amount", headers)
        self.assertIn("section_unused_amount", headers)


if __name__ == "__main__":
    unittest.main()
