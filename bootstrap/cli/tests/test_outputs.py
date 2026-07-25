"""Tests for SQLite integrity and a value-free authority map."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
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
        prepared_source = {
            "state": "source_prepared",
            "comparison": "manual",
            "secondary_source_name": "総務省 市町村決算カード",
            "secondary_source_url": "https://example.invalid/card-2024.html",
            "secondary_resolved_xlsx_url": (
                "https://example.invalid/card-2024.xlsx"
            ),
            "secondary_sha256": "card-sha256",
            "secondary_cache_path": "/cache/card-2024.xlsx",
            "secondary_fetched_at": "2026-01-02T03:04:05Z",
        }
        for record in records[3:]:
            record["source_locator"]["cross_check"] = dict(prepared_source)
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
            with closing(sqlite3.connect(database_path)) as connection, connection:
                integrity = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                count = connection.execute(
                    "SELECT count(*) FROM indicator"
                ).fetchone()[0]
                verification_states = {
                    row[0]
                    for row in connection.execute(
                        "SELECT verification_state FROM indicator"
                    ).fetchall()
                }
                fiscal_locator = connection.execute(
                    """
                    SELECT source_locator FROM indicator
                    WHERE indicator_key = 'zaiseiryoku_shisuu'
                    """
                ).fetchone()[0]
        self.assertEqual("ok", result["integrity_check"])
        self.assertEqual("ok", integrity)
        self.assertEqual(9, count)
        self.assertNotIn("raw_value:", content)
        self.assertNotIn("secret-value-", content)
        self.assertIn("sqlite_locator:", content)
        self.assertEqual({"verified_source_extraction"}, verification_states)
        self.assertIn('"state": "source_prepared"', fiscal_locator)
        self.assertIn('source_name: "総務省 市町村決算カード"', content)
        self.assertIn(
            'source_url: "https://example.invalid/card-2024.html"',
            content,
        )
        self.assertIn('sha256: "card-sha256"', content)
        self.assertIn('comparison: "manual"', content)
        self.assertIn('required_state: "verified"', content)
        self.assertNotIn("comparison_rule:", content)
        self.assertNotIn('required_state: "reconciled"', content)


if __name__ == "__main__":
    unittest.main()
