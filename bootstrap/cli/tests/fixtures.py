"""Synthetic API fixtures and a tiny XLSX builder."""

from __future__ import annotations

import html
import json
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

from bootstrap.cli.http import FetchResult


def region_payload(
    records: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a getRegionInfo-shaped response."""

    return {
        "GET_META_REGION_INF": {
            "RESULT": {"status": "0"},
            "METADATA_INF": {
                "CLASS_INF": {
                    "CLASS_OBJ": {
                        "@id": "region",
                        "CLASS": records,
                    }
                }
            },
        }
    }


PREFECTURES = region_payload(
    [
        {
            "@regionCode": "01000",
            "@name": "北県",
            "@level": "3",
            "@toDate": "999912",
        },
        {
            "@regionCode": "02000",
            "@name": "南県",
            "@level": "3",
            "@toDate": "999912",
        },
    ]
)

MUNICIPALITIES = region_payload(
    [
        {
            "@regionCode": "01101",
            "@name": "同名市",
            "@level": "9",
            "@toDate": "999912",
        },
        {
            "@regionCode": "02101",
            "@name": "同名市",
            "@level": "9",
            "@toDate": "999912",
        },
        {
            "@regionCode": "01102",
            "@name": "旧市",
            "@level": "9",
            "@toDate": "202001",
        },
    ]
)


class FakeRegionClient:
    """Serve synthetic dashboard responses through the HttpClient interface."""

    offline = False
    request_count = 0

    def fetch(self, url: str, **_: Any) -> FetchResult:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        payload = PREFECTURES if query.get("RegionLevel") == ["3"] else MUNICIPALITIES
        return FetchResult(
            url=url,
            final_url=url,
            body=json.dumps(payload, ensure_ascii=False).encode(),
            fetched_at="2026-01-02T03:04:05Z",
            content_type="application/json",
            encoding="utf-8",
            cache_path=Path("/tmp/fake-region.json"),
            sha256="fixture",
            from_cache=False,
        )


TABLES = {
    "population_total": {
        "id": "1001",
        "title": "男女別人口－全国、都道府県、市区町村",
        "labels": [("cat01", "1", "人口"), ("cat02", "0", "総数")],
        "raw": "12345",
        "unit": "人",
    },
    "households_total": {
        "id": "1002",
        "title": "世帯の種類別世帯数及び世帯人員－全国、都道府県、市区町村",
        "labels": [("cat01", "1", "世帯数"), ("cat02", "0", "総数")],
        "raw": "4567",
        "unit": "世帯",
    },
    "population_65_plus_ratio": {
        "id": "1003",
        "title": "年齢（3区分）人口構成比［年齢別］－全国、市区町村",
        "labels": [
            ("cat01", "1", "人口構成比［年齢別］"),
            ("cat02", "0", "国籍総数"),
            ("cat03", "0", "総数"),
            ("cat04", "3", "65歳以上"),
        ],
        "raw": "31.2",
        "unit": "％",
    },
}


class FakeEStatClient:
    """Serve getStatsList, getMetaInfo, and getStatsData fixtures."""

    offline = False
    request_count = 0

    def fetch(self, url: str, **_: Any) -> FetchResult:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        endpoint = parts.path.rsplit("/", 1)[-1]
        if endpoint == "getStatsList":
            search = query["searchWord"][0]
            if "世帯の種類別" in search:
                key = "households_total"
            elif "年齢（3区分）" in search:
                key = "population_65_plus_ratio"
            else:
                key = "population_total"
            table = TABLES[key]
            payload = {
                "GET_STATS_LIST": {
                    "RESULT": {"STATUS": 0},
                    "DATALIST_INF": {
                        "TABLE_INF": {
                            "@id": table["id"],
                            "TITLE": {"$": table["title"]},
                            "SURVEY_DATE": "202010",
                            "STATISTICS_NAME": {"$": "人口等基本集計"},
                        }
                    },
                }
            }
        else:
            table_id = query["statsDataId"][0]
            table = next(item for item in TABLES.values() if item["id"] == table_id)
            if endpoint == "getMetaInfo":
                classes = [
                    {
                        "@id": dimension,
                        "@name": dimension,
                        "CLASS": {"@code": code, "@name": label},
                    }
                    for dimension, code, label in table["labels"]
                ]
                classes.append(
                    {
                        "@id": "unit",
                        "@name": "単位",
                        "CLASS": {"@code": "u", "@name": table["unit"]},
                    }
                )
                payload = {
                    "GET_META_INFO": {
                        "RESULT": {"STATUS": 0},
                        "METADATA_INF": {
                            "CLASS_INF": {"CLASS_OBJ": classes}
                        },
                    }
                }
            else:
                value = {
                    "@area": query["cdArea"][0],
                    "@time": "2020000000",
                    "@unit": "u",
                    "$": table["raw"],
                }
                for dimension, code, _ in table["labels"]:
                    value[f"@{dimension}"] = code
                payload = {
                    "GET_STATS_DATA": {
                        "RESULT": {"STATUS": 0},
                        "STATISTICAL_DATA": {
                            "TABLE_INF": {"SURVEY_DATE": "202010"},
                            "DATA_INF": {"VALUE": value},
                        },
                    }
                }
        body = json.dumps(payload, ensure_ascii=False).encode()
        return FetchResult(
            url=url.replace(query.get("appId", [""])[0], "REDACTED"),
            final_url=url.replace(query.get("appId", [""])[0], "REDACTED"),
            body=body,
            fetched_at="2026-01-02T03:04:05Z",
            content_type="application/json",
            encoding="utf-8",
            cache_path=Path("/tmp/fake-estat.json"),
            sha256="fixture",
            from_cache=False,
        )


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def build_inline_xlsx(
    path: Path,
    sheets: list[tuple[str, dict[str, str]]],
) -> None:
    """Build a sparse multi-sheet XLSX using inline strings."""

    workbook_sheets = "".join(
        f'<sheet name="{html.escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheets, start=1)
    )
    relationships = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets>'
            f"{workbook_sheets}</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            f'2006/relationships">{relationships}</Relationships>',
        )
        for index, (_, cells) in enumerate(sheets, start=1):
            rows: dict[int, list[tuple[str, str]]] = {}
            for ref, value in cells.items():
                row = int("".join(char for char in ref if char.isdigit()))
                rows.setdefault(row, []).append((ref, value))
            xml_rows = "".join(
                f'<row r="{row}">'
                + "".join(
                    f'<c r="{ref}" t="inlineStr"><is><t>'
                    f"{html.escape(value)}</t></is></c>"
                    for ref, value in row_cells
                )
                + "</row>"
                for row, row_cells in sorted(rows.items())
            )
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                f'spreadsheetml/2006/main"><sheetData>{xml_rows}'
                "</sheetData></worksheet>",
            )


def build_minimal_xlsx(path: Path) -> None:
    """Build a one-sheet XLSX using shared strings and numeric cells."""

    headers = [
        "地方公共団体コード",
        "市町村名",
        "財政力指数",
        "経常収支比率",
        "実質公債費比率",
        "将来負担比率",
        "歳入総額",
        "歳出総額",
    ]
    strings = headers + ["架空町", "-"]
    shared = "".join(
        f"<si><t>{html.escape(value)}</t></si>" for value in strings
    )
    header_cells = "".join(
        f'<c r="{_column_name(index)}2" t="s"><v>{index - 1}</v></c>'
        for index in range(1, len(headers) + 1)
    )
    values = ["123457", "8", "0.42", "88.1", "7.2", "9", "100000", "90000"]
    data_cells: list[str] = []
    for index, value in enumerate(values, start=1):
        if index in {2, 6}:
            data_cells.append(
                f'<c r="{_column_name(index)}3" t="s"><v>{value}</v></c>'
            )
        else:
            data_cells.append(
                f'<c r="{_column_name(index)}3"><v>{value}</v></c>'
            )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        f'<row r="2">{header_cells}</row>'
        f'<row r="3">{"".join(data_cells)}</row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets>'
            '<sheet name="概況" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
            f'2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
            f"{shared}</sst>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
