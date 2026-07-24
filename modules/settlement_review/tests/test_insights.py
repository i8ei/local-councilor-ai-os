"""Tests for settlement review candidate generation."""

from __future__ import annotations

import csv
import io
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path

from modules.settlement_review import csv_templates, insights
from modules.settlement_review.tests import create_fixtures

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]


class InsightTests(unittest.TestCase):
    def setUp(self) -> None:
        create_fixtures.main()
        self.passing = MODULE_DIR / "tests" / "passing.db"
        self.failing = MODULE_DIR / "tests" / "failing.db"

    def test_generate_review_candidates_from_verified_database(self) -> None:
        with closing(sqlite3.connect(self.passing)) as connection, connection:
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
            [
                sys.executable,
                "-m",
                "modules.settlement_review.insights",
                str(self.failing),
            ],
            cwd=REPO_ROOT,
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
