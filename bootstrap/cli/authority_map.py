"""Emit a value-free per-municipality authority map."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


INDICATOR_INFO = {
    "population_total": (
        "総人口",
        "国勢調査の総人口。",
        ("municipality_profile", "longitudinal_comparison"),
    ),
    "households_total": (
        "総世帯数",
        "国勢調査の総世帯数。",
        ("municipality_profile", "service_demand_baseline"),
    ),
    "population_65_plus_ratio": (
        "65歳以上人口構成比",
        "国勢調査の公式公表構成比。",
        ("municipality_profile", "aging_policy"),
    ),
    "zaiseiryoku_shisuu": (
        "財政力指数",
        "市町村別決算状況調の財政力指数。",
        ("fiscal_health", "peer_comparison"),
    ),
    "keijou_shuushi_hiritsu": (
        "経常収支比率",
        "市町村別決算状況調の経常収支比率。",
        ("fiscal_health", "budget_scrutiny"),
    ),
    "jisshitsu_kousaihi_hiritsu": (
        "実質公債費比率",
        "市町村別決算状況調の実質公債費比率。",
        ("debt_risk", "budget_scrutiny"),
    ),
    "shourai_futan_hiritsu": (
        "将来負担比率",
        "市町村別決算状況調の将来負担比率。",
        ("debt_risk", "long_term_planning"),
    ),
    "total_revenue": (
        "歳入総額",
        "普通会計の歳入決算総額。",
        ("budget_scale", "budget_scrutiny"),
    ),
    "total_expenditure": (
        "歳出総額",
        "普通会計の歳出決算総額。",
        ("budget_scale", "budget_scrutiny"),
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _source_id(record: dict[str, Any]) -> str:
    if record["indicator"] in {
        "population_total",
        "households_total",
        "population_65_plus_ratio",
    }:
        return "estat_census"
    return "soumu_municipal_fiscal_overview"


def generate_authority_map(
    municipality: dict[str, Any],
    records: Iterable[dict[str, Any]],
    output_path: str | Path,
    *,
    database_name: str,
    census_warning: str | None = None,
) -> dict[str, Any]:
    """Generate routes and locators only; never duplicate indicator values."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    by_indicator = {str(record["indicator"]): record for record in records}
    lines = [
        "# Generated authority routes. Indicator values are intentionally absent.",
        'schema_version: "1"',
        f"generated_at: {_quote(_utc_now())}",
        "municipality:",
        f"  area_code_5: {_quote(municipality['area_code_5'])}",
        (
            "  local_government_code_6: "
            f"{_quote(municipality['local_government_code_6'])}"
        ),
        f"  name: {_quote(municipality['name'])}",
        f"  prefecture: {_quote(municipality['prefecture'])}",
        "indicators:",
    ]
    for indicator, (label, description, use_cases) in INDICATOR_INFO.items():
        if indicator not in by_indicator:
            raise ValueError(f"authority map対象指標がありません: {indicator}")
        record = by_indicator[indicator]
        source_id = _source_id(record)
        source_locator = record["source_locator"]
        table_id = source_locator.get("stats_data_id")
        geography_width = 5 if source_id == "estat_census" else 6
        caveats: list[str] = []
        if source_id == "estat_census" and census_warning:
            caveats.append(census_warning)
        if indicator == "population_65_plus_ratio":
            caveats.append("年齢不詳の分母上の扱いは原表定義の確認が必要。")
        if record.get("value") is None:
            caveats.append("原表は欠測記号。ゼロとして扱わない。")
        cross_check = source_locator.get("cross_check")
        lines.extend(
            [
                f"  {indicator}:",
                f"    label: {_quote(label)}",
                f"    description: {_quote(description)}",
                f"    canonical_unit: {_quote(record['unit'])}",
                (
                    "    definition_reference: "
                    f"{_quote(source_id + '#' + indicator)}"
                ),
                "    use_cases:",
            ]
        )
        for use_case in use_cases:
            lines.extend(
                [
                    f"      {use_case}:",
                    "        primary:",
                    f"          source_id: {_quote(source_id)}",
                    f"          locator_id: {_quote(indicator)}",
                    "          dataset_id: null",
                    (
                        f"          table_id: {_quote(table_id)}"
                        if table_id
                        else "          table_id: null"
                    ),
                    f"          field: {_quote(indicator)}",
                    f"          source_url: {_quote(record['source_url'])}",
                    (
                        "          sqlite_locator: "
                        + _quote(
                            f"{database_name}::indicator"
                            f"[municipality_code={municipality['area_code_5']},"
                            f"indicator_key={indicator}]"
                        )
                    ),
                    "          geography_code:",
                    (
                        "            system: "
                        + _quote(
                            "e-Stat standard area code"
                            if geography_width == 5
                            else "local government code with check digit"
                        )
                    ),
                    f"            width: {geography_width}",
                    (
                        "            normalization: "
                        + _quote(f"zero-padded {geography_width}-digit string")
                    ),
                    (
                        "          time_basis: "
                        + _quote(
                            "census reference date"
                            if source_id == "estat_census"
                            else "Japanese fiscal year"
                        )
                    ),
                    f"          unit: {_quote(record['unit'])}",
                ]
            )
            if cross_check:
                lines.extend(
                    [
                        "        cross_checks:",
                        (
                            "          - source_id: "
                            + _quote("soumu_municipal_fiscal_card")
                        ),
                        f"            locator_id: {_quote(indicator)}",
                        f"            field: {_quote(indicator)}",
                        (
                            "            source_url: "
                            + _quote(cross_check["secondary_source_url"])
                        ),
                        (
                            "            comparison_rule: "
                            + _quote(cross_check["comparison_rule"])
                        ),
                        '            tolerance: "0.0001"',
                    ]
                )
            else:
                lines.append("        cross_checks: []")
            lines.extend(
                [
                    "        transformations: []",
                    "        verification:",
                    (
                        "          required_state: "
                        + _quote("reconciled" if cross_check else "verified")
                    ),
                    "          required_checks:",
                    '            - "nonempty definition and unit"',
                    '            - "official source route available"',
                    '            - "SQLite integrity_check = ok"',
                ]
            )
            if caveats:
                lines.append("        caveats:")
                lines.extend(f"          - {_quote(item)}" for item in caveats)
            else:
                lines.append("        caveats: []")
    lines.extend(
        [
            "defaults:",
            '  missing_value_policy: "preserve source marker; never coerce to zero"',
            '  stale_after: "until a newer official source is discovered"',
            "  external_use_requires:",
            '    - "value"',
            '    - "as_of"',
            '    - "definition"',
            '    - "source"',
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(output), "indicator_routes": len(INDICATOR_INFO)}
