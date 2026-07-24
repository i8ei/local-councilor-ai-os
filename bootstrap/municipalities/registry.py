"""Read and validate the bundled current-municipality snapshot."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = REGISTRY_DIR / "municipalities.csv"
DEFAULT_METADATA_PATH = REGISTRY_DIR / "metadata.json"
REQUIRED_COLUMNS = (
    "prefecture_name",
    "municipality_name",
    "name_aliases",
    "prefecture_code_2",
    "area_code_5",
    "local_government_code_6",
    "region_level",
    "official_home_url",
    "valid_from",
    "valid_to",
    "code_source_url",
    "home_source_url",
)


class RegistryError(RuntimeError):
    """Raised when the bundled registry is missing or inconsistent."""


def normalize_name(value: str) -> str:
    """NFC-normalize a registry lookup value."""

    return unicodedata.normalize("NFC", value).replace("\u3000", " ").strip()


def _expected_code(area_code_5: str) -> str:
    weighted_sum = sum(
        int(digit) * weight
        for digit, weight in zip(area_code_5, (6, 5, 4, 3, 2), strict=True)
    )
    return area_code_5 + str((11 - (weighted_sum % 11)) % 10)


def _prefecture_matches(hint: str, prefecture: str) -> bool:
    normalized_hint = normalize_name(hint)
    normalized_prefecture = normalize_name(prefecture)
    suffixes = "都道府県"
    return normalized_hint == normalized_prefecture or normalized_hint.rstrip(
        suffixes
    ) == normalized_prefecture.rstrip(suffixes)


@lru_cache(maxsize=4)
def _load_registry_cached(path_text: str) -> tuple[dict[str, str], ...]:
    path = Path(path_text)
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
                raise RegistryError(
                    "自治体registryの列が契約と一致しません: "
                    f"{path}: {reader.fieldnames}"
                )
            return tuple(
                {key: normalize_name(value) for key, value in row.items()}
                for row in reader
            )
    except OSError as error:
        raise RegistryError(
            f"自治体registryを読み込めません: {path}: {error}"
        ) from error


def load_registry(
    path: Path = DEFAULT_REGISTRY_PATH,
) -> tuple[dict[str, str], ...]:
    """Load the packaged CSV snapshot."""

    return _load_registry_cached(str(path.resolve()))


@lru_cache(maxsize=4)
def _load_metadata_cached(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(
            f"自治体registry metadataを読み込めません: {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise RegistryError(f"自治体registry metadataがobjectではありません: {path}")
    return payload


def load_metadata(path: Path = DEFAULT_METADATA_PATH) -> dict[str, Any]:
    """Load snapshot provenance."""

    return dict(_load_metadata_cached(str(path.resolve())))


def lookup(
    name: str,
    prefecture_hint: str | None,
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> tuple[str, list[dict[str, str]]]:
    """Return exact current matches and the municipality part of the input."""

    requested_name = normalize_name(name)
    rows = load_registry(registry_path)
    prefectures = sorted(
        {row["prefecture_name"] for row in rows},
        key=len,
        reverse=True,
    )
    embedded_hint: str | None = None
    search_name = requested_name
    for prefecture in prefectures:
        if requested_name.startswith(prefecture):
            embedded_hint = prefecture
            search_name = requested_name[len(prefecture) :].strip()
            break

    matches = [
        dict(row)
        for row in rows
        if normalize_name(row["municipality_name"]) == search_name
        or search_name
        in {
            normalize_name(alias)
            for alias in row.get("name_aliases", "").split("|")
            if alias
        }
    ]
    hint = prefecture_hint or embedded_hint
    if hint:
        matches = [
            row
            for row in matches
            if _prefecture_matches(hint, row["prefecture_name"])
        ]
    matches.sort(key=lambda row: row["area_code_5"])
    return search_name, matches


def validate_registry(
    rows: tuple[dict[str, str], ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate codes, URLs, uniqueness, source coverage, and record count."""

    registry_rows = rows if rows is not None else load_registry()
    registry_metadata = metadata if metadata is not None else load_metadata()
    errors: list[str] = []
    area_codes: set[str] = set()
    local_codes: set[str] = set()
    identities: set[tuple[str, str]] = set()
    allowed_levels = {"8", "9", "10", "12", "13"}
    sources = registry_metadata.get("sources", [])
    source_urls = {
        str(item.get("url", ""))
        for item in sources
        if isinstance(item, dict)
    }

    for index, row in enumerate(registry_rows, start=2):
        area_code = row.get("area_code_5", "")
        local_code = row.get("local_government_code_6", "")
        identity = (
            row.get("prefecture_name", ""),
            row.get("municipality_name", ""),
        )
        if not re.fullmatch(r"\d{5}", area_code):
            errors.append(f"line {index}: invalid area_code_5: {area_code}")
        elif local_code != _expected_code(area_code):
            errors.append(
                f"line {index}: check digit mismatch: {area_code}/{local_code}"
            )
        if row.get("prefecture_code_2") != area_code[:2]:
            errors.append(f"line {index}: prefecture code mismatch")
        if row.get("region_level") not in allowed_levels:
            errors.append(f"line {index}: unsupported region level")
        for key in ("official_home_url", "code_source_url", "home_source_url"):
            value = row.get(key, "")
            if not re.match(r"^https?://", value):
                errors.append(f"line {index}: invalid {key}: {value}")
        for key in ("code_source_url", "home_source_url"):
            if row.get(key, "") not in source_urls:
                errors.append(f"line {index}: {key} is missing from metadata")
        if area_code in area_codes:
            errors.append(f"line {index}: duplicate area code: {area_code}")
        if local_code in local_codes:
            errors.append(f"line {index}: duplicate local code: {local_code}")
        if identity in identities:
            errors.append(f"line {index}: duplicate identity: {identity}")
        area_codes.add(area_code)
        local_codes.add(local_code)
        identities.add(identity)

    declared_count = registry_metadata.get("record_count")
    if declared_count != len(registry_rows):
        errors.append(
            f"metadata record_count mismatch: {declared_count}/{len(registry_rows)}"
        )
    declared_hash = registry_metadata.get("registry_sha256")
    if rows is None and declared_hash:
        actual_hash = hashlib.sha256(DEFAULT_REGISTRY_PATH.read_bytes()).hexdigest()
        if declared_hash != actual_hash:
            errors.append(
                f"registry SHA-256 mismatch: {declared_hash}/{actual_hash}"
            )
    if errors:
        preview = " / ".join(errors[:10])
        suffix = f"（ほか{len(errors) - 10}件）" if len(errors) > 10 else ""
        raise RegistryError(f"自治体registry検証失敗: {preview}{suffix}")
    return {
        "status": "ok",
        "record_count": len(registry_rows),
        "prefecture_count": len(
            {row["prefecture_code_2"] for row in registry_rows}
        ),
        "generated_at": registry_metadata.get("generated_at"),
    }
