"""Tests for budget review CSV ingestion, verification, and insights."""

from __future__ import annotations

import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import csv_templates  # noqa: E402
import ingest_csv  # noqa: E402
import insights  # noqa: E402
import verify_totals  # noqa: E402

COMMON = {
    "fiscal_year": "2099",
    "account_name": "一般会計",
    "budget_stage": "initial",
    "proposal_no": "",
    "raw_value": "fixture raw",
    "unit": "千円",
    "as_of": "2099年度当初予算",
    "definition": "fixture definition",
    "source_name": "例予算書",
    "source_url": "https://example.invalid/budget.pdf",
    "source_locator": '{"page":1}',
    "fetched_at": "2099-01-01T00:00:00Z",
    "verification_state": "verified",
    "print_page": "1",
    "pdf_page": "1",
}


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = csv_templates.FIELDS
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def total(side: str, amount: str) -> dict[str, str]:
    return {**COMMON, "side": side, "grain": "total", "current_year_amount": amount,
            "previous_year_amount": amount, "comparison_amount": "0"}


def kan(side: str, code: str, name: str, current: str, previous: str) -> dict[str, str]:
    return {**COMMON, "side": side, "grain": "kan", "kan_code": code,
            "kan_name": name, "current_year_amount": current,
            "previous_year_amount": previous,
            "comparison_amount": str(int(current) - int(previous))}


def ko(side: str, kan_code: str, ko_code: str, name: str, current: str, previous: str) -> dict[str, str]:
    return {**COMMON, "side": side, "grain": "ko", "kan_code": kan_code,
            "kan_name": f"{kan_code}款", "ko_code": ko_code, "ko_name": name,
            "current_year_amount": current, "previous_year_amount": previous,
            "comparison_amount": str(int(current) - int(previous))}


class BudgetPipelineTests(unittest.TestCase):
    def test_ingest_verify_and_insights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "budget.csv"
            db = root / "budget.db"
            rows = [
                total("revenue", "100"),
                total("expenditure", "100"),
                kan("revenue", "1", "町税", "100", "100"),
                ko("revenue", "1", "1", "町民税", "100", "100"),
                kan("expenditure", "2", "総務費", "100", "80"),
                ko("expenditure", "2", "1", "総務管理費", "100", "80"),
            ]
            write_csv(csv_path, rows)
            report = ingest_csv.ingest_csv(csv_path, db)
            self.assertEqual(6, report["rows_loaded"])
            self.assertEqual(0, verify_totals.verify(db))
            with sqlite3.connect(db) as connection:
                result = insights.generate_insights(connection, min_change_ratio=0.1)
            self.assertTrue(result["candidates"])
            self.assertIn("value", result["candidates"][0])

    def test_verification_fails_on_unbalanced_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "budget.csv"
            db = root / "budget.db"
            write_csv(csv_path, [total("revenue", "100"), total("expenditure", "90")])
            ingest_csv.ingest_csv(csv_path, db)
            self.assertNotEqual(0, verify_totals.verify(db))

    def test_template_contains_source_locator(self) -> None:
        output = io.StringIO()
        csv_templates.write_template(output)
        headers = next(csv.reader(io.StringIO(output.getvalue())))
        self.assertIn("source_locator", headers)
        self.assertIn("budget_stage", headers)
        self.assertIn("comparison_amount", headers)


if __name__ == "__main__":
    unittest.main()
