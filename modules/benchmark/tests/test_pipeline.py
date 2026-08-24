"""Synthetic tests for benchmark DB construction and comparison."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bootstrap.cli.db import build_database
from modules.benchmark import build_from_bootstrap, compare, presets

UNITS = {
    "zaiseiryoku_shisuu": "指数",
    "keijou_shuushi_hiritsu": "％",
    "jisshitsu_kousaihi_hiritsu": "％",
    "shourai_futan_hiritsu": "％",
    "total_revenue": "千円",
    "total_expenditure": "千円",
    "population_total": "人",
    "households_total": "世帯",
    "population_65_plus_ratio": "％",
}


def make_record(
    indicator: str,
    value: float | None,
    *,
    as_of: str = "2024年度",
    raw_value: str | None = None,
) -> dict[str, object]:
    return {
        "indicator": indicator,
        "value": value,
        "raw_value": raw_value if raw_value is not None else str(value),
        "unit": UNITS[indicator],
        "as_of": as_of,
        "definition": f"{indicator} fixture definition",
        "source_name": "fixture source",
        "source_url": "https://example.invalid/source",
        "source_locator": {"indicator": indicator},
        "fetched_at": "2026-07-23T00:00:00Z",
    }


def make_bootstrap_db_with_records(
    path: Path,
    code: str,
    name: str,
    records: list[dict[str, object]],
) -> None:
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
    build_database(municipality, records, {"fixture": True}, path)


def make_bootstrap_db(
    path: Path,
    code: str,
    name: str,
    value: float | None,
) -> None:
    make_bootstrap_db_with_records(
        path,
        code,
        name,
        [make_record("zaiseiryoku_shisuu", value)],
    )


def fiscal_health_records(
    *,
    as_of: str = "2024年度",
    shourai_value: float | None = 10.0,
    shourai_as_of: str | None = None,
) -> list[dict[str, object]]:
    return [
        make_record("zaiseiryoku_shisuu", 0.5, as_of=as_of),
        make_record("keijou_shuushi_hiritsu", 90.0, as_of=as_of),
        make_record("jisshitsu_kousaihi_hiritsu", 5.0, as_of=as_of),
        make_record(
            "shourai_futan_hiritsu",
            shourai_value,
            as_of=shourai_as_of or as_of,
            raw_value="-" if shourai_value is None else None,
        ),
    ]


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
            with closing(sqlite3.connect(out)) as connection, connection:
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
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare(connection, "zaiseiryoku_shisuu", limit=10)
            names = [item["name"] for item in result["items"]]
            self.assertEqual(["B町", "A町", "C町"], names)
            self.assertIsNone(result["items"][-1]["value"])

    def test_mixed_monetary_units_rank_normalized_not_raw(self) -> None:
        # mod-03: 甲町80億円（千円単位 8,000,000）と乙町60億円（円単位
        # 6,000,000,000）を並べる旧実装は乙町を1位にした。単位を共通スケールへ
        # 正規化して順位付けし、正規化したことを応答で宣言する。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for code, name, value, unit in (
                ("11111", "甲町", 8_000_000, "千円"),
                ("22222", "乙町", 6_000_000_000, "円"),
            ):
                records = [
                    dict(
                        make_record("total_revenue", value, raw_value=str(value)),
                        unit=unit,
                    )
                ]
                make_bootstrap_db_with_records(
                    root / code / "municipality.db", code, name, records
                )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare(connection, "total_revenue")
            names = [item["name"] for item in result["items"]]
            self.assertEqual(["甲町", "乙町"], names)  # 80億円 > 60億円
            self.assertEqual(
                "value_desc_nulls_last_unit_normalized", result["order"]
            )
            self.assertIsNotNone(result["unit_normalization"])
            self.assertEqual(["円", "千円"], result["units"])

    def test_unsortable_mixed_units_refuse_ranking(self) -> None:
        # mod-03: 指数と円が混在したらランキングを拒否し、行は保持・名称順。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for code, name, unit in (
                ("11111", "A町", "指数"),
                ("22222", "B町", "千円"),
            ):
                records = [
                    dict(
                        make_record("total_revenue", 100.0, raw_value="100"),
                        unit=unit,
                    )
                ]
                make_bootstrap_db_with_records(
                    root / code / "municipality.db", code, name, records
                )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare(connection, "total_revenue")
            self.assertEqual("unranked_unit_mismatch", result["order"])
            self.assertEqual(
                ["A町", "B町"],
                [item["name"] for item in result["items"]],
            )
            self.assertTrue(
                any("unit_mismatch" in m for m in result["missing_or_unresolved"])
            )

    def test_as_of_max_silent_drop_is_reported(self) -> None:
        # mod-04: --as-of未指定時はMAX(as_of)へ固定され、その時点を持たない
        # 自治体が黙って消える。名前を missing_or_unresolved に出す。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records_new = [make_record("total_revenue", 500.0, as_of="2024年度")]
            records_old = [make_record("total_revenue", 400.0, as_of="2023年度")]
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db", "11111", "A町", records_old
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db", "22222", "B町", records_new
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare(connection, "total_revenue")
            self.assertEqual("2024年度", result["as_of"])
            self.assertEqual(["B町"], [item["name"] for item in result["items"]])
            self.assertTrue(
                any("A町" in m and "latest available 2023年度" in m
                    for m in result["missing_or_unresolved"])
            )


class BenchmarkPresetTests(unittest.TestCase):
    def test_built_in_preset_declarations(self) -> None:
        self.assertEqual(
            {"zaisei_kenzensei", "kessan_gaiyou", "jinkou_kouzou"},
            set(presets.PRESETS),
        )
        self.assertEqual(
            "fiscal_year",
            presets.PRESETS["zaisei_kenzensei"].same_as_of_rule,
        )
        self.assertEqual(
            "census_date",
            presets.PRESETS["jinkou_kouzou"].same_as_of_rule,
        )
        for preset in presets.PRESETS.values():
            self.assertTrue(preset.caveats)
            self.assertEqual(
                set(preset.indicator_keys),
                set(preset.unit_notes),
            )

    def test_same_year_bundle_is_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db",
                "11111",
                "A町",
                fiscal_health_records(),
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db",
                "22222",
                "B町",
                fiscal_health_records(),
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare_preset(
                    connection,
                    "zaisei_kenzensei",
                    limit=10,
                )

            self.assertEqual("match", result["same_as_of_check"]["status"])
            self.assertTrue(result["bundle_comparison_allowed"])
            self.assertEqual(["2024年度"], result["same_as_of_check"]["observed"])
            self.assertEqual(
                list(presets.PRESETS["zaisei_kenzensei"].indicator_keys),
                result["preset_meta"]["indicator_keys"],
            )
            first_value = result["rows"][0]["values"]["zaiseiryoku_shisuu"]
            self.assertTrue(
                {"value", "as_of", "definition", "source"}
                <= set(first_value)
            )

    def test_as_of_mismatch_is_marked_without_dropping_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db",
                "11111",
                "A町",
                fiscal_health_records(shourai_as_of="2023年度"),
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db",
                "22222",
                "B町",
                fiscal_health_records(),
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare_preset(
                    connection,
                    "zaisei_kenzensei",
                    limit=10,
                )

            self.assertEqual("mismatch", result["same_as_of_check"]["status"])
            self.assertFalse(result["bundle_comparison_allowed"])
            self.assertEqual(2, result["row_count"])
            by_name = {row["name"]: row for row in result["rows"]}
            self.assertEqual(
                ["2023年度", "2024年度"],
                by_name["A町"]["same_as_of"]["observed"],
            )
            self.assertEqual(
                "mismatch",
                by_name["A町"]["same_as_of"]["status"],
            )
            self.assertEqual(
                10.0,
                by_name["A町"]["values"]["shourai_futan_hiritsu"]["value"],
            )

    def test_cross_municipality_vintages_mark_overall_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db",
                "11111",
                "A町",
                fiscal_health_records(as_of="2023年度"),
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db",
                "22222",
                "B町",
                fiscal_health_records(as_of="2024年度"),
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare_preset(
                    connection,
                    "zaisei_kenzensei",
                    limit=10,
                )

            self.assertEqual("mismatch", result["same_as_of_check"]["status"])
            self.assertEqual(
                {
                    "11111": ["2023年度"],
                    "22222": ["2024年度"],
                },
                result["same_as_of_check"]["as_of_by_area_code"],
            )
            self.assertEqual(
                {"match"},
                {row["same_as_of"]["status"] for row in result["rows"]},
            )
            self.assertEqual(2, result["row_count"])

    def test_null_stays_null_and_incomplete_row_sorts_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db",
                "11111",
                "A町",
                fiscal_health_records(),
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db",
                "22222",
                "B町",
                fiscal_health_records(shourai_value=None),
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare_preset(
                    connection,
                    "zaisei_kenzensei",
                    limit=10,
                )

            self.assertEqual(["A町", "B町"], [
                row["name"] for row in result["rows"]
            ])
            missing = result["rows"][-1]["values"]["shourai_futan_hiritsu"]
            self.assertIsNone(missing["value"])
            self.assertEqual("-", missing["raw_value"])
            self.assertFalse(result["rows"][-1]["bundle_complete"])

    def test_revenue_expenditure_balance_is_explicitly_derived(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_bootstrap_db_with_records(
                root / "a" / "municipality.db",
                "11111",
                "A町",
                [
                    make_record("total_revenue", 1200.0),
                    make_record("total_expenditure", 1000.0),
                ],
            )
            make_bootstrap_db_with_records(
                root / "b" / "municipality.db",
                "22222",
                "B町",
                [
                    make_record(
                        "total_revenue",
                        900.0,
                        as_of="2024年度",
                    ),
                    make_record(
                        "total_expenditure",
                        800.0,
                        as_of="2023年度",
                    ),
                ],
            )
            out = root / "benchmark.db"
            build_from_bootstrap.build([root], out)
            with closing(sqlite3.connect(out)) as connection, connection:
                result = compare.compare_preset(
                    connection,
                    "kessan_gaiyou",
                    limit=10,
                )

            by_name = {row["name"]: row for row in result["rows"]}
            derived = by_name["A町"]["values"][
                "revenue_expenditure_balance"
            ]
            self.assertEqual(200.0, derived["value"])
            self.assertEqual("2024年度", derived["as_of"])
            self.assertTrue(derived["derived"])
            self.assertIsNone(derived["source"])
            self.assertEqual("computed", derived["derivation"]["status"])
            self.assertEqual(
                "total_revenue - total_expenditure",
                derived["derivation"]["formula"],
            )
            refused = by_name["B町"]["values"][
                "revenue_expenditure_balance"
            ]
            self.assertIsNone(refused["value"])
            self.assertEqual(
                "as_of_mismatch",
                refused["derivation"]["reason"],
            )


if __name__ == "__main__":
    unittest.main()
