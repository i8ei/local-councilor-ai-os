"""Tests for normalized CSV ingestion into settlement review DB."""

from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import ingest_csv  # noqa: E402
import verify_totals  # noqa: E402

COMMON = {
    "fiscal_year": "2099",
    "account_name": "例会計",
    "raw_value": "fixture raw",
    "unit": "円",
    "as_of": "2099年度",
    "definition": "fixture definition",
    "source_name": "例決算書",
    "source_url": "https://example.invalid/settlement.pdf",
    "source_locator": '{"page":1}',
    "fetched_at": "2099-01-01T00:00:00Z",
    "verification_state": "verified",
    "print_page": "1",
    "pdf_page": "1",
}


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class SettlementCsvIngestTests(unittest.TestCase):
    def test_ingest_three_csvs_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "settlement.db"
            summary = root / "summary.csv"
            revenue = root / "revenue.csv"
            expenditure = root / "expenditure.csv"
            write_csv(summary, [
                {**COMMON, "side": "revenue", "kan_code": "1", "kan_name": "歳入款", "budget_current_amount": "100", "collected_amount": "80", "uncollectible_amount": "5", "outstanding_amount": "15"},
                {**COMMON, "side": "expenditure", "kan_code": "1", "kan_name": "歳出款", "budget_current_amount": "100", "spent_amount": "70", "carryover_amount": "10", "unused_amount": "20"},
            ])
            write_csv(revenue, [
                {**COMMON, "kan_code": "1", "kan_name": "歳入款", "ko_code": "1", "ko_name": "歳入項1", "budget_current_amount": "60", "collected_amount": "50", "uncollectible_amount": "2", "outstanding_amount": "8"},
                {**COMMON, "kan_code": "1", "kan_name": "歳入款", "ko_code": "2", "ko_name": "歳入項2", "budget_current_amount": "40", "collected_amount": "30", "uncollectible_amount": "3", "outstanding_amount": "7"},
            ])
            write_csv(expenditure, [
                {**COMMON, "kan_code": "1", "kan_name": "歳出款", "ko_code": "1", "ko_name": "歳出項", "moku_code": "1", "moku_name": "歳出目1", "setsu_code": "1", "setsu_name": "歳出節1", "item_budget_current_amount": "60", "item_spent_amount": "40", "item_carryover_amount": "5", "item_unused_amount": "15", "section_budget_current_amount": "30", "section_spent_amount": "25", "section_carryover_amount": "5", "section_unused_amount": "0"},
                {**COMMON, "kan_code": "1", "kan_name": "歳出款", "ko_code": "1", "ko_name": "歳出項", "moku_code": "1", "moku_name": "歳出目1", "setsu_code": "2", "setsu_name": "歳出節2", "item_budget_current_amount": "60", "item_spent_amount": "40", "item_carryover_amount": "5", "item_unused_amount": "15", "section_budget_current_amount": "30", "section_spent_amount": "15", "section_carryover_amount": "0", "section_unused_amount": "15"},
                {**COMMON, "kan_code": "1", "kan_name": "歳出款", "ko_code": "1", "ko_name": "歳出項", "moku_code": "2", "moku_name": "歳出目2", "setsu_code": "1", "setsu_name": "歳出節1", "item_budget_current_amount": "40", "item_spent_amount": "30", "item_carryover_amount": "5", "item_unused_amount": "5", "section_budget_current_amount": "40", "section_spent_amount": "30", "section_carryover_amount": "5", "section_unused_amount": "5"},
            ])
            self.assertEqual(2, ingest_csv.ingest_csv("summary", summary, db)["rows_loaded"])
            self.assertEqual(2, ingest_csv.ingest_csv("revenue", revenue, db)["rows_loaded"])
            self.assertEqual(3, ingest_csv.ingest_csv("expenditure", expenditure, db)["rows_loaded"])
            self.assertEqual(0, verify_totals.verify(db))
            with sqlite3.connect(db) as connection:
                self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])


    def test_missing_required_field_is_rejected(self) -> None:
        # A required summary column left blank must stop ingestion, never
        # silently insert a partial row.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "settlement.db"
            summary = root / "summary.csv"
            broken = {**COMMON, "side": "revenue", "kan_code": "1",
                      "kan_name": "", "budget_current_amount": "100",
                      "collected_amount": "80", "uncollectible_amount": "5",
                      "outstanding_amount": "15"}
            write_csv(summary, [broken])
            with self.assertRaises(ValueError):
                ingest_csv.ingest_csv("summary", summary, db)

    def test_verify_totals_flags_a_mismatch(self) -> None:
        # Detail rows that do not reconcile to the summary must fail the gate.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "settlement.db"
            summary = root / "summary.csv"
            revenue = root / "revenue.csv"
            write_csv(summary, [
                {**COMMON, "side": "revenue", "kan_code": "1",
                 "kan_name": "歳入款", "budget_current_amount": "100",
                 "collected_amount": "80", "uncollectible_amount": "5",
                 "outstanding_amount": "15"},
            ])
            write_csv(revenue, [
                {**COMMON, "kan_code": "1", "kan_name": "歳入款",
                 "ko_code": "1", "ko_name": "歳入項1",
                 "budget_current_amount": "60", "collected_amount": "50",
                 "uncollectible_amount": "2", "outstanding_amount": "8"},
                {**COMMON, "kan_code": "1", "kan_name": "歳入款",
                 "ko_code": "2", "ko_name": "歳入項2",
                 "budget_current_amount": "40", "collected_amount": "31",
                 "uncollectible_amount": "3", "outstanding_amount": "7"},
            ])
            ingest_csv.ingest_csv("summary", summary, db)
            ingest_csv.ingest_csv("revenue", revenue, db)
            self.assertNotEqual(0, verify_totals.verify(db))


if __name__ == "__main__":
    unittest.main()
