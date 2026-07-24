"""Tier 1b fiscal discovery, overview-XLSX extraction, and card cross-check."""

from __future__ import annotations

import html.parser
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .http import FetchError, FetchResult, HttpClient
from .xlsx import (
    Cell,
    HeaderMatch,
    Worksheet,
    XlsxError,
    find_header_columns,
    normalize_label,
    read_workbook,
)

FISCAL_INDEX = "https://www.soumu.go.jp/iken/kessan_jokyo_2.html"
CARD_INDEX = "https://www.soumu.go.jp/iken/zaisei/card.html"

INDICATORS: dict[str, dict[str, Any]] = {
    "zaiseiryoku_shisuu": {
        "label": "財政力指数",
        "aliases": ("財政力指数",),
        "unit": "index",
        "definition": "基準財政収入額を基準財政需要額で除した数値の直近3か年平均。",
    },
    "keijou_shuushi_hiritsu": {
        "label": "経常収支比率",
        "aliases": ("経常収支比率", "経常収支比率（％）"),
        "unit": "％",
        "definition": (
            "経常一般財源等が人件費・扶助費・公債費等の経常経費に"
            "充当された割合。"
        ),
    },
    "jisshitsu_kousaihi_hiritsu": {
        "label": "実質公債費比率",
        "aliases": ("実質公債費比率", "実質公債費比率（％）"),
        "unit": "％",
        "definition": (
            "一般会計等が負担する元利償還金等の標準財政規模に対する比率。"
        ),
    },
    "shourai_futan_hiritsu": {
        "label": "将来負担比率",
        "aliases": ("将来負担比率", "将来負担比率（％）"),
        "unit": "％",
        "definition": (
            "一般会計等が将来負担すべき実質的な負債の標準財政規模に"
            "対する比率。"
        ),
    },
    "total_revenue": {
        "label": "歳入総額",
        "aliases": ("歳入総額",),
        "unit": "千円",
        "definition": "地方財政状況調査の普通会計における歳入決算総額。",
    },
    "total_expenditure": {
        "label": "歳出総額",
        "aliases": ("歳出総額",),
        "unit": "千円",
        "definition": "地方財政状況調査の普通会計における歳出決算総額。",
    },
}


class FiscalError(RuntimeError):
    """Raised when the primary fiscal source cannot be discovered or parsed."""


@dataclass(frozen=True)
class Link:
    """A discovered HTML link with nearby visible context."""

    url: str
    text: str
    context: str


class _LinkParser(html.parser.HTMLParser):
    """Collect links and a bounded amount of preceding visible text."""

    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.links: list[Link] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._visible: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        self._visible.append(text)
        self._visible = self._visible[-30:]
        if self._href is not None:
            self._anchor_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join(self._anchor_text).strip()
        context = " ".join(self._visible[-20:])
        self.links.append(
            Link(urllib.parse.urljoin(self.page_url, self._href), text, context)
        )
        self._href = None
        self._anchor_text = []


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value or ""))


def _links(page_url: str, result: FetchResult) -> list[Link]:
    parser = _LinkParser(page_url)
    parser.feed(result.text())
    return parser.links


def _fiscal_year(value: str) -> int | None:
    normalized = _normalize(value)
    match = re.search(r"令和(元|\d+)年度", normalized)
    if match:
        era_year = 1 if match.group(1) == "元" else int(match.group(1))
        return 2018 + era_year
    match = re.search(r"平成(元|\d+)年度", normalized)
    if match:
        era_year = 1 if match.group(1) == "元" else int(match.group(1))
        return 1988 + era_year
    match = re.search(r"(20\d{2})年度", normalized)
    return int(match.group(1)) if match else None


def _fetch_html(client: HttpClient, url: str, cache_label: str) -> FetchResult:
    try:
        result = client.fetch(url, cache_key=f"soumu:html:{cache_label}:{url}")
    except FetchError as error:
        raise FiscalError(f"総務省ページを取得できません: {url}: {error}") from error
    if "html" not in result.content_type and b"<html" not in result.body[:4096].lower():
        raise FiscalError(
            f"HTMLとして検証できない応答です: {url} ({result.content_type})"
        )
    return result


def _validate_xlsx_result(result: FetchResult, source_url: str) -> None:
    accepted_content_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/octet-stream",
        "application/zip",
        "binary/octet-stream",
    }
    if result.content_type not in accepted_content_types:
        raise FiscalError(
            f"XLSXとして認めないContent-Typeです: "
            f"{source_url} ({result.content_type})"
        )
    if not result.body.startswith(b"PK\x03\x04"):
        raise FiscalError(
            f"XLSXシグネチャがありません: {source_url} "
            f"({result.content_type})"
        )


def discover_latest_fiscal_page(
    client: HttpClient,
) -> tuple[int, str, str, str]:
    """Discover the latest municipality fiscal-year page from the stable index."""

    result = _fetch_html(client, FISCAL_INDEX, "fiscal-index")
    candidates: list[tuple[int, str, str]] = []
    for link in _links(FISCAL_INDEX, result):
        combined = link.text + " " + link.context
        year = _fiscal_year(combined)
        path = urllib.parse.urlsplit(link.url).path.lower()
        semantic_match = "市町村" in combined or "shichouson" in path
        if (
            year is not None
            and semantic_match
            and path.endswith((".html", ".htm"))
            and link.url != FISCAL_INDEX
        ):
            candidates.append((year, link.url, link.text))
    if not candidates:
        raise FiscalError("市町村別決算状況調の索引から年度ページを発見できません")
    year, url, label = max(candidates, key=lambda item: (item[0], item[1]))
    return year, url, label, result.fetched_at


def _municipality_kind(region_level: str) -> str:
    if region_level in {"8", "9", "10"}:
        return "都市"
    if region_level in {"12", "13"}:
        return "町村"
    raise FiscalError(f"財政ファイル区分を判定できない地域レベルです: {region_level}")


def discover_overview_xlsx(
    client: HttpClient,
    page_url: str,
    fiscal_year: int,
    region_level: str,
) -> tuple[FetchResult, dict[str, Any]]:
    """Discover the city/town overview workbook from a fiscal-year page."""

    page = _fetch_html(client, page_url, f"fiscal-year:{fiscal_year}")
    kind = _municipality_kind(region_level)
    xlsx_links = [
        link
        for link in _links(page_url, page)
        if urllib.parse.urlsplit(link.url).path.lower().endswith(".xlsx")
    ]
    overview = [
        link for link in xlsx_links if "概況" in _normalize(link.text + link.context)
    ]
    other_kind = "町村" if kind == "都市" else "都市"
    strict: list[Link] = []
    for link in overview:
        context = _normalize(link.text + link.context)
        kind_position = context.rfind(kind)
        other_position = context.rfind(other_kind)
        if kind_position >= 0 and kind_position > other_position:
            strict.append(link)
    fallback_used = False
    if strict:
        chosen = strict[0]
    elif len(overview) == 1:
        chosen = overview[0]
        fallback_used = True
    else:
        raise FiscalError(
            f"{fiscal_year}年度ページから{kind}の概況XLSXを一意に発見できません"
        )
    try:
        workbook = client.fetch(
            chosen.url,
            cache_key=f"soumu:xlsx:fiscal:{fiscal_year}:{kind}:{chosen.url}",
        )
    except FetchError as error:
        raise FiscalError(f"概況XLSXを取得できません: {error}") from error
    _validate_xlsx_result(workbook, chosen.url)
    return workbook, {
        "index_url": FISCAL_INDEX,
        "year_page_url": page_url,
        "year_page_fetched_at": page.fetched_at,
        "xlsx_url": workbook.final_url,
        "xlsx_sha256": workbook.sha256,
        "xlsx_fetched_at": workbook.fetched_at,
        "xlsx_content_type": workbook.content_type,
        "kind": kind,
        "selection_fallback_used": fallback_used,
        "link_text": chosen.text,
        "link_context": chosen.context,
    }


def _normalize_code(value: str) -> str | None:
    cleaned = value.strip().replace(",", "")
    if cleaned.endswith(".0"):
        cleaned = cleaned[:-2]
    if not cleaned.isdigit() or len(cleaned) > 6:
        return None
    return cleaned.zfill(6)


def _parse_number(raw: str) -> int | float | None:
    cleaned = (
        raw.strip()
        .replace(",", "")
        .replace("％", "")
        .replace("%", "")
    )
    if cleaned in {"", "-", "―", "－", "***", "*", "…"}:
        return None
    try:
        number = float(cleaned)
    except ValueError as error:
        raise FiscalError(f"財政値を数値として解釈できません: {raw!r}") from error
    return int(number) if number.is_integer() else number


def _display_raw_value(raw: str) -> str:
    """Shorten obvious binary-float tails while leaving source text unchanged."""

    stripped = raw.strip()
    match = re.fullmatch(r"([+-]?\d+)\.(\d{12,})", stripped)
    if not match or not re.search(r"(?:0{6,}|9{6,})\d?$", match.group(2)):
        return raw
    concise = format(float(stripped), ".12g")
    return concise if len(concise) < len(stripped) else raw


def _header_spec() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {
        "local_government_code_6": (
            "団体コード",
            "地方公共団体コード",
            "市町村コード",
        ),
        "municipality_name": ("団体名", "市町村名", "市町村"),
    }
    result.update(
        {
            indicator: tuple(spec["aliases"])
            for indicator, spec in INDICATORS.items()
        }
    )
    return result


def _find_primary_row(
    sheet: Worksheet,
    headers: dict[str, HeaderMatch],
    code: str,
) -> int | None:
    header_bottom = max(match.cell.row for match in headers.values())
    code_column = headers["local_government_code_6"].column
    matches = [
        cell.row
        for cell in sheet.cells
        if cell.row > header_bottom
        and cell.column == code_column
        and _normalize_code(cell.value) == code
    ]
    unique = sorted(set(matches))
    if len(unique) > 1:
        raise FiscalError(
            f"概況XLSXの団体コード {code} が複数行にあります: {sheet.name}"
        )
    return unique[0] if unique else None


def parse_overview_xlsx(
    xlsx_path: Any,
    municipality: dict[str, Any],
    fiscal_year: int,
    source_page_url: str,
    discovery: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract six indicators by labels and one six-digit code row."""

    try:
        sheets = read_workbook(xlsx_path)
    except XlsxError as error:
        raise FiscalError(str(error)) from error
    code = str(municipality["local_government_code_6"])
    selected: tuple[Worksheet, dict[str, HeaderMatch], int] | None = None
    errors: list[str] = []
    for sheet in sheets:
        try:
            headers = find_header_columns(sheet.cells, _header_spec(), max_row=200)
            row_number = _find_primary_row(sheet, headers, code)
            if row_number is not None:
                if selected is not None:
                    raise FiscalError(
                        f"団体コード {code} を複数シートで発見しました"
                    )
                selected = (sheet, headers, row_number)
        except (XlsxError, FiscalError) as error:
            errors.append(f"{sheet.name}: {error}")
    if selected is None:
        detail = "; ".join(errors[:3])
        raise FiscalError(f"概況XLSXに団体コード {code} の行がありません。{detail}")

    sheet, headers, row_number = selected
    row = sheet.rows().get(row_number, {})
    name_cell = row.get(headers["municipality_name"].column)
    if name_cell is None:
        raise FiscalError(f"団体名セルが空です: {sheet.name} row {row_number}")
    if _normalize(name_cell.value) != _normalize(str(municipality["name"])):
        raise FiscalError(
            f"団体コードと名称が一致しません: {code} "
            f"({name_cell.value} != {municipality['name']})"
        )

    records: list[dict[str, Any]] = []
    for indicator, spec in INDICATORS.items():
        header = headers[indicator]
        value_cell = row.get(header.column)
        if value_cell is None:
            raise FiscalError(
                f"{spec['label']}の値セルが空です: {sheet.name} row {row_number}"
            )
        source_locator = {
            "kind": "xlsx",
            "index_url": FISCAL_INDEX,
            "year_page_url": source_page_url,
            "resolved_xlsx_url": discovery["xlsx_url"],
            "sheet": sheet.name,
            "row_key": code,
            "row": row_number,
            "column": header.column,
            "header_cell": header.cell.ref,
            "header_label": header.matched_label,
            "header_fallback_used": header.fallback_used,
            "value_cell": value_cell.ref,
            "sha256": discovery["xlsx_sha256"],
        }
        display_raw_value = _display_raw_value(value_cell.value)
        if display_raw_value != value_cell.value:
            source_locator["original_xml_value"] = value_cell.value
            source_locator["raw_value_display_normalization"] = (
                "shortened_obvious_binary_float_tail"
            )
        records.append(
            {
                "indicator": indicator,
                "value": _parse_number(value_cell.value),
                "raw_value": display_raw_value,
                "unit": spec["unit"],
                "as_of": f"{fiscal_year}年度",
                "definition": spec["definition"],
                "source_name": "総務省 市町村別決算状況調",
                "source_url": source_page_url,
                "source_locator": source_locator,
                "fetched_at": discovery["xlsx_fetched_at"],
            }
        )
    parse_info = {
        "sheet": sheet.name,
        "row": row_number,
        "code_cell": row[headers["local_government_code_6"].column].ref,
        "name_cell": name_cell.ref,
        "header_fallbacks": {
            key: {
                "matched_label": match.matched_label,
                "cell": match.cell.ref,
                "fallback_used": match.fallback_used,
            }
            for key, match in headers.items()
        },
    }
    return records, parse_info


def _discover_card_page(
    client: HttpClient, fiscal_year: int
) -> tuple[str, str]:
    index = _fetch_html(client, CARD_INDEX, "card-index")
    candidates: list[tuple[int, str, str]] = []
    for link in _links(CARD_INDEX, index):
        combined = link.text + " " + link.context
        year = _fiscal_year(combined)
        path = urllib.parse.urlsplit(link.url).path.lower()
        if (
            year is not None
            and ("市町村決算カード" in combined or re.search(r"/card-\d+\.html$", path))
            and path.endswith((".html", ".htm"))
        ):
            candidates.append((year, link.url, link.text))
    same_year = [item for item in candidates if item[0] == fiscal_year]
    if not same_year:
        available = sorted({item[0] for item in candidates}, reverse=True)
        raise FiscalError(
            f"決算カードに同一年度 {fiscal_year} のページがありません"
            f"（掲載年度: {available[:3]}）"
        )
    _, url, label = same_year[0]
    return url, label


def _discover_card_xlsx(
    client: HttpClient,
    page_url: str,
    fiscal_year: int,
    prefecture: str,
) -> tuple[FetchResult, dict[str, Any]]:
    page = _fetch_html(client, page_url, f"card-year:{fiscal_year}")
    target = _normalize(prefecture)
    candidates: list[tuple[int, Link]] = []
    for link in _links(page_url, page):
        if not urllib.parse.urlsplit(link.url).path.lower().endswith(".xlsx"):
            continue
        context = _normalize(link.context + link.text)
        position = context.rfind(target)
        if position >= 0:
            candidates.append((len(context) - position, link))
    if not candidates:
        raise FiscalError(f"決算カード年度ページに{prefecture}のXLSXがありません")
    _, chosen = min(candidates, key=lambda item: item[0])
    try:
        workbook = client.fetch(
            chosen.url,
            cache_key=(
                f"soumu:xlsx:card:{fiscal_year}:{prefecture}:{chosen.url}"
            ),
        )
    except FetchError as error:
        raise FiscalError(f"決算カードXLSXを取得できません: {error}") from error
    _validate_xlsx_result(workbook, chosen.url)
    return workbook, {
        "index_url": CARD_INDEX,
        "year_page_url": page_url,
        "xlsx_url": workbook.final_url,
        "xlsx_sha256": workbook.sha256,
        "xlsx_fetched_at": workbook.fetched_at,
        "xlsx_content_type": workbook.content_type,
    }


def _locate_card_value(
    cells: tuple[Cell, ...], label: str, same_row: bool
) -> tuple[Cell, Cell]:
    label_cells = [
        cell for cell in cells if normalize_label(cell.value).startswith(label)
    ]
    candidates: list[tuple[int, Cell, Cell]] = []
    for label_cell in label_cells:
        for value_cell in cells:
            row_delta = value_cell.row - label_cell.row
            column_delta = value_cell.column - label_cell.column
            if column_delta <= 0 or column_delta > 30:
                continue
            if same_row and row_delta != 0:
                continue
            if not same_row and not (-1 <= row_delta <= 2):
                continue
            try:
                _parse_number(value_cell.value)
            except FiscalError:
                continue
            score = abs(row_delta) * 2 + column_delta
            candidates.append((score, label_cell, value_cell))
    if not candidates:
        raise FiscalError(f"決算カードの「{label}」に対応する値がありません")
    _, label_cell, value_cell = min(candidates, key=lambda item: item[0])
    return label_cell, value_cell


def _parse_card_xlsx(
    xlsx_path: Any,
    municipality_name: str,
    fiscal_year: int,
    discovery: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    try:
        sheets = read_workbook(xlsx_path)
    except XlsxError as error:
        raise FiscalError(str(error)) from error
    matches = [
        sheet
        for sheet in sheets
        if re.sub(r"^\d+", "", _normalize(sheet.name)) == _normalize(municipality_name)
    ]
    if len(matches) != 1:
        raise FiscalError(
            f"決算カードの自治体シートが1件になりません: "
            f"{municipality_name} ({len(matches)}件)"
        )
    sheet = matches[0]
    extracted: dict[str, dict[str, Any]] = {}
    for indicator, spec in INDICATORS.items():
        label_cell, value_cell = _locate_card_value(
            sheet.cells,
            str(spec["label"]),
            indicator != "keijou_shuushi_hiritsu",
        )
        source_locator = {
            "kind": "xlsx_cross_check",
            "index_url": CARD_INDEX,
            "year_page_url": discovery["year_page_url"],
            "resolved_xlsx_url": discovery["xlsx_url"],
            "sheet": sheet.name,
            "label_cell": label_cell.ref,
            "value_cell": value_cell.ref,
            "sha256": discovery["xlsx_sha256"],
        }
        display_raw_value = _display_raw_value(value_cell.value)
        if display_raw_value != value_cell.value:
            source_locator["original_xml_value"] = value_cell.value
            source_locator["raw_value_display_normalization"] = (
                "shortened_obvious_binary_float_tail"
            )
        extracted[indicator] = {
            "value": _parse_number(value_cell.value),
            "raw_value": display_raw_value,
            "source_name": "総務省 市町村決算カード",
            "source_url": discovery["year_page_url"],
            "source_locator": source_locator,
            "fetched_at": discovery["xlsx_fetched_at"],
            "as_of": f"{fiscal_year}年度",
        }
    return extracted


def _apply_cross_check(
    primary_records: list[dict[str, Any]],
    secondary: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for record in primary_records:
        indicator = str(record["indicator"])
        card = secondary[indicator]
        primary_value = record.get("value")
        secondary_value = card.get("value")
        if primary_value is None and secondary_value is None:
            state = "matched_missing"
        elif primary_value is None or secondary_value is None:
            state = "mismatch"
        else:
            difference = abs(float(primary_value) - float(secondary_value))
            state = "matched" if difference <= 0.0001 else "mismatch"
        check = {
            "state": state,
            "comparison_rule": "numeric equality within 0.0001; missing matches missing",
            "secondary_raw_value": card["raw_value"],
            "secondary_source_name": card["source_name"],
            "secondary_source_url": card["source_url"],
            "secondary_source_locator": card["source_locator"],
            "secondary_fetched_at": card["fetched_at"],
        }
        record["source_locator"]["cross_check"] = check
        checks[indicator] = check
    mismatch_count = sum(
        1 for check in checks.values() if check["state"] == "mismatch"
    )
    return {
        "status": "reconciled" if mismatch_count == 0 else "mismatch",
        "mismatch_count": mismatch_count,
        "checks": checks,
    }


def fetch_fiscal(
    municipality: dict[str, Any],
    client: HttpClient,
    *,
    cross_check: bool = False,
) -> dict[str, Any]:
    """Fetch the primary overview workbook and optionally check the fiscal card."""

    fiscal_year, page_url, page_label, index_fetched_at = (
        discover_latest_fiscal_page(client)
    )
    workbook, discovery = discover_overview_xlsx(
        client,
        page_url,
        fiscal_year,
        str(municipality["region_level"]),
    )
    records, parse_info = parse_overview_xlsx(
        workbook.cache_path,
        municipality,
        fiscal_year,
        page_url,
        discovery,
    )
    cross_check_result: dict[str, Any] = {"status": "not_requested"}
    warnings: list[str] = []
    if cross_check:
        try:
            card_page_url, card_page_label = _discover_card_page(
                client, fiscal_year
            )
            card_workbook, card_discovery = _discover_card_xlsx(
                client,
                card_page_url,
                fiscal_year,
                str(municipality["prefecture"]),
            )
            card_discovery["year_page_label"] = card_page_label
            secondary = _parse_card_xlsx(
                card_workbook.cache_path,
                str(municipality["name"]),
                fiscal_year,
                card_discovery,
            )
            cross_check_result = _apply_cross_check(records, secondary)
            cross_check_result["discovery"] = card_discovery
            if cross_check_result["status"] == "mismatch":
                warnings.append("決算状況調と決算カードの一部指標が一致しません")
        except (FiscalError, KeyError, OSError) as error:
            cross_check_result = {"status": "failed", "error": str(error)}
            warnings.append(f"任意の決算カード検算に失敗しました: {error}")
    return {
        "fiscal_year": fiscal_year,
        "status": "parsed_primary_overview_xlsx",
        "records": records,
        "discovery": {
            **discovery,
            "index_fetched_at": index_fetched_at,
            "year_page_label": page_label,
            "parse": parse_info,
        },
        "cross_check": cross_check_result,
        "warnings": warnings,
    }
