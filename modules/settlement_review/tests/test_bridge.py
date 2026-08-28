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
from modules.settlement_review.tests import create_fixtures

MODULE_DIR = Path(__file__).resolve().parents[1]


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

    def test_integration_on_real_settlement_schema_fixture(self) -> None:
        """The bridge must run end-to-end against the module's own real
        schema (schema.sql), not just the hand-rolled test schema."""

        create_fixtures.main()
        passing = MODULE_DIR / "tests" / "passing.db"

        # The real settlement_revenue/settlement_expenditure tables must be
        # detected (previously the wrong column requirement made the bridge
        # raise BridgeError on its own database).
        conn = sqlite3.connect(passing)
        try:
            val = validate_database(conn)
        finally:
            conn.close()
        self.assertEqual(val["exp_schema"]["table"], "settlement_expenditure")
        self.assertEqual(val["rev_schema"]["table"], "settlement_revenue")
        self.assertGreaterEqual(val["total_records"], 1)

        # With permissive thresholds the single-year fixture still surfaces
        # persistent unused, carryover, and uncollected revenue.
        report = run_bridge_analysis(
            passing,
            min_unused_years=1,
            min_unused_amount=1,
            min_unused_rate=0.0,
            min_carryover_years=1,
            min_outstanding_amount=1,
        )
        self.assertEqual(report["schema_version"], "lcaios-bridge/1")
        # 例目1 (budget 60, spent 40, unused 15) is flagged at the item grain.
        self.assertTrue(
            any("例目1" in item["moku_name"] for item in report["persistent_unused"])
        )
        self.assertTrue(report["carried_forward_chains"])
        self.assertTrue(
            all(row["amount_outstanding"] >= 1 for row in report["revenue_uncollected"])
        )

    def test_cli_returns_2_on_database_error(self) -> None:
        """A corrupt / unreadable database must exit 2, not traceback."""

        broken = self.work_dir / "broken.db"
        broken.write_bytes(b"this is not a sqlite database at all")
        ret = main(["--db", str(broken)])
        self.assertEqual(ret, 2)

    def test_ebpm_card_generation_and_minutes_matching(self) -> None:
        """The bridge can render structured EBPM cards and auto-match speeches from a minutes DB."""
        # 1. Create a dummy minutes DB with speeches
        min_db = self.work_dir / "minutes.db"
        conn = sqlite3.connect(min_db)
        try:
            conn.execute("""
                CREATE TABLE speeches (
                    speech_id TEXT PRIMARY KEY,
                    speaker TEXT,
                    speaker_role TEXT,
                    date TEXT,
                    meeting_name TEXT,
                    council_name TEXT,
                    text TEXT,
                    source_url TEXT,
                    locator TEXT,
                    fetched_at TEXT
                );
            """)
            conn.execute("""
                INSERT INTO speeches (speech_id, speaker, speaker_role, date, meeting_name, council_name, text, source_url, locator, fetched_at)
                VALUES ('s1', '総務課長', 'executive', '2025-09-10', '9月定例会', 'テスト町議会', '一般管理費の予算執行について、当初の見積もりが過大となっておりました。', '', '', '');
            """)
            conn.commit()
        finally:
            conn.close()

        # 2. Run CLI with --format ebpm-card and --minutes-db
        ebpm_dir = self.work_dir / "ebpm_out"
        ret = main([
            "--db", str(self.db_path),
            "--minutes-db", str(min_db),
            "--municipality", "テスト町",
            "--format", "ebpm-card",
            "--ebpm-out-dir", str(ebpm_dir),
        ])
        self.assertEqual(ret, 0)

        # Check generated card
        card_file = ebpm_dir / "ebpm-card-一般管理.md"
        self.assertTrue(card_file.is_file())
        card_content = card_file.read_text(encoding="utf-8")
        self.assertIn("EBPM質問・政策設計カード", card_content)
        self.assertIn("テスト町", card_content)
        self.assertIn("総務課長", card_content)
        self.assertIn("多年度決算推移", card_content)
        self.assertIn("ロジックモデル分析", card_content)
        self.assertIn("政策提言・質問項目", card_content)
    def test_ebpm_card_with_normalized_minutes_schema_and_preceding_context(self) -> None:
        """The bridge correctly matches executive speeches using the normalized minutes_db schema and preceding question context."""
        min_db = self.work_dir / "minutes_normalized.db"
        conn = sqlite3.connect(min_db)
        try:
            conn.execute("""
                CREATE TABLE meetings (
                    meeting_id TEXT PRIMARY KEY,
                    council_name TEXT NOT NULL,
                    meeting_name TEXT NOT NULL,
                    session TEXT,
                    date TEXT,
                    date_inferred INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT NOT NULL UNIQUE,
                    adapter TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE speeches (
                    speech_id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id),
                    seq INTEGER NOT NULL,
                    speaker TEXT,
                    speaker_role TEXT,
                    text TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    UNIQUE (meeting_id, seq)
                );
            """)
            conn.execute("""
                INSERT INTO meetings (meeting_id, council_name, meeting_name, date, source_url, adapter, fetched_at)
                VALUES ('m1', 'テスト町議会', '決算審査特別委員会', '2025-09-12', 'https://example.invalid/m1.pdf', 'static', '2026-08-28T21:00:00Z');
            """)
            # Question mentions "道路維持", response mentions "不用額" but omits "道路維持"
            conn.execute("""
                INSERT INTO speeches (speech_id, meeting_id, seq, speaker, speaker_role, text, locator)
                VALUES ('sp1', 'm1', 1, '山田議員', 'member', '決算書の道路維持費について不用額が生じていますが理由を伺います。', 'p1#l1');
            """)
            conn.execute("""
                INSERT INTO speeches (speech_id, meeting_id, seq, speaker, speaker_role, text, locator)
                VALUES ('sp2', 'm1', 2, '建設課長', 'executive', 'お答えします。入札不調および天候不順により事業執行が遅れ、残額が発生いたしました。', 'p1#l5');
            """)
            conn.commit()
        finally:
            conn.close()

        ebpm_dir = self.work_dir / "ebpm_out_normalized"
        ret = main([
            "--db", str(self.db_path),
            "--minutes-db", str(min_db),
            "--municipality", "テスト町",
            "--format", "ebpm-card",
            "--ebpm-out-dir", str(ebpm_dir),
        ])
        self.assertEqual(ret, 0)

        card_file = ebpm_dir / "ebpm-card-道路維持.md"
        self.assertTrue(card_file.is_file())
        card_content = card_file.read_text(encoding="utf-8")
        self.assertIn("建設課長", card_content)
        self.assertIn("入札不調および天候不順により事業執行が遅れ", card_content)

    def test_clean_speaker_and_meta_formatting(self) -> None:
        """The bridge cleans up unclosed speaker parentheses and trims metadata prefixes cleanly."""
        from modules.settlement_review.bridge import _clean_speaker
        self.assertEqual(_clean_speaker("税務課長（江口"), "税務課長（江口）")
        self.assertEqual(_clean_speaker("建設課長(山田"), "建設課長(山田)")
        self.assertEqual(_clean_speaker("町長"), "町長")
        self.assertEqual(_clean_speaker("総務課長（山田君）"), "総務課長（山田君）")


if __name__ == "__main__":
    unittest.main()

