"""Tests for SQLite integrity and a value-free authority map."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bootstrap.cli.authority_map import generate_authority_map
from bootstrap.cli.db import build_database


class OutputTests(unittest.TestCase):
    def test_database_and_authority_routes(self) -> None:
        municipality = {
            "area_code_5": "12345",
            "local_government_code_6": "123457",
            "name": "架空町",
            "prefecture": "架空県",
            "prefecture_code_2": "12",
            "region_level": "12",
            "resolved_from": "fixture",
            "source_url": "https://example.invalid/regions",
            "resolved_at": "2026-01-02T03:04:05Z",
        }
        keys = [
            "population_total",
            "households_total",
            "population_65_plus_ratio",
            "zaiseiryoku_shisuu",
            "keijou_shuushi_hiritsu",
            "jisshitsu_kousaihi_hiritsu",
            "shourai_futan_hiritsu",
            "total_revenue",
            "total_expenditure",
        ]
        records = [
            {
                "indicator": key,
                "value": index + 0.5,
                "raw_value": f"secret-value-{index}",
                "unit": "unit",
                "as_of": "2024年度",
                "definition": "fixture definition",
                "source_name": "fixture source",
                "source_url": f"https://example.invalid/{key}",
                "source_locator": {"kind": "fixture"},
                "fetched_at": "2026-01-02T03:04:05Z",
            }
            for index, key in enumerate(keys)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "municipality.db"
            map_path = root / "authority_map.yaml"
            result = build_database(
                municipality, records, {"fixture": True}, database_path
            )
            generate_authority_map(
                municipality,
                records,
                map_path,
                database_name=database_path.name,
            )
            content = map_path.read_text(encoding="utf-8")
            with sqlite3.connect(database_path) as connection:
                integrity = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                count = connection.execute(
                    "SELECT count(*) FROM indicator"
                ).fetchone()[0]
        self.assertEqual("ok", result["integrity_check"])
        self.assertEqual("ok", integrity)
        self.assertEqual(9, count)
        self.assertNotIn("raw_value:", content)
        self.assertNotIn("secret-value-", content)
        self.assertIn("sqlite_locator:", content)


if __name__ == "__main__":
    unittest.main()
