"""Validator for municipality source profiles (standard library only)."""

from __future__ import annotations

import datetime
import re
import urllib.parse
from typing import Any

from bootstrap.municipalities.registry import load_registry

ALLOWED_STATUSES = {
    "ready",
    "needs_review",
    "unsupported",
    "not_found",
    "blocked",
    "not_evaluated",
}
ALLOWED_ADAPTERS = {
    None,
    "kaigiroku_net",
    "static",
    "g_reiki",
    "dbsr",
    "voices",
    "d1_law",
    "joureikun",
    "official_document_index",
}

# Entry keys that are mutually exclusive
ENTRY_KEYS = ("base_url", "index_url", "tenant_url")

# Adapter -> required entry key
ADAPTER_REQUIRED_ENTRY: dict[str, str] = {
    "g_reiki": "base_url",
    "static": "index_url",
    "kaigiroku_net": "tenant_url",
    "d1_law": "index_url",
    "joureikun": "index_url",
    "official_document_index": "index_url",
    "dbsr": "index_url",
    "voices": "index_url",
}

ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _parse_iso8601(value: str) -> datetime.datetime | None:
    """Parse strict UTC ISO8601 like 2026-08-20T11:41:46Z."""
    if not ISO8601_RE.match(value):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None


def _host(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return parsed.netloc.lower()
    except Exception:
        return None


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        p = urllib.parse.urlsplit(value)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def validate_profile(data: dict[str, Any]) -> list[str]:
    """Validate a source profile dict and return error messages (empty = ok)."""
    errors: list[str] = []

    # Top-level required keys
    for key in (
        "schema_version",
        "area_code_5",
        "prefecture",
        "municipality",
        "official_home_url",
        "sources",
    ):
        if key not in data:
            errors.append(f"missing required key: {key}")

    # Early exit if missing keys prevents further checks, but continue to report multiple
    if errors:
        # still check basics that exist
        pass

    # schema_version
    if "schema_version" in data and data["schema_version"] != 1:
        errors.append("schema_version must be 1")

    # area_code_5
    area = data.get("area_code_5")
    if area is not None:
        if not isinstance(area, str) or not re.fullmatch(r"\d{5}", area):
            errors.append("area_code_5 must be a 5-digit string")

    # prefecture / municipality
    for field in ("prefecture", "municipality"):
        val = data.get(field)
        if val is not None and (not isinstance(val, str) or not val.strip()):
            errors.append(f"{field} must be a non-empty string")

    # official_home_url
    home = data.get("official_home_url")
    if home is not None and not _is_http_url(home):
        errors.append("official_home_url must be an http(s) URL")

    # sources
    sources = data.get("sources")
    if sources is not None:
        if not isinstance(sources, dict):
            errors.append("sources must be an object")
        else:
            for kind in ("minutes", "regulations", "budget", "settlement"):
                if kind not in sources:
                    errors.append(f"sources missing key: {kind}")
                    continue
                entry = sources[kind]
                if not isinstance(entry, dict):
                    errors.append(f"sources.{kind} must be an object")
                    continue
                # status
                status = entry.get("status")
                if status not in ALLOWED_STATUSES:
                    errors.append(
                        f"sources.{kind}.status must be one of {sorted(ALLOWED_STATUSES)}"
                    )
                # adapter
                adapter = entry.get("adapter")
                # JSON null -> None, string values must be in allowed (excluding None set)
                if adapter not in ALLOWED_ADAPTERS:
                    errors.append(
                        f"sources.{kind}.adapter must be one of {sorted([str(a) for a in ALLOWED_ADAPTERS])}"
                    )
                # entry keys exclusivity
                present_entries: list[str] = []
                for ek in ENTRY_KEYS:
                    val = entry.get(ek)
                    # treat missing or None as absent
                    if val is not None:
                        if not isinstance(val, str) or not val.strip():
                            errors.append(
                                f"sources.{kind}.{ek} must be a non-empty string or null"
                            )
                        elif not _is_http_url(val):
                            errors.append(f"sources.{kind}.{ek} must be an http(s) URL")
                        else:
                            present_entries.append(ek)

                if len(present_entries) > 1:
                    errors.append(
                        f"sources.{kind}: entry keys are mutually exclusive, found {present_entries}"
                    )

                # adapter-specific required entry
                if isinstance(adapter, str) and adapter in ADAPTER_REQUIRED_ENTRY:
                    required = ADAPTER_REQUIRED_ENTRY[adapter]
                    if required not in present_entries:
                        # Only require if status not in not_evaluated? But spec says g_reiki must have base_url etc.
                        # For unsupported statuses, we still expect entry for d1_law/joureikun but g_reiki needs_review also requires.
                        # We enforce requirement when adapter is not None
                        errors.append(
                            f"sources.{kind}: adapter {adapter} requires {required}"
                        )
                    # Also ensure no other entry present (already checked exclusivity)
                elif adapter is None:
                    if present_entries:
                        errors.append(
                            f"sources.{kind}: adapter is null but entry URL present {present_entries}"
                        )
                # For other adapters like dbsr etc., if present_entries ==0 while adapter not None, may be okay for some statuses?
                # But we already handled required for known adapters; for unknown adapter absence is already reported.

                # ready conditions
                if status == "ready":
                    ver_at = entry.get("verified_at")
                    ver_by = entry.get("verified_by")
                    evidence = entry.get("evidence")
                    # verified_at must be non-null ISO8601 not future
                    if ver_at is None:
                        errors.append(f"sources.{kind}: ready requires verified_at")
                    elif not isinstance(ver_at, str):
                        errors.append(
                            f"sources.{kind}: verified_at must be ISO8601 string"
                        )
                    else:
                        dt = _parse_iso8601(ver_at)
                        if dt is None:
                            errors.append(
                                f"sources.{kind}: verified_at must be ISO8601 UTC (YYYY-MM-DDTHH:MM:SSZ)"
                            )
                        else:
                            now = datetime.datetime.now(datetime.timezone.utc)
                            if dt > now:
                                errors.append(
                                    f"sources.{kind}: verified_at must not be in the future"
                                )
                    if not isinstance(ver_by, str) or not ver_by.strip():
                        errors.append(f"sources.{kind}: ready requires verified_by")
                    if adapter is None:
                        errors.append(f"sources.{kind}: ready requires adapter")
                    if not present_entries:
                        errors.append(
                            f"sources.{kind}: ready requires an entry URL (base_url/index_url/tenant_url)"
                        )
                    if not isinstance(evidence, list) or len(evidence) == 0:
                        errors.append(
                            f"sources.{kind}: ready requires evidence with at least 1 entry"
                        )
                    else:
                        # validate evidence entries
                        for idx, ev in enumerate(evidence):
                            if not isinstance(ev, dict):
                                errors.append(
                                    f"sources.{kind}.evidence[{idx}] must be an object"
                                )
                                continue
                            url = ev.get("url")
                            obs = ev.get("observed_on")
                            if not isinstance(url, str) or not _is_http_url(url):
                                errors.append(
                                    f"sources.{kind}.evidence[{idx}].url must be http(s) URL"
                                )
                            if obs is not None and (
                                not isinstance(obs, str) or not _is_http_url(obs)
                            ):
                                errors.append(
                                    f"sources.{kind}.evidence[{idx}].observed_on must be http(s) URL or null"
                                )

                else:
                    # For non-ready, if evidence present, validate its shape
                    evidence = entry.get("evidence")
                    if evidence is not None:
                        if not isinstance(evidence, list):
                            errors.append(f"sources.{kind}.evidence must be a list")
                        else:
                            for idx, ev in enumerate(evidence):
                                if not isinstance(ev, dict):
                                    errors.append(
                                        f"sources.{kind}.evidence[{idx}] must be an object"
                                    )
                                    continue
                                url = ev.get("url")
                                obs = ev.get("observed_on")
                                if url is not None and (
                                    not isinstance(url, str) or not _is_http_url(url)
                                ):
                                    errors.append(
                                        f"sources.{kind}.evidence[{idx}].url must be http(s) URL"
                                    )
                                if obs is not None and (
                                    not isinstance(obs, str) or not _is_http_url(obs)
                                ):
                                    errors.append(
                                        f"sources.{kind}.evidence[{idx}].observed_on must be http(s) URL"
                                    )

                    # verified_at if present must be valid ISO8601 and not future
                    ver_at = entry.get("verified_at")
                    if ver_at is not None:
                        if not isinstance(ver_at, str):
                            errors.append(
                                f"sources.{kind}.verified_at must be string or null"
                            )
                        elif _parse_iso8601(ver_at) is None:
                            errors.append(
                                f"sources.{kind}.verified_at must be ISO8601 UTC"
                            )
                        else:
                            dt = _parse_iso8601(ver_at)
                            if dt is not None:
                                now = datetime.datetime.now(datetime.timezone.utc)
                                if dt > now:
                                    errors.append(
                                        f"sources.{kind}.verified_at must not be in the future"
                                    )

                # config validation (stdlib only, no guessing)
                config = entry.get("config")
                if config is not None:
                    if not isinstance(config, dict):
                        errors.append(f"sources.{kind}.config must be an object")
                    else:
                        for regex_key in (
                            "follow_link_regex",
                            "link_include_regex",
                            "link_exclude_regex",
                        ):
                            val = config.get(regex_key)
                            if val is not None:
                                if not isinstance(val, str):
                                    errors.append(
                                        f"sources.{kind}.config.{regex_key} must be a string"
                                    )
                                elif val.strip():
                                    try:
                                        re.compile(val)
                                    except re.error as exc:
                                        errors.append(
                                            f"sources.{kind}.config.{regex_key} is not a valid regex: {exc}"
                                        )
                # 推測禁止: if entry URL exists, evidence must have at least 1 and host must match
                if present_entries:
                    entry_url = None
                    for ek in ENTRY_KEYS:
                        v = entry.get(ek)
                        if isinstance(v, str) and v.strip():
                            entry_url = v
                            break
                    if entry_url is not None:
                        ev_list = entry.get("evidence")
                        if not isinstance(ev_list, list) or len(ev_list) == 0:
                            errors.append(
                                f"sources.{kind}: entry URL requires evidence with at least 1 entry"
                            )
                        else:
                            entry_host = _host(entry_url)
                            found = False
                            for ev in ev_list:
                                if not isinstance(ev, dict):
                                    continue
                                for field in ("url", "observed_on"):
                                    val = ev.get(field)
                                    if isinstance(val, str) and _is_http_url(val):
                                        if _host(val) == entry_host:
                                            found = True
                                            break
                                if found:
                                    break
                            if not found:
                                errors.append(
                                    f"sources.{kind}: entry URL host must match at least one evidence url/observed_on host"
                                )

    # Registry cross-check
    if isinstance(area, str) and re.fullmatch(r"\d{5}", area):
        try:
            rows = load_registry()
            matched: dict[str, str] | None = None
            for row in rows:
                if row.get("area_code_5") == area:
                    matched = row
                    break
            if matched is None:
                errors.append(f"area_code_5 {area} not found in registry")
            else:
                # prefecture check
                prof_pref = data.get("prefecture")
                if isinstance(prof_pref, str) and prof_pref != matched.get(
                    "prefecture_name"
                ):
                    errors.append(
                        f"prefecture mismatch: profile {prof_pref!r} vs registry {matched.get('prefecture_name')!r}"
                    )
                prof_muni = data.get("municipality")
                if isinstance(prof_muni, str) and prof_muni != matched.get(
                    "municipality_name"
                ):
                    errors.append(
                        f"municipality mismatch: profile {prof_muni!r} vs registry {matched.get('municipality_name')!r}"
                    )
                prof_home = data.get("official_home_url")
                if isinstance(prof_home, str) and prof_home != matched.get(
                    "official_home_url"
                ):
                    errors.append(
                        f"official_home_url mismatch: profile {prof_home!r} vs registry {matched.get('official_home_url')!r}"
                    )
        except Exception as exc:
            errors.append(f"registry check failed: {exc}")

    return errors
