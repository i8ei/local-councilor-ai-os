"""Tests for lcaios.dashboard module."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lcaios.dashboard import (
    build_dashboard_report,
    find_candidate_databases,
    inspect_database,
    main,
    render_dashboard_markdown,
)


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

        # 1. Create a dummy minutes DB
        self.minutes_db = self.work_dir / "minutes.db"
        conn = sqlite3.connect(self.minutes_db)
        try:
            conn.execute("""
                CREATE TABLE meetings (
                    meeting_id TEXT PRIMARY KEY,
                    council_name TEXT,
                    meeting_name TEXT,
                    date TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE speeches (
                    speech_id TEXT PRIMARY KEY,
                    meeting_id TEXT,
                    speaker TEXT,
                    speaker_role TEXT,
                    text TEXT
                );
            """)
            conn.executemany(
                "INSERT INTO meetings VALUES (?, ?, ?, ?);",
                [
                    ("m1", "架空町議会", "第1回定例会", "2024-03-01"),
                    ("m2", "架空町議会", "第2回定例会", "2024-06-01"),
                    ("m3", "隣町議会", "第1回定例会", "2024-03-15"),
                ],
            )
            conn.executemany(
                "INSERT INTO speeches VALUES (?, ?, ?, ?, ?);",
                [
                    ("s1", "m1", "山田太郎", "町長", "答弁します"),
                    ("s2", "m1", "佐藤花子", "議員", "質問します"),
                    ("s3", "m2", "", "", "話者なし発言"),
                    ("s4", "m3", "鈴木一郎", "議員", "隣町の質問"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        # 2. Create a dummy regulations DB
        self.reg_db = self.work_dir / "regulations.db"
        conn = sqlite3.connect(self.reg_db)
        try:
            conn.execute("""
                CREATE TABLE regulation_documents (
                    document_id TEXT PRIMARY KEY,
                    title TEXT,
                    source_name TEXT,
                    fetched_at TEXT
                );
            """)
            conn.executemany(
                "INSERT INTO regulation_documents VALUES (?, ?, ?, ?);",
                [
                    ("r1", "架空町介護保険条例", "架空町例規集", "2026-08-01"),
                    ("r2", "架空町総合計画条例", "架空町例規集", "2026-08-01"),
                    ("r3", "隣町基本条例", "隣町例規集", "2026-08-02"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        # 3. Create a dummy settlement DB
        self.settlement_db = self.work_dir / "settlement.db"
        conn = sqlite3.connect(self.settlement_db)
        try:
            conn.execute("""
                CREATE TABLE summary (
                    id INTEGER PRIMARY KEY,
                    fiscal_year INTEGER,
                    revenue_settled INTEGER,
                    exp_settled INTEGER,
                    balance INTEGER
                );
            """)
            conn.executemany(
                "INSERT INTO summary VALUES (?, ?, ?, ?, ?);",
                [
                    (1, 2024, 1000000, 900000, 100000),
                    (2, 2025, 1100000, 950000, 150000),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        # 4. Create a dummy benchmark DB
        self.benchmark_db = self.work_dir / "benchmark.db"
        conn = sqlite3.connect(self.benchmark_db)
        try:
            conn.execute("CREATE TABLE municipality (code TEXT PRIMARY KEY);")
            conn.execute("CREATE TABLE indicator (id TEXT PRIMARY KEY);")
            conn.executemany(
                "INSERT INTO municipality VALUES (?);", [("41441",), ("41209",)]
            )
            conn.executemany(
                "INSERT INTO indicator VALUES (?);", [("ind1",), ("ind2",), ("ind3",)]
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_inspect_minutes_db(self) -> None:
        info = inspect_database(self.minutes_db)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["db_type"], "minutes")
        self.assertEqual(info["total_councils"], 2)
        self.assertEqual(info["total_meetings"], 3)
        self.assertEqual(info["total_speeches"], 4)

        councils = {c["council_name"]: c for c in info["councils"]}
        self.assertIn("架空町議会", councils)
        self.assertEqual(councils["架空町議会"]["meetings"], 2)
        self.assertEqual(councils["架空町議会"]["speeches"], 3)
        self.assertEqual(councils["架空町議会"]["period_start"], "2024-03-01")
        self.assertEqual(councils["架空町議会"]["period_end"], "2024-06-01")
        self.assertEqual(councils["架空町議会"]["no_speaker_percent"], 33.3)

    def test_inspect_regulations_db(self) -> None:
        info = inspect_database(self.reg_db)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["db_type"], "regulations")
        self.assertEqual(info["total_sources"], 2)
        self.assertEqual(info["total_documents"], 3)

    def test_inspect_settlement_db(self) -> None:
        info = inspect_database(self.settlement_db)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["db_type"], "settlement")
        self.assertEqual(len(info["records"]), 2)
        self.assertEqual(info["records"][0]["fiscal_year"], 2024)
        self.assertEqual(info["records"][0]["revenue_settled"], 1000000)

    def test_inspect_benchmark_db(self) -> None:
        info = inspect_database(self.benchmark_db)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["db_type"], "benchmark")
        self.assertEqual(info["municipalities_count"], 2)
        self.assertEqual(info["indicators_count"], 3)

    def test_find_candidate_databases(self) -> None:
        # Create databases that should be ignored
        trash_dir = self.work_dir / ".trash"
        trash_dir.mkdir()
        (trash_dir / "trashed.db").touch()

        cache_dir = self.work_dir / "cache"
        cache_dir.mkdir()
        (cache_dir / "cached.db").touch()

        (self.work_dir / "backup.bak.db").touch()
        (self.work_dir / "temp.tmp").touch()

        found = find_candidate_databases([self.work_dir])
        found_names = {p.name for p in found}
        self.assertEqual(
            found_names,
            {"minutes.db", "regulations.db", "settlement.db", "benchmark.db"},
        )

        # A search dir with 'tmp' in its own name must still discover DBs inside it
        sub_tmp_dir = self.work_dir / "tmp_data"
        sub_tmp_dir.mkdir()
        valid_db = sub_tmp_dir / "valid.db"
        valid_db.touch()
        found_in_tmp = find_candidate_databases([sub_tmp_dir])
        self.assertEqual({p.name for p in found_in_tmp}, {"valid.db"})

    def test_build_and_render_report(self) -> None:
        dbs = [self.minutes_db, self.reg_db, self.settlement_db, self.benchmark_db]
        report = build_dashboard_report(dbs, vault_path=self.work_dir)
        self.assertEqual(report["schema_version"], "lcaios-dashboard/1")
        self.assertEqual(len(report["databases"]), 4)

        md = render_dashboard_markdown(report)
        self.assertIn("# 🏛️ 自治体公開データ 見取り図（MOC）", md)
        self.assertIn("> [!summary] データ基盤サマリ", md)
        self.assertIn("架空町議会", md)
        self.assertIn("架空町例規集", md)
        self.assertIn("1,000,000 円", md)
        self.assertIn("2 自治体 / 3 指標収録", md)

    def test_main_cli_markdown_and_out(self) -> None:
        out_file = self.work_dir / "output.md"
        ret = main([
            "--db",
            str(self.minutes_db),
            str(self.reg_db),
            "--out",
            str(out_file),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue(out_file.is_file())
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("# 🏛️ 自治体公開データ 見取り図（MOC）", content)
        self.assertIn("架空町議会", content)

    def test_main_cli_json(self) -> None:
        out_file = self.work_dir / "output.json"
        ret = main([
            "--db",
            str(self.settlement_db),
            "--format",
            "json",
            "--out",
            str(out_file),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue(out_file.is_file())
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "lcaios-dashboard/1")
        self.assertEqual(len(data["databases"]), 1)

    def test_inspect_settlement_summary_from_real_fixture(self) -> None:
        """Aggregate the real settlement_review schema (settlement_summary)."""

        from modules.settlement_review.tests import create_fixtures

        create_fixtures.main()
        passing = (
            Path(__file__).resolve().parents[2]
            / "modules"
            / "settlement_review"
            / "tests"
            / "passing.db"
        )
        info = inspect_database(passing)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["db_type"], "settlement")
        # Fixture: revenue collected=80, expenditure spent=70 (fiscal 2099).
        by_year = {r["fiscal_year"]: r for r in info["records"]}
        rec = by_year[2099]
        self.assertEqual(rec["revenue_settled"], 80)
        self.assertEqual(rec["expenditure_settled"], 70)
        self.assertEqual(rec["balance"], 10)

    def test_build_report_skips_corrupt_database(self) -> None:
        """A corrupt / non-SQLite file must not abort the whole report."""

        corrupt = self.work_dir / "corrupt.db"
        corrupt.write_bytes(b"this is not a sqlite database at all" * 10)
        report = build_dashboard_report([self.settlement_db, corrupt])
        self.assertEqual(len(report["databases"]), 1)
        self.assertEqual(report["databases"][0]["db_type"], "settlement")

    def test_main_write_vault_without_vault_flag_errors(self) -> None:
        ret = main(["--write-vault"])
        self.assertEqual(ret, 2)


if __name__ == "__main__":
    unittest.main()
