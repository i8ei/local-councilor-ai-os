"""Evaluate source freshness without performing network requests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REGISTRY = (
    Path(__file__).resolve().parents[1]
    / "data-contracts"
    / "source_registry.v1.json"
)
ESTAT_INDICATORS = frozenset(
    {
        "population_total",
        "households_total",
        "population_65_plus_ratio",
    }
)
SOURCE_IDS = {
    "estat": "estat-api-v3",
    "fiscal": "soumu-municipal-fiscal-overview",
}
STATE_ORDER = {"fresh": 0, "due": 1, "unknown": 2, "stale": 3}


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def load_freshness_registry(path: str | Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    """Load the source registry used by the local freshness evaluator."""

    registry_path = Path(path)
    value = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(value.get("sources"), dict):
        raise ValueError("source registryにsources objectがありません")
    return value


def _policy(
    registry: dict[str, Any],
    source_id: str,
) -> dict[str, Any]:
    defaults = registry.get("defaults")
    default_freshness = (
        defaults.get("freshness", {})
        if isinstance(defaults, dict)
        else {}
    )
    source = registry["sources"].get(source_id)
    source_freshness = (
        source.get("freshness", {})
        if isinstance(source, dict)
        else {}
    )
    return {**default_freshness, **source_freshness}


def _source_rows(
    rows: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped = {"estat": [], "fiscal": []}
    for row in rows:
        key = str(row.get("indicator_key") or "")
        source_name = str(row.get("source_name") or "").lower()
        is_estat = key in ESTAT_INDICATORS or "e-stat" in source_name
        grouped["estat" if is_estat else "fiscal"].append(row)
    return grouped


def _evaluate_source(
    source_id: str,
    rows: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
    current_time: datetime,
    forced_stale_reason: str | None = None,
    forced_unknown_reason: str | None = None,
) -> dict[str, Any]:
    periods = sorted(
        {
            str(row.get("as_of"))
            for row in rows
            if row.get("as_of") not in (None, "")
        }
    )
    retrieved_values = [
        parsed
        for row in rows
        if (parsed := _parse_datetime(row.get("fetched_at"))) is not None
    ]
    checked_at = max(retrieved_values) if retrieved_values else None
    interval_value = policy.get("recommended_check_interval_days")
    try:
        interval_days = int(interval_value)
    except (TypeError, ValueError):
        interval_days = 0
    due_at = (
        checked_at + timedelta(days=interval_days)
        if checked_at is not None and interval_days > 0
        else None
    )

    if forced_stale_reason:
        state = "stale"
        reason = forced_stale_reason
    elif forced_unknown_reason:
        state = "unknown"
        reason = forced_unknown_reason
    elif not rows or checked_at is None or due_at is None:
        state = "unknown"
        reason = "source_period_or_latest_check_is_unknown"
    elif current_time > due_at:
        state = "due"
        reason = "recommended_source_check_interval_exceeded"
    else:
        state = "fresh"
        reason = "latest_period_was_checked_within_registry_interval"

    return {
        "source_id": source_id,
        "state": state,
        "reason": reason,
        "source_periods": periods,
        "retrieved_at": _format_datetime(checked_at),
        "latest_period_checked": periods,
        "checked_at": _format_datetime(checked_at),
        "check_due_at": _format_datetime(due_at),
        "recommended_check_interval_days": interval_days or None,
        "check_method": policy.get("check_method"),
        "period_semantics": policy.get("period_semantics"),
        "failure_policy": policy.get("failure_policy"),
    }


def evaluate_bootstrap_freshness(
    rows: Iterable[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    registry_path: str | Path = DEFAULT_REGISTRY,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate e-Stat and fiscal freshness from saved row provenance."""

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    registry = load_freshness_registry(registry_path)
    grouped = _source_rows(rows)
    census_selection = metadata.get("census_selection")
    census_fallback = bool(
        isinstance(census_selection, dict)
        and census_selection.get("used_fallback")
    )
    census_latest_check_known = bool(
        isinstance(census_selection, dict)
        and (
            census_selection.get("reason")
            or census_selection.get("tables")
        )
    )
    fiscal_discovery = metadata.get("fiscal_discovery")
    fiscal_latest_check_known = bool(
        isinstance(fiscal_discovery, dict)
        and (
            fiscal_discovery.get("index_url")
            or fiscal_discovery.get("year_page_url")
        )
    )
    sources = [
        _evaluate_source(
            SOURCE_IDS["estat"],
            grouped["estat"],
            policy=_policy(registry, SOURCE_IDS["estat"]),
            current_time=current_time,
            forced_stale_reason=(
                "census_fallback_used" if census_fallback else None
            ),
            forced_unknown_reason=(
                None
                if census_latest_check_known
                else "latest_census_selection_not_recorded"
            ),
        ),
        _evaluate_source(
            SOURCE_IDS["fiscal"],
            grouped["fiscal"],
            policy=_policy(registry, SOURCE_IDS["fiscal"]),
            current_time=current_time,
            forced_unknown_reason=(
                None
                if fiscal_latest_check_known
                else "latest_fiscal_discovery_not_recorded"
            ),
        ),
    ]
    state = max(
        (item["state"] for item in sources),
        key=lambda item: STATE_ORDER[item],
    )
    return {
        "state": state,
        "reason": "worst_source_state",
        "evaluated_at": _format_datetime(current_time),
        "sources": sources,
    }
