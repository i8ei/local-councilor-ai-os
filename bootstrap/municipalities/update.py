#!/usr/bin/env python3
"""Rebuild the bundled municipality registry from official public indexes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from bootstrap.cli.http import FetchResult, HttpClient
from bootstrap.cli.resolve import MUNICIPALITY_LEVELS, REGION_API, _classes
from bootstrap.municipalities.registry import (
    DEFAULT_METADATA_PATH,
    DEFAULT_REGISTRY_PATH,
    REQUIRED_COLUMNS,
    RegistryError,
    validate_registry,
)

J_LIS_CODE_ROOT = "https://www.j-lis.go.jp/spd/code-address/jititai-code.html"
J_LIS_MAP_ROOT = "https://www.j-lis.go.jp/spd/map-search/cms_1069.html"
EXPECTED_MUNICIPALITY_COUNT = 1741
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "municipalities"


@dataclass
class Cell:
    """One parsed HTML table cell."""

    text_parts: list[str] = field(default_factory=list)
    links: list[tuple[str, list[str]]] = field(default_factory=list)
    active_link: int | None = None

    def text(self) -> str:
        return " ".join("".join(self.text_parts).split())


class TableParser(HTMLParser):
    """Extract table rows and links using only the Python standard library."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self._table_depth = 0
        self._table: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell: Cell | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"th", "td"} and self._row is not None:
            self._cell = Cell()
        elif self._cell is not None and tag == "a" and attributes.get("href"):
            self._cell.links.append((str(attributes["href"]), []))
            self._cell.active_link = len(self._cell.links) - 1

    def handle_endtag(self, tag: str) -> None:
        if self._cell is not None and tag == "a":
            self._cell.active_link = None
        elif self._table_depth == 1 and tag in {"th", "td"}:
            if self._row is not None and self._cell is not None:
                self._row.append(self._cell)
            self._cell = None
        elif self._table_depth == 1 and tag == "tr":
            if self._table is not None and self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            if self._table_depth == 1 and self._table:
                self.tables.append(self._table)
                self._table = None
            self._table_depth = max(0, self._table_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        self._cell.text_parts.append(data)
        if self._cell.active_link is not None:
            self._cell.links[self._cell.active_link][1].append(data)


class AnchorParser(HTMLParser):
    """Extract document-wide anchors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = str(href)
                self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.anchors.append(
                (self._href, " ".join("".join(self._text).split()))
            )
            self._href = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


def _parse_tables(text: str) -> list[list[list[Cell]]]:
    parser = TableParser()
    parser.feed(text)
    return parser.tables


def _parse_anchors(text: str, base_url: str) -> list[tuple[str, str]]:
    parser = AnchorParser()
    parser.feed(text)
    return [
        (urllib.parse.urljoin(base_url, href), text)
        for href, text in parser.anchors
    ]


def _fetch(
    client: HttpClient,
    url: str,
    cache_key: str,
    source_records: dict[str, dict[str, Any]],
) -> FetchResult:
    result = client.fetch(url, cache_key=cache_key)
    source_records[result.url] = {
        "url": result.url,
        "final_url": result.final_url,
        "fetched_at": result.fetched_at,
        "sha256": result.sha256,
        "from_cache": result.from_cache,
    }
    return result


def _current_regions(
    client: HttpClient,
    source_records: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    prefecture_url = REGION_API + "?Lang=JP&RegionLevel=3"
    municipality_url = (
        REGION_API
        + "?Lang=JP&RegionLevel="
        + urllib.parse.quote(MUNICIPALITY_LEVELS, safe="")
    )
    prefecture_result = _fetch(
        client, prefecture_url, "municipality-registry:estat-prefectures", source_records
    )
    municipality_result = _fetch(
        client,
        municipality_url,
        "municipality-registry:estat-municipalities",
        source_records,
    )
    prefectures = {
        item.get("@regionCode", "")[:2]: item.get("@name", "").strip()
        for item in _classes(prefecture_result.json())
        if item.get("@toDate") == "999912"
        and re.fullmatch(r"\d{5}", item.get("@regionCode", ""))
    }
    municipalities = {
        item["@regionCode"]: {
            "name": item.get("@name", "").strip(),
            "region_level": item.get("@level", ""),
            "valid_from": _format_period(item.get("@fromDate", "")),
            "valid_to": (
                ""
                if item.get("@toDate") == "999912"
                else _format_period(item.get("@toDate", ""))
            ),
        }
        for item in _classes(municipality_result.json())
        if item.get("@toDate") == "999912"
        and re.fullmatch(r"\d{5}", item.get("@regionCode", ""))
    }
    if len(prefectures) != 47:
        raise RegistryError(f"現行都道府県が47件ではありません: {len(prefectures)}")
    return prefectures, municipalities


def _format_period(value: str) -> str:
    if re.fullmatch(r"\d{6}", value):
        return value[:4] + "-" + value[4:]
    return value


def _prefecture_page_links(
    root: FetchResult,
    prefectures: dict[str, str],
    *,
    kind: str,
) -> dict[str, str]:
    anchors = _parse_anchors(root.text(), root.final_url)
    result: dict[str, str] = {}
    for code, prefecture in prefectures.items():
        expected = prefecture + "内"
        matches = [
            url
            for url, text in anchors
            if (
                text.startswith(expected)
                if kind == "code"
                else text == prefecture
            )
        ]
        if len(matches) != 1:
            raise RegistryError(
                f"J-LIS {kind}索引で{prefecture}のページを一意に発見できません: "
                f"{matches}"
            )
        result[code] = matches[0]
    return result


def _code_rows(page: FetchResult) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for table in _parse_tables(page.text()):
        for row in table:
            values = [cell.text() for cell in row]
            if len(values) < 2 or not re.fullmatch(r"\d{6}", values[0]):
                continue
            result[values[0][:5]] = (values[0], values[1])
    return result


def _home_rows(page: FetchResult) -> dict[str, str]:
    result: dict[str, str] = {}
    jlis_host = urllib.parse.urlsplit(page.final_url).hostname
    for table in _parse_tables(page.text()):
        for row in table:
            if not row:
                continue
            name = row[0].text()
            external_links = [
                urllib.parse.urljoin(page.final_url, href)
                for href, _ in row[0].links
                if urllib.parse.urlsplit(
                    urllib.parse.urljoin(page.final_url, href)
                ).hostname
                != jlis_host
            ]
            if name and len(external_links) == 1:
                result[name] = external_links[0]
    return result


def _expected_code(area_code_5: str) -> str:
    weighted_sum = sum(
        int(digit) * weight
        for digit, weight in zip(area_code_5, (6, 5, 4, 3, 2), strict=True)
    )
    return area_code_5 + str((11 - (weighted_sum % 11)) % 10)


def build_records(
    *,
    prefectures: dict[str, str],
    current_regions: dict[str, dict[str, str]],
    code_pages: dict[str, tuple[str, dict[str, tuple[str, str]]]],
    home_pages: dict[str, list[tuple[str, dict[str, str]]]],
) -> list[dict[str, str]]:
    """Join three official sources and fail on disagreement."""

    records: list[dict[str, str]] = []
    for prefecture_code, prefecture_name in sorted(prefectures.items()):
        code_source_url, codes = code_pages[prefecture_code]
        homes: dict[str, tuple[str, str]] = {}
        for home_source_url, page_homes in home_pages[prefecture_code]:
            for name, home_url in page_homes.items():
                if name in homes and homes[name][0] != home_url:
                    raise RegistryError(
                        f"{prefecture_name} {name}の公式URLが複数あります"
                    )
                homes[name] = (home_url, home_source_url)

        for area_code, (local_code, municipality_name) in sorted(codes.items()):
            region = current_regions.get(area_code)
            home = homes.get(municipality_name)
            if region is None or home is None:
                continue
            if local_code != _expected_code(area_code):
                raise RegistryError(
                    f"J-LIS団体コードの検査数字が不正です: {local_code}"
                )
            records.append(
                {
                    "prefecture_name": prefecture_name,
                    "municipality_name": region["name"],
                    "name_aliases": (
                        municipality_name
                        if municipality_name != region["name"]
                        else ""
                    ),
                    "prefecture_code_2": prefecture_code,
                    "area_code_5": area_code,
                    "local_government_code_6": local_code,
                    "region_level": region["region_level"],
                    "official_home_url": home[0],
                    "valid_from": region["valid_from"],
                    "valid_to": region["valid_to"],
                    "code_source_url": code_source_url,
                    "home_source_url": home[1],
                }
            )
    return records


def _csv_body(records: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=REQUIRED_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode("utf-8")


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".tmp-",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def update(
    *,
    cache_dir: Path,
    registry_path: Path,
    metadata_path: Path,
    write: bool,
    refresh: bool,
    expected_count: int,
) -> dict[str, Any]:
    """Acquire, verify, and optionally write one national snapshot."""

    client = HttpClient(cache_dir, refresh=refresh)
    sources: dict[str, dict[str, Any]] = {}
    prefectures, current_regions = _current_regions(client, sources)
    code_root = _fetch(
        client, J_LIS_CODE_ROOT, "municipality-registry:jlis-code-root", sources
    )
    map_root = _fetch(
        client, J_LIS_MAP_ROOT, "municipality-registry:jlis-map-root", sources
    )
    code_links = _prefecture_page_links(
        code_root, prefectures, kind="code"
    )
    map_links = _prefecture_page_links(map_root, prefectures, kind="map")

    code_pages: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {}
    home_pages: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for prefecture_code, prefecture_name in sorted(prefectures.items()):
        code_page = _fetch(
            client,
            code_links[prefecture_code],
            f"municipality-registry:jlis-code-{prefecture_code}",
            sources,
        )
        code_pages[prefecture_code] = (
            code_page.url,
            _code_rows(code_page),
        )
        map_page = _fetch(
            client,
            map_links[prefecture_code],
            f"municipality-registry:jlis-map-{prefecture_code}",
            sources,
        )
        pages = [(map_page.url, _home_rows(map_page))]
        if prefecture_name == "北海道":
            region_links = [
                url
                for url, text in _parse_anchors(map_page.text(), map_page.final_url)
                if text.startswith(("道央", "道南", "道北", "道東"))
            ]
            if len(region_links) != 4:
                raise RegistryError(
                    f"北海道の4地域ページを発見できません: {region_links}"
                )
            pages = []
            for index, url in enumerate(region_links, start=1):
                region_page = _fetch(
                    client,
                    url,
                    f"municipality-registry:jlis-map-01-{index}",
                    sources,
                )
                pages.append((region_page.url, _home_rows(region_page)))
        home_pages[prefecture_code] = pages

    records = build_records(
        prefectures=prefectures,
        current_regions=current_regions,
        code_pages=code_pages,
        home_pages=home_pages,
    )
    if len(records) != expected_count:
        raise RegistryError(
            f"基礎自治体数が期待値と一致しません: {len(records)}/{expected_count}"
        )
    included_codes = {record["area_code_5"] for record in records}
    excluded_current_regions = [
        {
            "prefecture_name": prefectures.get(area_code[:2], ""),
            "region_name": region["name"],
            "area_code_5": area_code,
            "region_level": region["region_level"],
            "reason": (
                "not present in both J-LIS local-government code and "
                "official website indexes"
            ),
        }
        for area_code, region in sorted(current_regions.items())
        if area_code not in included_codes
    ]
    fetched_times = sorted(
        str(item["fetched_at"]) for item in sources.values()
    )
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": fetched_times[-1],
        "record_count": len(records),
        "prefecture_count": len(prefectures),
        "name_alias_count": sum(
            bool(record["name_aliases"]) for record in records
        ),
        "excluded_current_region_count": len(excluded_current_regions),
        "excluded_current_regions": excluded_current_regions,
        "scope": (
            "Current basic municipalities present in all three sources: "
            "e-Stat region metadata, J-LIS local-government codes, and "
            "J-LIS official website map."
        ),
        "source_roots": {
            "estat_region_api": REGION_API,
            "jlis_code": J_LIS_CODE_ROOT,
            "jlis_home": J_LIS_MAP_ROOT,
        },
        "sources": sorted(sources.values(), key=lambda item: str(item["url"])),
        "retrieval": {
            key: value
            for key, value in client.retrieval_report().items()
            if key
            in {
                "offline",
                "refresh",
                "live_request_count",
                "cache_hit_count",
                "cache_miss_count",
                "refresh_count",
                "latestness_rechecked_this_run",
            }
        },
    }
    validate_registry(tuple(records), metadata)
    csv_body = _csv_body(records)
    csv_sha256 = hashlib.sha256(csv_body).hexdigest()
    existing_sha256 = (
        hashlib.sha256(registry_path.read_bytes()).hexdigest()
        if registry_path.is_file()
        else None
    )

    report = {
        "status": "ok",
        "record_count": len(records),
        "prefecture_count": len(prefectures),
        "generated_at": metadata["generated_at"],
        "live_request_count": client.request_count,
        "cache_hit_count": client.cache_hit_count,
        "registry_sha256": csv_sha256,
        "registry_changed": existing_sha256 != csv_sha256,
        "written": write,
    }
    if not write:
        return report

    _atomic_write(registry_path, csv_body)
    metadata["registry_file"] = registry_path.name
    metadata["registry_sha256"] = csv_sha256
    metadata_body = (
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(metadata_path, metadata_body)
    report.update(
        {
            "registry_path": str(registry_path),
            "metadata_path": str(metadata_path),
        }
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="公式索引から全国基礎自治体registryを再構築して検証"
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--write",
        action="store_true",
        help="検証成功後にregistryとmetadataを原子的に置換する",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=EXPECTED_MUNICIPALITY_COUNT,
        help="自治体再編時は根拠を確認して明示的に更新する",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = update(
            cache_dir=args.cache_dir,
            registry_path=args.registry,
            metadata_path=args.metadata,
            write=args.write,
            refresh=args.refresh,
            expected_count=args.expected_count,
        )
    except (OSError, RegistryError, ValueError) as error:
        print(
            json.dumps(
                {"status": "failed", "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
