#!/usr/bin/env python3
"""Normalize a lcaios-explorer export into the bundled hint snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from bootstrap.municipalities import load_metadata, load_registry
from bootstrap.observatory.catalog import LANES, SOURCE_KINDS

OBSERVATORY_DIR = Path(__file__).resolve().parent


class ExportError(RuntimeError):
    """Raised when the explorer export cannot produce a safe snapshot."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _json_value(value: object, fallback: object) -> object:
    if value is None or value == "":
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            item
            for item in value
            if isinstance(item, str) and item.strip()
        }
    )


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        return None
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )


def _home_source_kinds(raw: object) -> set[str]:
    signals = _object(_json_value(raw, {}))
    return {
        kind
        for kind in SOURCE_KINDS
        if isinstance(signals.get(kind), dict)
        and isinstance(signals[kind].get("count"), int)
        and signals[kind]["count"] > 0
    }


def _source_urls(raw: object) -> dict[str, list[str]]:
    parsed = _object(_json_value(raw, {}))
    output: dict[str, list[str]] = {}
    for kind in SOURCE_KINDS:
        urls = {_safe_url(item) for item in _strings(parsed.get(kind))}
        output[kind] = sorted(url for url in urls if url is not None)
    return output


def _candidate_pages(raw: object) -> list[str]:
    parsed = _json_value(raw, [])
    if not isinstance(parsed, list):
        return []
    urls: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict) or item.get("status") != "acquired":
            continue
        url = _safe_url(item.get("candidateUrl"))
        if url:
            urls.add(url)
    return sorted(urls)


def _page_stop_reasons(raw: object) -> list[str]:
    parsed = _json_value(raw, [])
    if not isinstance(parsed, list):
        return []
    return _strings(
        [
            item.get("stopReason")
            for item in parsed
            if isinstance(item, dict)
        ]
    )


def _lane(
    home_status: str,
    depth1_status: str | None,
    source_kinds: set[str],
) -> str:
    if home_status != "acquired":
        return "source_run_stopped"
    if depth1_status is None:
        return "depth1_missing"
    if depth1_status == "no_candidates":
        return "depth1_no_candidates"
    if depth1_status == "partial":
        return "depth1_partial"
    if depth1_status != "acquired":
        return "depth1_stopped"
    return "covered" if source_kinds else "source_gap"


def normalize_rows(rows: list[object]) -> list[dict[str, Any]]:
    """Validate registry identity and normalize all explorer rows."""

    registry = {row["area_code_5"]: row for row in load_registry()}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(rows, start=1):
        if not isinstance(value, dict):
            raise ExportError(f"row {index} is not an object")
        code = value.get("municipality_code")
        if not isinstance(code, str) or not re.fullmatch(r"\d{5}", code):
            raise ExportError(f"row {index} has an invalid municipality code")
        expected = registry.get(code)
        if expected is None:
            raise ExportError(f"row {index} has an unknown municipality: {code}")
        if code in seen:
            raise ExportError(f"duplicate municipality: {code}")
        seen.add(code)
        for field in ("prefecture_name", "municipality_name"):
            if value.get(field) != expected[field]:
                raise ExportError(f"registry identity mismatch: {code}/{field}")

        feature = _object(_json_value(value.get("feature_json"), {}))
        navigation_mode = feature.get("navigationMode")
        if navigation_mode not in {"static", "javascript_candidate", "unknown"}:
            navigation_mode = "unknown"
        urls = _source_urls(value.get("depth1_new_source_urls_json"))
        source_kinds = _home_source_kinds(
            value.get("home_source_signals_json")
        )
        source_kinds.update(
            kind for kind in SOURCE_KINDS if urls[kind]
        )
        vendors = set(_strings(feature.get("vendorSignals")))
        vendors.update(
            _strings(
                _json_value(value.get("home_vendor_signals_json"), [])
            )
        )
        vendors.update(
            _strings(
                _json_value(
                    value.get("depth1_added_vendor_signals_json"),
                    [],
                )
            )
        )
        home_status = str(value.get("home_acquisition_status") or "missing")
        depth1_raw = value.get("depth1_acquisition_status")
        depth1_status = str(depth1_raw) if depth1_raw is not None else None
        stop_reasons = _strings(
            [
                value.get("home_stop_reason"),
                value.get("depth1_stop_reason"),
                *_page_stop_reasons(value.get("depth1_page_results_json")),
            ]
        )
        lane = _lane(home_status, depth1_status, source_kinds)
        if lane not in LANES:
            raise ExportError(f"invalid derived lane: {code}/{lane}")
        normalized.append(
            {
                "area_code_5": code,
                "prefecture_name": expected["prefecture_name"],
                "municipality_name": expected["municipality_name"],
                "observed_at": {
                    "home": value.get("home_observed_at"),
                    "depth1": value.get("depth1_observed_at"),
                },
                "lane": lane,
                "navigation_mode": navigation_mode,
                "profile_id": value.get("depth1_profile_id"),
                "acquisition": {
                    "home": home_status,
                    "depth1": depth1_status,
                },
                "source_kinds": [
                    kind for kind in SOURCE_KINDS if kind in source_kinds
                ],
                "vendor_signals": sorted(vendors),
                "source_urls": urls,
                "candidate_pages": _candidate_pages(
                    value.get("depth1_page_results_json")
                ),
                "stop_reasons": stop_reasons,
            }
        )
    missing = set(registry) - seen
    if missing or len(normalized) != len(registry):
        raise ExportError(
            "explorer export does not cover the bundled municipality registry: "
            f"rows={len(normalized)} missing={len(missing)}"
        )
    return sorted(normalized, key=lambda item: str(item["area_code_5"]))


def _extract_rows(payload: object) -> list[object]:
    if isinstance(payload, dict):
        rows = payload.get("results")
        if isinstance(rows, list):
            return rows
    if isinstance(payload, list) and len(payload) == 1:
        item = payload[0]
        if isinstance(item, dict) and isinstance(item.get("results"), list):
            return list(item["results"])
    raise ExportError("input must be a JSON result containing a results array")


def write_snapshot(
    records: list[dict[str, Any]],
    *,
    output_dir: Path,
    generated_at: str,
    source_run_id: str,
    depth1_pilot_ids: list[str],
    scope_version: int,
    explorer_revision: str,
    replace: bool,
) -> dict[str, Any]:
    """Write canonical JSONL and its provenance manifest."""

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "municipalities.jsonl"
    manifest_path = output_dir / "manifest.json"
    if not replace and (snapshot_path.exists() or manifest_path.exists()):
        raise ExportError("output exists; pass --replace to update the snapshot")
    snapshot_text = "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    )
    snapshot_bytes = snapshot_text.encode("utf-8")
    registry_metadata = load_metadata()
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "generator": "bootstrap.observatory.update",
        "explorer": {
            "repository": "i8ei/lcaios-explorer",
            "revision": explorer_revision,
            "source_run_id": source_run_id,
            "depth1_pilot_ids": depth1_pilot_ids,
            "scope_version": scope_version,
        },
        "municipality_registry": {
            "generated_at": registry_metadata.get("generated_at"),
            "sha256": registry_metadata.get("registry_sha256"),
        },
        "snapshot": {
            "file": snapshot_path.name,
            "record_count": len(records),
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        },
        "trust_boundary": {
            "classification": "deterministic_prior_observation",
            "ready_without_live_confirmation": False,
            "raw_html_included": False,
            "documents_included": False,
        },
    }
    snapshot_tmp = snapshot_path.with_suffix(".jsonl.tmp")
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    snapshot_tmp.write_bytes(snapshot_bytes)
    manifest_tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    snapshot_tmp.replace(snapshot_path)
    manifest_tmp.replace(manifest_path)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="lcaios-explorer exportをbootstrap用snapshotへ正規化"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=OBSERVATORY_DIR)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument(
        "--depth1-pilot-id",
        action="append",
        required=True,
    )
    parser.add_argument("--scope-version", required=True, type=int)
    parser.add_argument("--explorer-revision", required=True)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if len(args.depth1_pilot_id) != 2:
        print("ERROR: --depth1-pilot-id must be passed exactly twice", file=sys.stderr)
        return 2
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        records = normalize_rows(_extract_rows(payload))
        manifest = write_snapshot(
            records,
            output_dir=args.output_dir,
            generated_at=args.generated_at or _utc_now(),
            source_run_id=args.source_run_id,
            depth1_pilot_ids=args.depth1_pilot_id,
            scope_version=args.scope_version,
            explorer_revision=args.explorer_revision,
            replace=args.replace,
        )
    except (ExportError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
