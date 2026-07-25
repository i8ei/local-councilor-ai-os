"""Load and validate the bundled municipality observatory snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bootstrap.municipalities import load_metadata, load_registry

OBSERVATORY_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = OBSERVATORY_DIR / "manifest.json"
DEFAULT_SNAPSHOT_PATH = OBSERVATORY_DIR / "municipalities.jsonl"
SOURCE_KINDS = ("minutes", "regulations", "budget", "settlement")
LANES = {
    "source_run_stopped",
    "depth1_missing",
    "depth1_no_candidates",
    "depth1_partial",
    "depth1_stopped",
    "source_gap",
    "covered",
}
NAVIGATION_MODES = {"static", "javascript_candidate", "unknown"}


class ObservatoryError(RuntimeError):
    """Raised when the bundled observatory snapshot is inconsistent."""


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObservatoryError(f"{label} must be an object")
    return value


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ObservatoryError(f"{label} must be a string array")
    return list(value)


def _validate_record(
    value: object,
    *,
    line_number: int,
    registry_by_code: dict[str, dict[str, str]],
) -> dict[str, Any]:
    record = _object(value, f"line {line_number}")
    code = record.get("area_code_5")
    if not isinstance(code, str) or not re.fullmatch(r"\d{5}", code):
        raise ObservatoryError(f"line {line_number}: invalid area_code_5")
    registry = registry_by_code.get(code)
    if registry is None:
        raise ObservatoryError(f"line {line_number}: unknown area_code_5: {code}")
    for key in ("prefecture_name", "municipality_name"):
        if record.get(key) != registry[key]:
            raise ObservatoryError(
                f"line {line_number}: registry identity mismatch: {code}/{key}"
            )
    if record.get("lane") not in LANES:
        raise ObservatoryError(f"line {line_number}: invalid lane")
    if record.get("navigation_mode") not in NAVIGATION_MODES:
        raise ObservatoryError(f"line {line_number}: invalid navigation_mode")

    source_kinds = _strings(
        record.get("source_kinds"),
        f"line {line_number}.source_kinds",
    )
    if source_kinds != [
        kind for kind in SOURCE_KINDS if kind in set(source_kinds)
    ]:
        raise ObservatoryError(
            f"line {line_number}: source_kinds must be unique and ordered"
        )
    source_urls = _object(
        record.get("source_urls"),
        f"line {line_number}.source_urls",
    )
    if set(source_urls) != set(SOURCE_KINDS):
        raise ObservatoryError(
            f"line {line_number}: source_urls keys do not match the contract"
        )
    for kind in SOURCE_KINDS:
        urls = _strings(
            source_urls[kind],
            f"line {line_number}.source_urls.{kind}",
        )
        if urls != sorted(set(urls)):
            raise ObservatoryError(
                f"line {line_number}: source URLs must be unique and sorted"
            )
        if any(not re.match(r"^https?://", url) for url in urls):
            raise ObservatoryError(
                f"line {line_number}: invalid source URL for {kind}"
            )
    for key in ("candidate_pages", "vendor_signals", "stop_reasons"):
        values = _strings(record.get(key), f"line {line_number}.{key}")
        if values != sorted(set(values)):
            raise ObservatoryError(
                f"line {line_number}: {key} must be unique and sorted"
            )
    if any(
        not re.match(r"^https?://", url)
        for url in record["candidate_pages"]
    ):
        raise ObservatoryError(f"line {line_number}: invalid candidate page URL")
    return record


def load_catalog(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Load a hash-checked snapshot keyed by five-digit municipality code."""

    try:
        manifest = _object(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            "observatory manifest",
        )
        snapshot_bytes = snapshot_path.read_bytes()
    except (OSError, json.JSONDecodeError) as error:
        raise ObservatoryError(
            f"observatory snapshot cannot be read: {error}"
        ) from error
    if manifest.get("schema_version") != 1:
        raise ObservatoryError("unsupported observatory manifest schema")
    snapshot = _object(manifest.get("snapshot"), "manifest.snapshot")
    actual_hash = hashlib.sha256(snapshot_bytes).hexdigest()
    if snapshot.get("sha256") != actual_hash:
        raise ObservatoryError("observatory snapshot SHA-256 mismatch")

    municipality_metadata = load_metadata()
    registry_manifest = _object(
        manifest.get("municipality_registry"),
        "manifest.municipality_registry",
    )
    if (
        registry_manifest.get("sha256")
        != municipality_metadata.get("registry_sha256")
    ):
        raise ObservatoryError(
            "observatory snapshot targets a different municipality registry"
        )
    registry_rows = load_registry()
    registry_by_code = {row["area_code_5"]: row for row in registry_rows}
    records: dict[str, dict[str, Any]] = {}
    try:
        lines = snapshot_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ObservatoryError("observatory snapshot is not UTF-8") from error
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ObservatoryError(
                f"observatory snapshot line {line_number} is invalid JSON"
            ) from error
        record = _validate_record(
            payload,
            line_number=line_number,
            registry_by_code=registry_by_code,
        )
        code = str(record["area_code_5"])
        if code in records:
            raise ObservatoryError(f"duplicate observatory code: {code}")
        records[code] = record

    declared_count = snapshot.get("record_count")
    if declared_count != len(records) or len(records) != len(registry_rows):
        raise ObservatoryError(
            "observatory snapshot record count does not match the registry"
        )
    if set(records) != set(registry_by_code):
        raise ObservatoryError(
            "observatory snapshot municipality coverage is incomplete"
        )
    return {
        "manifest": manifest,
        "records": records,
    }


def lookup(
    area_code_5: str,
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return one prior observation without promoting it to live evidence."""

    loaded = catalog if catalog is not None else load_catalog()
    records = _object(loaded.get("records"), "catalog.records")
    record = records.get(area_code_5)
    return dict(record) if isinstance(record, dict) else None
