"""Tier 1a census discovery and extraction through e-Stat API 3.0."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .http import FetchError, HttpClient


API_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"
STATS_CODE = "00200521"

# These are nationwide 2020 tables, not municipality-specific values.
FALLBACK_TABLES = {
    "population_total": ("0003445078", "202010"),
    "households_total": ("0003445098", "202010"),
    "population_65_plus_ratio": ("0003445163", "202010"),
}

SEARCHES = {
    "population_total": "男女別人口 AND 全国 AND 都道府県 AND 市区町村",
    "households_total": (
        "世帯の種類別世帯数 AND 世帯人員 AND 全国 AND 都道府県 AND 市区町村"
    ),
    "population_65_plus_ratio": (
        "男女 AND 年齢（3区分） AND 国籍総数か日本人別人口構成比 AND 市区町村"
    ),
}

TITLE_MATCHERS = {
    "population_total": ("男女別人口－全国",),
    "households_total": ("世帯の種類別世帯数及び世帯人員－全国",),
    "population_65_plus_ratio": ("年齢（3区分）", "人口構成比［年齢別］－全国"),
}

LABEL_REQUIREMENTS = {
    "population_total": ("人口", "総数"),
    "households_total": ("世帯数", "総数"),
    "population_65_plus_ratio": (
        "人口構成比［年齢別］",
        "国籍総数",
        "総数",
        "65歳以上",
    ),
}

DEFINITIONS = {
    "population_total": "国勢調査の調査時点に当該区域に常住する総人口。",
    "households_total": "国勢調査における世帯の種類「総数」の世帯数。",
    "population_65_plus_ratio": (
        "国勢調査の国籍総数・男女総数における65歳以上人口の公式公表構成比。"
        "年齢不詳の分母上の扱いは原表定義の確認が必要であり、自前計算しない。"
    ),
}


class CensusError(RuntimeError):
    """Raised when census tables cannot be selected or parsed safely."""


@dataclass(frozen=True)
class TableSelection:
    """The three common-vintage tables and selection provenance."""

    tables: dict[str, tuple[str, str]]
    used_fallback: bool
    warning: str | None
    reason: str


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("$", ""))
    return str(value or "")


def _api_get(
    client: HttpClient,
    endpoint: str,
    params: dict[str, Any],
    *,
    cache_label: str,
) -> tuple[dict[str, Any], str]:
    app_id = os.environ.get("ESTAT_APPID")
    if not app_id and not client.offline:
        raise CensusError("オンライン取得には環境変数 ESTAT_APPID が必要です")
    request_params = {"appId": app_id or "OFFLINE", "lang": "J", **params}
    url = f"{API_BASE}/{endpoint}?" + urllib.parse.urlencode(request_params)
    safe_params = {"lang": "J", **params}
    safe_key = json.dumps(safe_params, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(safe_key.encode("utf-8")).hexdigest()
    try:
        result = client.fetch(
            url,
            cache_key=f"estat:{endpoint}:{cache_label}:{digest}",
            sensitive_query_keys={"appId"},
        )
    except FetchError as error:
        raise CensusError(f"e-Stat {endpoint} の取得に失敗しました: {error}") from error
    return result.json(), result.fetched_at


def _check_result(root: dict[str, Any], operation: str) -> None:
    result = root.get("RESULT", {})
    try:
        status = int(result.get("STATUS", 999))
    except (TypeError, ValueError):
        status = 999
    if status > 2:
        raise CensusError(f"{operation} APIエラー: {result}")


def _fallback(reason: str) -> TableSelection:
    warning = (
        "WARNING: e-Statの動的表探索に失敗したため、検証済み2020年表へ"
        "フォールバックしました。latest-ness lost（最新性は保証されません）。"
    )
    return TableSelection(dict(FALLBACK_TABLES), True, warning, reason)


def discover_tables(client: HttpClient) -> TableSelection:
    """Select the latest survey date shared by all three semantic table matches."""

    candidates: dict[str, list[tuple[str, str]]] = {}
    failures: list[str] = []
    for indicator, query in SEARCHES.items():
        try:
            payload, _ = _api_get(
                client,
                "getStatsList",
                {"statsCode": STATS_CODE, "searchWord": query, "limit": 500},
                cache_label=indicator,
            )
            root = payload.get("GET_STATS_LIST", {})
            _check_result(root, "getStatsList")
            seen: set[str] = set()
            matches: list[tuple[str, str]] = []
            for table in _as_list(root.get("DATALIST_INF", {}).get("TABLE_INF")):
                if not isinstance(table, dict):
                    continue
                table_id = str(table.get("@id", ""))
                if not table_id or table_id in seen:
                    continue
                seen.add(table_id)
                title = _text(table.get("TITLE"))
                survey_date = _text(table.get("SURVEY_DATE"))
                statistics_name = _text(table.get("STATISTICS_NAME"))
                if any(term not in title for term in TITLE_MATCHERS[indicator]):
                    continue
                if "人口等基本集計" not in statistics_name:
                    continue
                if any(
                    excluded in title
                    for excluded in ("人口集中地区", "不詳補完", "都市計画")
                ):
                    continue
                if re.fullmatch(r"\d{6}", survey_date):
                    matches.append((table_id, survey_date))
            candidates[indicator] = matches
            if not matches:
                failures.append(f"{indicator}: 意味条件に合う表なし")
        except (CensusError, KeyError, TypeError, ValueError) as error:
            candidates[indicator] = []
            failures.append(f"{indicator}: {error}")

    common_dates: set[str] | None = None
    for indicator in SEARCHES:
        dates = {survey_date for _, survey_date in candidates[indicator]}
        common_dates = dates if common_dates is None else common_dates & dates
    if not common_dates:
        reason = "; ".join(failures) or "3指標に共通するSURVEY_DATEがありません"
        return _fallback(reason)

    chosen_date = max(common_dates)
    selected: dict[str, tuple[str, str]] = {}
    for indicator in SEARCHES:
        matches = sorted(
            (
                item
                for item in candidates[indicator]
                if item[1] == chosen_date
            ),
            key=lambda item: item[0],
        )
        if not matches:
            return _fallback(f"{indicator}: {chosen_date} の表を確定できません")
        selected[indicator] = matches[0]
    return TableSelection(
        selected,
        False,
        None,
        f"3指標が共通して揃う最新SURVEY_DATE {chosen_date}",
    )


def _metadata_maps(
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    root = payload.get("GET_META_INFO", {})
    _check_result(root, "getMetaInfo")
    objects = root.get("METADATA_INF", {}).get("CLASS_INF", {}).get(
        "CLASS_OBJ", []
    )
    dimension_names: dict[str, str] = {}
    labels: dict[str, dict[str, str]] = {}
    for obj in _as_list(objects):
        if not isinstance(obj, dict):
            continue
        dimension_id = str(obj.get("@id", ""))
        dimension_names[dimension_id] = str(obj.get("@name", ""))
        labels[dimension_id] = {
            str(item.get("@code", "")): str(item.get("@name", ""))
            for item in _as_list(obj.get("CLASS"))
            if isinstance(item, dict)
        }
    return dimension_names, labels


def _parse_numeric(raw: str) -> int | float | None:
    cleaned = raw.replace(",", "").strip()
    if cleaned in {"", "-", "―", "－", "***", "…", "X"}:
        return None
    try:
        number = float(cleaned)
    except ValueError as error:
        raise CensusError(f"数値として解釈できないe-Stat値です: {raw!r}") from error
    return int(number) if number.is_integer() else number


def _census_as_of(value: dict[str, Any], survey_date: str) -> str:
    raw_time = str(value.get("@time", ""))
    year_match = re.match(r"(\d{4})", raw_time)
    year = year_match.group(1) if year_match else survey_date[:4]
    month = survey_date[4:6] if len(survey_date) >= 6 else "10"
    return f"{year}-{month}-01"


def _fetch_indicator(
    client: HttpClient,
    indicator: str,
    table_id: str,
    survey_date: str,
    area_code: str,
) -> dict[str, Any]:
    meta_payload, _ = _api_get(
        client,
        "getMetaInfo",
        {"statsDataId": table_id},
        cache_label=table_id,
    )
    _, label_maps = _metadata_maps(meta_payload)
    data_payload, fetched_at = _api_get(
        client,
        "getStatsData",
        {
            "statsDataId": table_id,
            "cdArea": area_code,
            "limit": 10000,
            "metaGetFlg": "Y",
            "explanationGetFlg": "Y",
            "annotationGetFlg": "Y",
        },
        cache_label=f"{table_id}:{area_code}",
    )
    root = data_payload.get("GET_STATS_DATA", {})
    _check_result(root, "getStatsData")
    statistical_data = root.get("STATISTICAL_DATA", {})
    values = _as_list(statistical_data.get("DATA_INF", {}).get("VALUE"))
    requirements = LABEL_REQUIREMENTS[indicator]
    matches: list[tuple[dict[str, Any], list[str]]] = []
    for value in values:
        if not isinstance(value, dict) or str(value.get("@area")) != area_code:
            continue
        expanded = [
            label_maps[dimension].get(str(code), "")
            for key, code in value.items()
            if key.startswith("@")
            and (dimension := key[1:]) in label_maps
        ]
        if all(required in expanded for required in requirements):
            matches.append((value, expanded))
    if len(matches) != 1:
        raise CensusError(
            f"{indicator}: 表 {table_id} の意味条件に合う値が"
            f"1件になりません（{len(matches)}件）"
        )

    value, expanded = matches[0]
    raw_value = str(value.get("$", ""))
    table_info = statistical_data.get("TABLE_INF", {})
    actual_survey_date = _text(table_info.get("SURVEY_DATE")) or survey_date
    raw_unit = str(value.get("@unit", ""))
    unit = label_maps.get("unit", {}).get(raw_unit, raw_unit)
    return {
        "indicator": indicator,
        "value": _parse_numeric(raw_value),
        "raw_value": raw_value,
        "unit": unit,
        "as_of": _census_as_of(value, actual_survey_date),
        "definition": DEFINITIONS[indicator],
        "source_name": "e-Stat 国勢調査",
        "source_url": f"https://www.e-stat.go.jp/dbview?sid={table_id}",
        "source_locator": {
            "kind": "estat_api_3",
            "stats_data_id": table_id,
            "area_code": area_code,
            "dimensions": {
                key[1:]: str(code)
                for key, code in value.items()
                if key.startswith("@") and key != "@unit"
            },
            "labels": expanded,
        },
        "fetched_at": fetched_at,
    }


def _fetch_selected(
    client: HttpClient,
    selection: TableSelection,
    area_code: str,
) -> list[dict[str, Any]]:
    dates = {survey_date for _, survey_date in selection.tables.values()}
    if len(dates) != 1:
        raise CensusError(f"3指標のSURVEY_DATEが一致しません: {selection.tables}")
    return [
        _fetch_indicator(
            client, indicator, table_id, survey_date, area_code
        )
        for indicator, (table_id, survey_date) in selection.tables.items()
    ]


def fetch_census(
    municipality: dict[str, Any], client: HttpClient
) -> dict[str, Any]:
    """Discover and fetch the three common-vintage census indicators."""

    selection = discover_tables(client)
    try:
        records = _fetch_selected(
            client, selection, str(municipality["area_code_5"])
        )
    except CensusError as error:
        if selection.used_fallback:
            raise
        selection = _fallback(f"動的選択表のメタデータ照合失敗: {error}")
        records = _fetch_selected(
            client, selection, str(municipality["area_code_5"])
        )
    return {
        "selection_policy": (
            "人口・世帯・65歳以上比率が同一SURVEY_DATEで揃う最新の"
            "国勢調査人口等基本集計"
        ),
        "selection": {
            "tables": selection.tables,
            "used_fallback": selection.used_fallback,
            "reason": selection.reason,
            "warning": selection.warning,
        },
        "records": records,
    }
