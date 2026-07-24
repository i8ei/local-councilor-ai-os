"""Tier 0 municipality resolution through a snapshot and the e-Stat API."""

from __future__ import annotations

import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .http import HttpClient
from bootstrap.municipalities import (
    RegistryError,
    load_metadata,
    lookup as registry_lookup,
)


REGION_API = "https://dashboard.e-stat.go.jp/api/1.0/Json/getRegionInfo"
MUNICIPALITY_LEVELS = "8,9,10,12,13"


class ResolveError(RuntimeError):
    """Raised when a municipality cannot be resolved safely."""


class AmbiguousMunicipality(ResolveError):
    """Raised when a prefecture hint is required."""

    def __init__(self, name: str, candidates: list[dict[str, str]]) -> None:
        self.name = name
        self.candidates = candidates
        rendered = [
            f"{item['prefecture']} {item['name']} ({item['area_code_5']})"
            for item in candidates
        ]
        super().__init__(
            f"自治体名「{name}」は複数候補です。--prefecture が必要です: "
            + " / ".join(rendered)
        )


@dataclass(frozen=True)
class RegionResponse:
    """Parsed API response plus acquisition provenance."""

    payload: dict[str, Any]
    source_url: str
    fetched_at: str


def normalize_name(value: str) -> str:
    """NFC-normalize user and API names."""

    return unicodedata.normalize("NFC", value).replace("\u3000", " ").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _classes(payload: dict[str, Any]) -> list[dict[str, str]]:
    root = payload.get("GET_META_REGION_INF", {})
    result = root.get("RESULT", {})
    if str(result.get("status")) != "0":
        raise ResolveError(f"地域メタ情報APIエラー: {result}")
    objects = root.get("METADATA_INF", {}).get("CLASS_INF", {}).get(
        "CLASS_OBJ", []
    )
    records: list[dict[str, str]] = []
    for class_object in _as_list(objects):
        if not isinstance(class_object, dict):
            continue
        for item in _as_list(class_object.get("CLASS")):
            if isinstance(item, dict):
                records.append({str(key): str(value) for key, value in item.items()})
    return records


def _fetch_region_info(
    client: HttpClient, params: dict[str, str], cache_label: str
) -> RegionResponse:
    query = urllib.parse.urlencode(params)
    url = REGION_API + "?" + query
    result = client.fetch(url, cache_key="regions:" + cache_label + ":" + query)
    return RegionResponse(result.json(), url, result.fetched_at)


def _prefecture_matches(hint: str, prefecture: str) -> bool:
    normalized_hint = normalize_name(hint)
    normalized_prefecture = normalize_name(prefecture)
    suffixes = "都道府県"
    return normalized_hint == normalized_prefecture or normalized_hint.rstrip(
        suffixes
    ) == normalized_prefecture.rstrip(suffixes)


def local_government_code(area_code_5: str) -> str:
    """Append the official check digit to a five-digit standard area code."""

    if len(area_code_5) != 5 or not area_code_5.isdigit():
        raise ResolveError(f"5桁標準地域コードが不正です: {area_code_5}")
    weighted_sum = sum(
        int(digit) * weight
        for digit, weight in zip(area_code_5, (6, 5, 4, 3, 2), strict=True)
    )
    check_digit = (11 - (weighted_sum % 11)) % 10
    return area_code_5 + str(check_digit)


def resolve_municipality(
    name: str,
    prefecture_hint: str | None,
    client: HttpClient,
) -> dict[str, Any]:
    """Resolve one current municipality by NFC-normalized exact name."""

    try:
        registry_search_name, registry_matches = registry_lookup(
            name, prefecture_hint
        )
        registry_metadata = load_metadata()
    except RegistryError:
        registry_search_name = normalize_name(name)
        registry_matches = []
        registry_metadata = {}

    if registry_matches:
        candidates = [
            {
                "name": item["municipality_name"],
                "area_code_5": item["area_code_5"],
                "local_government_code_6": item[
                    "local_government_code_6"
                ],
                "prefecture": item["prefecture_name"],
                "prefecture_code_2": item["prefecture_code_2"],
                "region_level": item["region_level"],
                "official_home_url": item["official_home_url"],
                "home_source_url": item["home_source_url"],
            }
            for item in registry_matches
        ]
        if len(candidates) > 1:
            raise AmbiguousMunicipality(registry_search_name, candidates)
        result: dict[str, Any] = dict(candidates[0])
        result.update(
            {
                "input_name": name,
                "input_prefecture": prefecture_hint,
                "normalized_name": registry_search_name,
                "candidate_count": 1,
                "resolved_from": "bundled municipality registry",
                "source_url": registry_matches[0]["code_source_url"],
                "resolved_at": registry_metadata.get("generated_at", ""),
                "registry_schema_version": registry_metadata.get(
                    "schema_version"
                ),
                "registry_generated_at": registry_metadata.get(
                    "generated_at"
                ),
            }
        )
        return result

    requested_name = normalize_name(name)
    prefecture_response = _fetch_region_info(
        client, {"Lang": "JP", "RegionLevel": "3"}, "prefectures"
    )
    prefectures = {
        item.get("@regionCode", "")[:2]: normalize_name(item.get("@name", ""))
        for item in _classes(prefecture_response.payload)
        if item.get("@toDate") == "999912"
        and len(item.get("@regionCode", "")) == 5
    }
    if not prefectures:
        raise ResolveError("現行都道府県表を取得できませんでした")

    embedded_hint: str | None = None
    search_name = requested_name
    for prefecture in prefectures.values():
        if requested_name.startswith(prefecture):
            embedded_hint = prefecture
            search_name = requested_name[len(prefecture) :].strip()
            break
    hint = prefecture_hint or embedded_hint

    municipality_response = _fetch_region_info(
        client,
        {
            "Lang": "JP",
            "RegionLevel": MUNICIPALITY_LEVELS,
            "SearchRegionWord": search_name,
        },
        "municipalities",
    )
    candidates: list[dict[str, str]] = []
    allowed_levels = set(MUNICIPALITY_LEVELS.split(","))
    for item in _classes(municipality_response.payload):
        code = item.get("@regionCode", "")
        if (
            normalize_name(item.get("@name", "")) == search_name
            and item.get("@toDate") == "999912"
            and item.get("@level") in allowed_levels
            and len(code) == 5
            and code.isdigit()
        ):
            candidates.append(
                {
                    "name": normalize_name(item["@name"]),
                    "area_code_5": code,
                    "local_government_code_6": local_government_code(code),
                    "prefecture": prefectures.get(code[:2], ""),
                    "prefecture_code_2": code[:2],
                    "region_level": item.get("@level", ""),
                }
            )
    if hint:
        candidates = [
            item
            for item in candidates
            if _prefecture_matches(hint, item["prefecture"])
        ]
    candidates.sort(key=lambda item: item["area_code_5"])
    if not candidates:
        suffix = f"（都道府県ヒント: {hint}）" if hint else ""
        raise ResolveError(f"現行自治体「{search_name}」が見つかりませんでした{suffix}")
    if any(not item["prefecture"] for item in candidates):
        raise ResolveError("自治体コードと都道府県表を照合できませんでした")
    if len(candidates) > 1:
        raise AmbiguousMunicipality(search_name, candidates)

    result: dict[str, Any] = dict(candidates[0])
    result.update(
        {
            "input_name": name,
            "input_prefecture": prefecture_hint,
            "normalized_name": search_name,
            "candidate_count": len(candidates),
            "resolved_from": "e-Stat dashboard getRegionInfo",
            "source_url": municipality_response.source_url,
            "resolved_at": municipality_response.fetched_at,
        }
    )
    return result
