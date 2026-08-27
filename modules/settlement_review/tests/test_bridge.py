"""Tests for modules.settlement_review.bridge."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from modules.settlement_review.bridge import (
    BridgeError,
    main,
    render_bridge_markdown,
    run_bridge_analysis,
    validate_database,
)


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

        # 1. Create multi-year settlement DB
        self.db_path = self.work_dir / "settlement_multi.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE expenditure (
                    id INTEGER PRIMARY KEY,
                    fiscal_year INTEGER,
                    kuan_no INTEGER,
                    kuan TEXT,
                    kou_no INTEGER,
                    kou TEXT,
                    moku_no INTEGER,
                    moku TEXT,
                    amount_budget_final INTEGER,
                    amount_spent INTEGER,
                    amount_carried_forward INTEGER,
                    amount_unused INTEGER
                );
            """)
            conn.execute("""
                CREATE TABLE revenue (
                    id INTEGER PRIMARY KEY,
                    fiscal_year INTEGER,
                    kuan_no INTEGER,
                    kuan TEXT,
                    kou_no INTEGER,
                    kou TEXT,
                    amount_budget INTEGER,
                    amount_settled INTEGER,
                    amount_collected INTEGER,
                    amount_uncollectible INTEGER,
                    amount_outstanding INTEGER
                );
            """)

            # Expenditure rows
            # Moku A: 2024 & 2025 persistent unused (budget 10M, spent 7M, unused 3M -> 30%)
            # Moku B: 2024 only unused (budget 10M, spent 9.5M, unused 0.5M)
            # Moku C: 2024 & 2025 consecutive carryover (carried 2M)
            conn.executemany(
                """INSERT INTO expenditure (
                    fiscal_year, kuan_no, kuan, kou_no, kou, moku_no, moku,
                    amount_budget_final, amount_spent, amount_carried_forward, amount_unused
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                [
                    (2024, 2, "総務費", 1, "総務管理費", 1, "一般管理費", 10000000, 7000000, 0, 3000000),
                    (2025, 2, "総務費", 1, "総務管理費", 1, "一般管理費", 10000000, 7000000, 0, 3000000),
                    (2024, 3, "民生費", 1, "社会福祉費", 1, "社会福祉総務費", 10000000, 9500000, 0, 500000),
                    (2025, 3, "民生費", 1, "社会福祉費", 1, "社会福祉総務費", 10000000, 9500000, 0, 500000),
                    (2024, 8, "土木費", 2, "道路橋梁費", 1, "道路維持費", 10000000, 5000000, 2000000, 3000000),
                    (2025, 8, "土木費", 2, "道路橋梁費", 1, "道路維持費", 10000000, 6000000, 2000000, 2000000),
                ],
            )

            # Revenue rows
            conn.executemany(
                """INSERT INTO revenue (
                    fiscal_year, kuan_no, kuan, kou_no, kou,
                    amount_budget, amount_settled, amount_collected,
                    amount_uncollectible, amount_outstanding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                [
                    (2024, 1, "町税", 1, "町民税", 50000000, 52000000, 50000000, 100000, 1900000),
                    (2025, 1, "町税", 1, "町民税", 50000000, 51000000, 48000000, 200000, 2800000),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validate_database_success(self) -> None:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            val = validate_database(conn)
            self.assertEqual(val["total_records"], 6)
            self.assertEqual(val["fiscal_years_count"], 2)
            self.assertEqual(val["exp_schema"]["table"], "expenditure")
        finally:
            conn.close()

    def test_validate_database_fails_when_empty_or_missing_table(self) -> None:
        empty_db = self.work_dir / "empty.db"
        conn = sqlite3.connect(empty_db)
        try:
            conn.execute("CREATE TABLE dummy (id INT);")
            conn.commit()
        finally:
            conn.close()

        conn_ro = sqlite3.connect(f"file:{empty_db}?mode=ro", uri=True)
        try:
            with self.assertRaises(BridgeError):
                validate_database(conn_ro)
        finally:
            conn_ro.close()

    def test_run_bridge_analysis_detects_persistent_unused(self) -> None:
        report = run_bridge_analysis(
            self.db_path,
            min_unused_years=2,
            min_unused_amount=1_000_000,
            min_unused_rate=0.15,
        )
        self.assertEqual(report["schema_version"], "lcaios-bridge/1")
        self.assertEqual(report["fiscal_years_count"], 2)

        # Should find 一般管理費 and 道路維持費 (both 2 years with >= 15% and >= 1M unused)
        # Social welfare (500k unused) should be excluded
        unused_names = [item["moku_name"] for item in report["persistent_unused"]]
        self.assertTrue(any("一般管理費" in name for name in unused_names))
        self.assertTrue(any("道路維持費" in name for name in unused_names))
        self.assertFalse(any("社会福祉総務費" in name for name in unused_names))

    def test_run_bridge_analysis_detects_carryover_chains(self) -> None:
        report = run_bridge_analysis(self.db_path, min_carryover_years=2)
        chains = report["carried_forward_chains"]
        self.assertEqual(len(chains), 1)
        self.assertIn("道路維持費", chains[0]["moku_name"])
        self.assertEqual(chains[0]["carried_years_count"], 2)

    def test_run_bridge_analysis_detects_revenue_uncollected(self) -> None:
        report = run_bridge_analysis(self.db_path, min_outstanding_amount=1_500_000)
        issues = report["revenue_uncollected"]
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["fiscal_year"], 2025)
        self.assertEqual(issues[0]["amount_outstanding"], 2800000)

    def test_render_bridge_markdown(self) -> None:
        report = run_bridge_analysis(self.db_path)
        md = render_bridge_markdown(report)
        self.assertIn("# 📊 予算決算 多年度ブリッジ分析レポート", md)
        self.assertIn("不用額が常態化している事業", md)
        self.assertIn("一般管理費", md)
        self.assertIn("道路維持費", md)
        self.assertIn("町民税", md)

    def test_cli_execution_markdown_and_json(self) -> None:
        out_file = self.work_dir / "report.md"
        ret = main(["--db", str(self.db_path), "--out", str(out_file)])
        self.assertEqual(ret, 0)
        self.assertTrue(out_file.is_file())
        self.assertIn("# 📊 予算決算 多年度ブリッジ分析レポート", out_file.read_text(encoding="utf-8"))

        out_json = self.work_dir / "report.json"
        ret_json = main(["--db", str(self.db_path), "--format", "json", "--out", str(out_json)])
        self.assertEqual(ret_json, 0)
        self.assertTrue(out_json.is_file())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "lcaios-bridge/1")


if __name__ == "__main__":
    unittest.main()
