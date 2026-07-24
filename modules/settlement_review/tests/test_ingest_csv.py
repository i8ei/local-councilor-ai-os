"""Tests for normalized CSV ingestion into settlement review DB."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from modules.settlement_review import ingest_csv, verify_totals

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
            manifest_dir = root / "runs"
            with redirect_stdout(io.StringIO()):
                for index, (kind, source) in enumerate(
                    (
                        ("summary", summary),
                        ("revenue", revenue),
                        ("expenditure", expenditure),
                    ),
                    start=1,
                ):
                    self.assertEqual(
                        0,
                        ingest_csv.main(
                            [
                                kind,
                                str(source),
                                "--db",
                                str(db),
                                "--manifest-dir",
                                str(manifest_dir),
                                "--run-id",
                                f"settlement-ingest-{index}",
                            ]
                        ),
                    )
                self.assertEqual(
                    0,
                    verify_totals.main(
                        [
                            str(db),
                            "--manifest-dir",
                            str(manifest_dir),
                            "--run-id",
                            "settlement-verify",
                        ]
                    ),
                )
            verify_manifest = json.loads(
                (manifest_dir / "settlement-verify.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("succeeded", verify_manifest["status"])
            self.assertEqual(7, verify_manifest["coverage"]["rows"])
            self.assertEqual(
                "settlement_reconciliation",
                verify_manifest["checks"][1]["name"],
            )
            with closing(sqlite3.connect(db)) as connection, connection:
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

    def test_manifest_count_rejects_non_allowlisted_table(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            with self.assertRaisesRegex(ValueError, "Unsupported settlement table"):
                verify_totals._table_row_count(
                    connection,
                    "settlement_summary; DROP TABLE settlement_summary",
                )


if __name__ == "__main__":
    unittest.main()
