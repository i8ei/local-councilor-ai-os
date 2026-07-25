"""Tests for stdlib XLSX parsing and label-based fiscal extraction."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bootstrap.cli.fiscal import (
    CARD_INDEX,
    FISCAL_INDEX,
    FiscalError,
    _display_raw_value,
    _parse_number,
    discover_latest_fiscal_page,
    fetch_fiscal,
    parse_overview_xlsx,
)
from bootstrap.cli.http import FetchResult
from bootstrap.cli.tests.fixtures import build_inline_xlsx, build_minimal_xlsx
from bootstrap.cli.xlsx import (
    Cell,
    XlsxError,
    column_number,
    find_header_columns,
    read_workbook,
)

DISCOVERY = {
    "xlsx_url": "https://example.invalid/opaque.xlsx",
    "xlsx_sha256": "fixture-sha",
    "xlsx_fetched_at": "2026-01-02T03:04:05Z",
}


def _cell(ref: str, value: str) -> Cell:
    row = int("".join(char for char in ref if char.isdigit()))
    return Cell(
        ref=ref,
        row=row,
        column=column_number(ref),
        value=value,
    )


def _overview_cells(
    *,
    header_overrides: dict[str, str] | None = None,
    duplicate_code_row: bool = False,
) -> dict[str, str]:
    cells = {
        "A2": "団体コード",
        "B2": "団体名",
        "C2": "財政力指数",
        "D2": "経常収支比率（％）",
        "E2": "実質公債費比率（%）",
        "F2": "将来負担比率（％）",
        "G2": "歳入総額（千円）",
        "H2": "歳出総額（千円）",
        "A3": "123457",
        "B3": "架空町",
        "C3": "0.42",
        "D3": "88.1",
        "E3": "7.2",
        "F3": "-",
        "G3": "100000",
        "H3": "90000",
    }
    cells.update(header_overrides or {})
    if duplicate_code_row:
        cells["A4"] = "123457"
    return cells


def _parse_overview(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return parse_overview_xlsx(
        path,
        {
            "name": "架空町",
            "local_government_code_6": "123457",
        },
        2024,
        "https://example.invalid/fiscal-year.html",
        DISCOVERY,
    )


def _fetched_html(html: str) -> FetchResult:
    body = html.encode()
    return FetchResult(
        url=FISCAL_INDEX,
        final_url=FISCAL_INDEX,
        body=body,
        fetched_at="2026-01-02T03:04:05Z",
        content_type="text/html",
        encoding="utf-8",
        cache_path=Path("/tmp/unused-fiscal-fixture"),
        sha256=hashlib.sha256(body).hexdigest(),
        from_cache=True,
    )


class _FiscalIndexClient:
    def __init__(self, html: str) -> None:
        self.result = _fetched_html(html)

    def fetch(self, url: str, **_: object) -> FetchResult:
        if url != FISCAL_INDEX:
            raise AssertionError(f"unexpected fetch: {url}")
        return self.result


class _MappedFiscalClient:
    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.results = results

    def fetch(self, url: str, **_: object) -> FetchResult:
        try:
            return self.results[url]
        except KeyError as error:
            raise AssertionError(f"unexpected fetch: {url}") from error


def _fetch_result(
    url: str,
    body: bytes,
    content_type: str,
    cache_path: Path,
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        body=body,
        fetched_at="2026-01-02T03:04:05Z",
        content_type=content_type,
        encoding="utf-8",
        cache_path=cache_path,
        sha256=hashlib.sha256(body).hexdigest(),
        from_cache=True,
    )


class XlsxFiscalTests(unittest.TestCase):
    def test_raw_value_shortens_only_obvious_float_noise(self) -> None:
        self.assertEqual("0.3", _display_raw_value("0.30000000000000004"))
        self.assertEqual("15.4", _display_raw_value("15.399999999999999"))
        self.assertEqual(
            "1.2345678901234567",
            _display_raw_value("1.2345678901234567"),
        )
        self.assertEqual("1,234.50", _display_raw_value("1,234.50"))

    def test_shared_strings_and_header_alias_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            build_minimal_xlsx(path)
            workbook = read_workbook(path)
            self.assertEqual("概況", workbook[0].name)
            records, parse_info = parse_overview_xlsx(
                path,
                {
                    "name": "架空町",
                    "local_government_code_6": "123457",
                },
                2024,
                "https://example.invalid/fiscal-year.html",
                DISCOVERY,
            )
        self.assertEqual(6, len(records))
        self.assertTrue(
            parse_info["header_fallbacks"]["local_government_code_6"][
                "fallback_used"
            ]
        )
        future_burden = next(
            item
            for item in records
            if item["indicator"] == "shourai_futan_hiritsu"
        )
        self.assertIsNone(future_burden["value"])
        self.assertEqual("-", future_burden["raw_value"])

    def test_latest_fiscal_link_context_starts_after_previous_anchor(self) -> None:
        client = _FiscalIndexClient(
            "<h2>令和5年度</h2>"
            '<a href="/z/shichouson.html">市町村</a>'
            "<h2>令和6年度</h2>"
            '<a href="/a/shichouson.html">市町村</a>'
        )
        year, url, _, _ = discover_latest_fiscal_page(client)  # type: ignore[arg-type]
        self.assertEqual(2024, year)
        self.assertEqual(
            "https://www.soumu.go.jp/a/shichouson.html",
            url,
        )

    def test_latest_fiscal_page_rejects_distinct_urls_for_max_year(self) -> None:
        client = _FiscalIndexClient(
            "<h2>令和6年度</h2>"
            '<a href="/a/shichouson.html">市町村</a>'
            "<h2>令和6年度</h2>"
            '<a href="/b/shichouson.html">市町村</a>'
        )
        with self.assertRaises(FiscalError) as caught:
            discover_latest_fiscal_page(client)  # type: ignore[arg-type]
        message = str(caught.exception)
        self.assertIn("https://www.soumu.go.jp/a/shichouson.html", message)
        self.assertIn("https://www.soumu.go.jp/b/shichouson.html", message)

    def test_overview_rejects_code_found_on_multiple_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-sheets.xlsx"
            build_inline_xlsx(
                path,
                [
                    ("概況A", _overview_cells()),
                    ("概況B", _overview_cells()),
                ],
            )
            with self.assertRaisesRegex(FiscalError, "複数シート"):
                _parse_overview(path)

    def test_overview_propagates_duplicate_rows_in_one_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-rows.xlsx"
            build_inline_xlsx(
                path,
                [("概況", _overview_cells(duplicate_code_row=True))],
            )
            with self.assertRaises(FiscalError) as caught:
                _parse_overview(path)
        self.assertIn("複数行", str(caught.exception))
        self.assertNotIn("行がありません", str(caught.exception))

    def test_header_match_rejects_winning_alias_in_multiple_columns(self) -> None:
        cells = (
            _cell("C2", "実質公債費比率(単年度)"),
            _cell("D2", "実質公債費比率(3か年平均)"),
        )
        with self.assertRaises(XlsxError) as caught:
            find_header_columns(
                cells,
                {
                    "jisshitsu_kousaihi_hiritsu": (
                        "実質公債費比率",
                        "実質公債費比率（％）",
                    )
                },
            )
        message = str(caught.exception)
        self.assertIn("jisshitsu_kousaihi_hiritsu", message)
        self.assertIn("C2「実質公債費比率(単年度)」", message)
        self.assertIn("D2「実質公債費比率(3か年平均)」", message)

    def test_header_match_allows_stacked_cells_in_same_column(self) -> None:
        match = find_header_columns(
            (
                _cell("C2", "実質公債費比率"),
                _cell("C3", "実質公債費比率(3か年平均)"),
            ),
            {"ratio": ("実質公債費比率",)},
        )
        self.assertEqual(3, match["ratio"].column)
        self.assertEqual("C2", match["ratio"].cell.ref)

    def test_fetch_fiscal_runs_the_complete_card_cross_check_path(self) -> None:
        year_url = "https://example.invalid/fiscal-2024.html"
        overview_url = "https://example.invalid/overview.xlsx"
        card_year_url = "https://example.invalid/card-2024.html"
        card_xlsx_url = "https://example.invalid/card.xlsx"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overview_path = root / "overview.xlsx"
            card_path = root / "card.xlsx"
            build_inline_xlsx(
                overview_path,
                [
                    (
                        "概況",
                        _overview_cells(
                            header_overrides={"F3": "12.3", "H3": "900000"}
                        ),
                    )
                ],
            )
            build_inline_xlsx(
                card_path,
                [
                    (
                        "数値抽出対象ではない",
                        {"A1": "このブックの値は自動抽出しない"},
                    )
                ],
            )
            html_path = root / "unused.html"
            pages = {
                FISCAL_INDEX: _fetch_result(
                    FISCAL_INDEX,
                    (
                        f"<h2>令和6年度</h2>"
                        f'<a href="{year_url}">市町村</a>'
                    ).encode(),
                    "text/html",
                    html_path,
                ),
                year_url: _fetch_result(
                    year_url,
                    (
                        "<h2>町村</h2>"
                        f'<a href="{overview_url}">概況</a>'
                    ).encode(),
                    "text/html",
                    html_path,
                ),
                overview_url: _fetch_result(
                    overview_url,
                    overview_path.read_bytes(),
                    (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                    overview_path,
                ),
                CARD_INDEX: _fetch_result(
                    CARD_INDEX,
                    (
                        "<h2>令和6年度</h2>"
                        f'<a href="{card_year_url}">市町村決算カード</a>'
                    ).encode(),
                    "text/html",
                    html_path,
                ),
                card_year_url: _fetch_result(
                    card_year_url,
                    (
                        "<h2>架空県</h2>"
                        f'<a href="{card_xlsx_url}">決算カード</a>'
                    ).encode(),
                    "text/html",
                    html_path,
                ),
                card_xlsx_url: _fetch_result(
                    card_xlsx_url,
                    card_path.read_bytes(),
                    (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                    card_path,
                ),
            }
            result = fetch_fiscal(  # type: ignore[arg-type]
                {
                    "name": "架空町",
                    "prefecture": "架空県",
                    "region_level": "12",
                    "local_government_code_6": "123457",
                },
                _MappedFiscalClient(pages),
                cross_check=True,
            )
            card_sha256 = hashlib.sha256(card_path.read_bytes()).hexdigest()
            self.assertEqual(
                {
                    "status": "source_prepared",
                    "comparison": "manual",
                    "discovery": {
                        "index_url": CARD_INDEX,
                        "year_page_url": card_year_url,
                        "xlsx_url": card_xlsx_url,
                        "xlsx_sha256": card_sha256,
                        "xlsx_fetched_at": "2026-01-02T03:04:05Z",
                        "xlsx_content_type": (
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                        "year_page_label": "市町村決算カード",
                    },
                    "cache_path": str(card_path),
                },
                result["cross_check"],
            )
            self.assertTrue(Path(result["cross_check"]["cache_path"]).is_file())
            self.assertEqual(
                {
                    "zaiseiryoku_shisuu": 0.42,
                    "keijou_shuushi_hiritsu": 88.1,
                    "jisshitsu_kousaihi_hiritsu": 7.2,
                    "shourai_futan_hiritsu": 12.3,
                    "total_revenue": 100000,
                    "total_expenditure": 900000,
                },
                {
                    record["indicator"]: record["value"]
                    for record in result["records"]
                },
            )
            expected_locator = {
                "state": "source_prepared",
                "comparison": "manual",
                "secondary_source_name": "総務省 市町村決算カード",
                "secondary_source_url": card_year_url,
                "secondary_resolved_xlsx_url": card_xlsx_url,
                "secondary_sha256": card_sha256,
                "secondary_cache_path": str(card_path),
                "secondary_fetched_at": "2026-01-02T03:04:05Z",
            }
            for record in result["records"]:
                self.assertEqual(
                    expected_locator,
                    record["source_locator"]["cross_check"],
                )
                self.assertNotIn(
                    "secondary_raw_value",
                    record["source_locator"]["cross_check"],
                )
                self.assertNotIn(
                    "comparison_rule",
                    record["source_locator"]["cross_check"],
                )
            self.assertEqual(
                [
                    "決算カードXLSXを手動検証用に準備しました。"
                    "値は自動で突合していません。"
                ],
                result["warnings"],
            )

    def test_parse_number_accepts_only_well_formed_finite_numbers(self) -> None:
        valid = {
            "0": 0,
            "+12.5": 12.5,
            "1,234": 1234,
            "-1,234.50": -1234.5,
            "12％": 12,
        }
        for raw, expected in valid.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, _parse_number(raw))
        for raw in ("NaN", "inf", "-inf", "1,2", "12,34", "9" * 400):
            with self.subTest(raw=raw):
                with self.assertRaises(FiscalError):
                    _parse_number(raw)

    def test_parse_number_keeps_missing_markers(self) -> None:
        for raw in ("", "-", "―", "－", "***", "*", "…"):
            with self.subTest(raw=raw):
                self.assertIsNone(_parse_number(raw))

    def test_overview_rejects_header_unit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-unit.xlsx"
            build_inline_xlsx(
                path,
                [
                    (
                        "概況",
                        _overview_cells(
                            header_overrides={"G2": "歳入総額（百万円）"}
                        ),
                    )
                ],
            )
            with self.assertRaises(FiscalError) as caught:
                _parse_overview(path)
        message = str(caught.exception)
        self.assertIn("歳入総額", message)
        self.assertIn("百万円", message)
        self.assertIn("千円", message)

    def test_overview_accepts_matching_percent_header_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matching-units.xlsx"
            build_inline_xlsx(path, [("概況", _overview_cells())])
            records, _ = _parse_overview(path)
        self.assertEqual(6, len(records))


if __name__ == "__main__":
    unittest.main()
