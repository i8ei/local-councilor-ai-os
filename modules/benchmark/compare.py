#!/usr/bin/env python3
"""Compare official indicators across municipalities in a benchmark DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from .presets import PRESETS, Preset, get_preset

# Monetary unit scales for cross-unit ranking (mod-03). Only these scale
# deterministically; any other unit (指数・人・世帯など) cannot be converted,
# so mixed non-scalable units refuse to rank instead of silently comparing
# raw numbers across different units.
_UNIT_SCALES: dict[str, float] = {
    "円": 1.0,
    "千円": 1_000.0,
    "百万円": 1_000_000.0,
    "億円": 100_000_000.0,
}


def _compare_sort_key(item: dict[str, Any]) -> tuple[int, float, str, str]:
    """Rank key: NULLs last, then (normalized) value desc, then name."""
    value = item.get("value")
    if value is None:
        return (1, 0.0, str(item.get("prefecture") or ""), str(item.get("name") or ""))
    scale = _UNIT_SCALES.get(str(item.get("unit") or ""), 1.0)
    return (0, -float(value) * scale, str(item.get("prefecture") or ""), str(item.get("name") or ""))


def compare(
    connection: sqlite3.Connection,
    indicator: str,
    *,
    as_of: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if as_of is None:
        row = connection.execute(
            "SELECT MAX(as_of) FROM benchmark_indicator WHERE indicator_key = ?",
            (indicator,),
        ).fetchone()
        as_of = row[0] if row else None
    if not as_of:
        return {"indicator_key": indicator, "as_of": None, "items": [], "missing_or_unresolved": ["indicator not found"]}
    rows = connection.execute(
        """
        SELECT
            m.area_code_5, m.local_government_code_6, m.name, m.prefecture,
            i.indicator_key, i.value, i.raw_value, i.unit, i.as_of,
            i.definition, i.source_name, i.source_url, i.source_locator,
            i.fetched_at, i.verification_state
        FROM benchmark_indicator AS i
        JOIN benchmark_municipality AS m ON m.area_code_5 = i.area_code_5
        WHERE i.indicator_key = ? AND i.as_of = ?
        """,
        (indicator, as_of),
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        try:
            item["source_locator"] = json.loads(item["source_locator"])
        except (TypeError, json.JSONDecodeError):
            pass
    unresolved: list[str] = [] if items else ["indicator/as_of combination not found"]

    # mod-04: an auto/selected as_of silently dropping municipalities that
    # have the indicator at another vintage was the reported bug; name them.
    if items:
        for mrow in connection.execute(
            """
            SELECT m.name, m.area_code_5, MAX(i.as_of) AS latest
            FROM benchmark_indicator AS i
            JOIN benchmark_municipality AS m ON m.area_code_5 = i.area_code_5
            WHERE i.indicator_key = ?
              AND i.area_code_5 NOT IN (
                  SELECT area_code_5 FROM benchmark_indicator
                  WHERE indicator_key = ? AND as_of = ?
              )
            GROUP BY m.area_code_5
            ORDER BY m.name
            """,
            (indicator, indicator, as_of),
        ):
            unresolved.append(
                f"{mrow['name']} ({mrow['area_code_5']}): no data at {as_of}; "
                f"latest available {mrow['latest']}"
            )

    # mod-03: value ranking must not silently mix units (derived-value
    # computation already refuses on unit_mismatch; compare() was the one
    # path that ignored units). Normalize when every unit is a known
    # monetary scale; otherwise refuse to rank and keep rows visible.
    units = sorted({str(item["unit"]) for item in items if item.get("unit")})
    cross_unit = len(units) >= 2
    unit_note: dict[str, Any] | None = None
    order = "value_desc_nulls_last"
    if cross_unit:
        if all(u in _UNIT_SCALES for u in units):
            order = "value_desc_nulls_last_unit_normalized"
            unit_note = {
                "scales": {u: _UNIT_SCALES[u] for u in units},
                "note": "units normalized to a common scale for ranking; "
                "each item keeps its own value/unit for display.",
            }
        else:
            order = "unranked_unit_mismatch"
            unresolved.append(
                f"unit_mismatch: mixed units {units} include a non-monetary "
                "unit; ranked comparison refused (rows kept, sorted by name)"
            )
    if order == "unranked_unit_mismatch":
        items.sort(
            key=lambda item: (
                str(item.get("prefecture") or ""),
                str(item.get("name") or ""),
                str(item.get("area_code_5") or ""),
            )
        )
    else:
        items.sort(key=_compare_sort_key)
    returned = items[:limit]
    return {
        "indicator_key": indicator,
        "as_of": as_of,
        "order": order,
        "unit_normalization": unit_note,
        "units": units,
        "items": returned,
        "external_use_note": "対外利用時は各itemsの value/as_of/definition/source_name/source_url を一体で表示する。",
        "missing_or_unresolved": unresolved,
    }


def _source_value(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "value": None,
            "as_of": None,
            "definition": None,
            "source": None,
            "raw_value": None,
            "unit": None,
            "fetched_at": None,
            "verification_state": None,
            "derived": False,
        }
    locator: Any = row["source_locator"]
    try:
        locator = json.loads(locator)
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "value": row["value"],
        "as_of": row["as_of"],
        "definition": row["definition"],
        "source": {
            "name": row["source_name"],
            "url": row["source_url"],
            "locator": locator,
        },
        "raw_value": row["raw_value"],
        "unit": row["unit"],
        "fetched_at": row["fetched_at"],
        "verification_state": row["verification_state"],
        "derived": False,
    }


def _derived_value(
    preset: Preset,
    values: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    derived: dict[str, dict[str, Any]] = {}
    for spec in preset.derived_indicators:
        left = values[spec.input_keys[0]]
        right = values[spec.input_keys[1]]
        left_value = left["value"]
        right_value = right["value"]
        same_as_of = (
            left["as_of"] is not None
            and left["as_of"] == right["as_of"]
        )
        same_unit = (
            left["unit"] is not None
            and left["unit"] == right["unit"]
        )
        if left_value is None or right_value is None:
            result = None
            status = "not_computed"
            reason = "missing_input"
        elif not same_as_of:
            result = None
            status = "not_computed"
            reason = "as_of_mismatch"
        elif not same_unit:
            result = None
            status = "not_computed"
            reason = "unit_mismatch"
        else:
            result = left_value - right_value
            status = "computed"
            reason = None
        derived[spec.key] = {
            "value": result,
            "as_of": left["as_of"] if same_as_of else None,
            "definition": spec.definition,
            "source": None,
            "raw_value": None,
            "unit": left["unit"] if same_unit else None,
            "fetched_at": None,
            "verification_state": None,
            "derived": True,
            "derivation": {
                "formula": spec.formula,
                "input_indicator_keys": list(spec.input_keys),
                "status": status,
                "reason": reason,
            },
        }
    return derived


def _row_same_as_of(
    values: dict[str, dict[str, Any]],
    indicator_keys: tuple[str, ...],
) -> dict[str, Any]:
    observed = sorted(
        {
            values[key]["as_of"]
            for key in indicator_keys
            if values[key]["as_of"] is not None
        }
    )
    missing = [
        key for key in indicator_keys if values[key]["as_of"] is None
    ]
    if len(observed) > 1:
        status = "mismatch"
    elif missing:
        status = "incomplete"
    elif observed:
        status = "match"
    else:
        status = "no_data"
    return {
        "status": status,
        "observed": observed,
        "missing_indicator_keys": missing,
    }


def compare_preset(
    connection: sqlite3.Connection,
    preset: str | Preset,
    *,
    as_of: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Compare a complete indicator bundle without hiding vintage mismatches."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected = get_preset(preset) if isinstance(preset, str) else preset
    connection.row_factory = sqlite3.Row
    municipalities = connection.execute(
        """
        SELECT
            area_code_5, local_government_code_6, name, prefecture
        FROM benchmark_municipality
        ORDER BY prefecture, name, area_code_5
        """
    ).fetchall()
    placeholders = ", ".join("?" for _ in selected.indicator_keys)
    parameters: list[Any] = list(selected.indicator_keys)
    as_of_clause = ""
    if as_of is not None:
        as_of_clause = "AND as_of = ?"
        parameters.append(as_of)
    indicator_rows = connection.execute(
        f"""
        SELECT
            area_code_5, indicator_key, value, raw_value, unit, as_of,
            definition, source_name, source_url, source_locator, fetched_at,
            verification_state
        FROM benchmark_indicator
        WHERE indicator_key IN ({placeholders})
          {as_of_clause}
        ORDER BY
            area_code_5,
            indicator_key,
            as_of DESC,
            source_url
        """,
        parameters,
    ).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in indicator_rows:
        latest.setdefault((row["area_code_5"], row["indicator_key"]), row)

    rows: list[dict[str, Any]] = []
    observed_all: set[str] = set()
    mismatch_rows: list[str] = []
    incomplete_rows: list[str] = []
    as_of_by_area_code: dict[str, list[str]] = {}
    for municipality in municipalities:
        code = municipality["area_code_5"]
        values = {
            key: _source_value(latest.get((code, key)))
            for key in selected.indicator_keys
        }
        check = _row_same_as_of(values, selected.indicator_keys)
        observed_all.update(check["observed"])
        as_of_by_area_code[code] = check["observed"]
        if check["status"] == "mismatch":
            mismatch_rows.append(code)
        if check["status"] in {"incomplete", "no_data"}:
            incomplete_rows.append(code)
        values.update(_derived_value(selected, values))
        null_keys = [
            key
            for key in selected.indicator_keys
            if values[key]["value"] is None
        ]
        rows.append(
            {
                "area_code_5": code,
                "local_government_code_6": municipality[
                    "local_government_code_6"
                ],
                "name": municipality["name"],
                "prefecture": municipality["prefecture"],
                "values": values,
                "same_as_of": check,
                "null_indicator_keys": null_keys,
                "bundle_complete": not null_keys and check["status"] == "match",
            }
        )

    rows.sort(
        key=lambda row: (
            not row["bundle_complete"],
            row["prefecture"],
            row["name"],
            row["area_code_5"],
        )
    )
    if len(observed_all) > 1 or mismatch_rows:
        overall_status = "mismatch"
    elif incomplete_rows:
        overall_status = "incomplete"
    elif observed_all:
        overall_status = "match"
    else:
        overall_status = "no_data"
    complete = all(row["bundle_complete"] for row in rows)
    total_rows = len(rows)
    returned_rows = rows[:limit]
    return {
        "preset_meta": selected.to_meta(),
        "requested_as_of": as_of,
        "same_as_of_check": {
            "status": overall_status,
            "observed": sorted(observed_all),
            "mismatched_area_codes": mismatch_rows,
            "incomplete_area_codes": incomplete_rows,
            "as_of_by_area_code": as_of_by_area_code,
            "comparison_allowed": overall_status == "match",
        },
        "bundle_comparison_allowed": overall_status == "match" and complete,
        "order": "complete_rows_then_municipality_name; no indicator ranking",
        "rows": returned_rows,
        "row_count": len(returned_rows),
        "total_municipalities": total_rows,
        "truncated": len(returned_rows) < total_rows,
        "external_use_note": (
            "各valuesの value/as_of/definition/source を一体で表示する。"
            "不一致・欠測を除外やゼロ置換で隠さない。"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("indicator_key", nargs="?")
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument("--db", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if bool(args.indicator_key) == bool(args.preset):
        parser.error("specify exactly one indicator_key or --preset")
    try:
        with closing(sqlite3.connect(Path(args.db))) as connection:
            if args.preset:
                result = compare_preset(
                    connection,
                    args.preset,
                    as_of=args.as_of,
                    limit=args.limit,
                )
            else:
                result = compare(
                    connection,
                    args.indicator_key,
                    as_of=args.as_of,
                    limit=args.limit,
                )
    except (sqlite3.Error, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
