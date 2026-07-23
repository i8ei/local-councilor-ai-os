"""Synthetic tests for benchmark DB construction and comparison."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
ROOT = MODULE_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_from_bootstrap  # noqa: E402
import compare  # noqa: E402
from bootstrap.cli.db import build_database  # noqa: E402


def make_bootstrap_db(path: Path, code: str, name: str, value: float) -> None:
    municipality = {
        "area_code_5": code,
        "local_government_code_6": code + "7" if len(code) == 5 else code,
        "name": name,
        "prefecture": "架空県",
        "prefecture_code_2": code[:2],
        "region_level": "12",
        "resolved_from": "fixture",
        "source_url": "https://example.invalid/regions",
        "resolved_at": "2026-07-23T00:00:00Z",
    }
    records = [{
        "indicator": "zaiseiryoku_shisuu",
        "value": value,
        "raw_value": str(value),
        "unit": "指数",
        "as_of": "2024年度",
        "definition": "fixture definition",
        "source_name": "fixture source",
        "source_url": "https://example.invalid/fiscal",
        "source_locator": {"cell": name},
        "fetched_at": "2026-07-23T00:00:00Z",
    }]
    build_database(municipality, records, {"fixture": True}, path)


class BenchmarkPipelineTests(unittest.TestCase):
    def test_build_and_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db1 = root / "a" / "municipality.db"
            db2 = root / "b" / "municipality.db"
            make_bootstrap_db(db1, "11111", "A町", 0.2)
            make_bootstrap_db(db2, "22222", "B町", 0.8)
            out = root / "benchmark.db"
            report = build_from_bootstrap.build([root], out)
            self.assertEqual("ok", report["integrity_check"])
            self.assertEqual(2, report["municipalities"])
            with sqlite3.connect(out) as connection:
                result = compare.compare(connection, "zaiseiryoku_shisuu", limit=10)
            self.assertEqual("2024年度", result["as_of"])
            self.assertEqual(["B町", "A町"], [item["name"] for item in result["items"]])
            self.assertIn("definition", result["items"][0])


    def test_missing_values_sort_last_without_becoming_zero(self) -> None:
        # A missing indicator value must stay null and rank after real values,
        # never be coerced to zero for comparison.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db1 = root / "a" / "municipality.db"
            db2 = root / "b" / "municipality.db"
            db3 = root / "c" / "municipality.db"
            make_bootstrap_db(db1, "11111", "A町", 0.2)
            make_bootstrap_db(db2, "22222", "B町", 0.8)
            make_bootstrap_db(db3, "33333", "C町", None)
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with sqlite3.connect(out) as connection:
                result = compare.compare(connection, "zaiseiryoku_shisuu", limit=10)
            names = [item["name"] for item in result["items"]]
            self.assertEqual(["B町", "A町", "C町"], names)
            self.assertIsNone(result["items"][-1]["value"])


if __name__ == "__main__":
    unittest.main()
