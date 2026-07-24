"""Tests for official index parsing and source joins."""

from __future__ import annotations

import unittest
from pathlib import Path

from bootstrap.cli.http import FetchResult
from bootstrap.municipalities.update import (
    _code_rows,
    _home_rows,
    build_records,
)


def page(body: str, url: str = "https://www.j-lis.go.jp/example") -> FetchResult:
    """Build an in-memory HTML response."""

    return FetchResult(
        url=url,
        final_url=url,
        body=body.encode(),
        fetched_at="2026-07-24T00:00:00Z",
        content_type="text/html",
        encoding="utf-8",
        cache_path=Path("/tmp/unused-registry-fixture"),
        sha256="fixture",
        from_cache=True,
    )


class MunicipalityRegistryUpdateTests(unittest.TestCase):
    def test_jlis_table_parsers_keep_code_name_and_external_home(self) -> None:
        code_page = page(
            """
            <table>
              <tr><th>団体コード</th><th>団体名</th></tr>
              <tr><td>043028</td><td>七ケ宿町</td></tr>
            </table>
            """
        )
        home_page = page(
            """
            <table>
              <tr>
                <td><a href="https://town.example.jp/">七ケ宿町</a></td>
                <td>紹介</td>
              </tr>
            </table>
            """
        )
        self.assertEqual(
            {"04302": ("043028", "七ケ宿町")},
            _code_rows(code_page),
        )
        self.assertEqual(
            {"七ケ宿町": "https://town.example.jp/"},
            _home_rows(home_page),
        )

    def test_join_preserves_cross_source_name_variant_as_alias(self) -> None:
        records = build_records(
            prefectures={"04": "宮城県"},
            current_regions={
                "04302": {
                    "name": "七ヶ宿町",
                    "region_level": "12",
                    "valid_from": "1970-04",
                    "valid_to": "",
                }
            },
            code_pages={
                "04": (
                    "https://www.j-lis.go.jp/code",
                    {"04302": ("043028", "七ケ宿町")},
                )
            },
            home_pages={
                "04": [
                    (
                        "https://www.j-lis.go.jp/map",
                        {"七ケ宿町": "https://town.example.jp/"},
                    )
                ]
            },
        )
        self.assertEqual(1, len(records))
        self.assertEqual("七ヶ宿町", records[0]["municipality_name"])
        self.assertEqual("七ケ宿町", records[0]["name_aliases"])
        self.assertEqual("043028", records[0]["local_government_code_6"])


if __name__ == "__main__":
    unittest.main()
