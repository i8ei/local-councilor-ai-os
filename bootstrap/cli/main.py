#!/usr/bin/env python3
"""Run Tier 0 through Tier 1 and print one JSON run report."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    script_directory = Path(__file__).resolve().parent
    sys.path = [
        entry
        for entry in sys.path
        if Path(entry or ".").resolve() != script_directory
    ]
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bootstrap.cli.authority_map import generate_authority_map
from bootstrap.cli.census import CensusError, fetch_census
from bootstrap.cli.db import DatabaseError, build_database
from bootstrap.cli.fiscal import FiscalError, fetch_fiscal
from bootstrap.cli.http import FetchError, HttpClient
from bootstrap.cli.resolve import (
    AmbiguousMunicipality,
    ResolveError,
    normalize_name,
    resolve_municipality,
)
from bootstrap.cli.xlsx import XlsxError


BOOTSTRAP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = BOOTSTRAP_DIR / ".cache"


def _safe_directory_name(name: str) -> str:
    normalized = normalize_name(name)
    safe = re.sub(r"[/\\\x00-\x1f]+", "_", normalized)
    return safe or "municipality"


def run(
    name: str,
    prefecture: str | None,
    *,
    out_dir: Path | None,
    offline: bool,
    cross_check: bool,
) -> dict[str, Any]:
    """Run the complete bootstrap and return a secret-free report."""

    output_dir = (
        out_dir
        if out_dir is not None
        else BOOTSTRAP_DIR / "output" / _safe_directory_name(name)
    )
    client = HttpClient(DEFAULT_CACHE_DIR, offline=offline)
    municipality = resolve_municipality(name, prefecture, client)
    census = fetch_census(municipality, client)
    fiscal = fetch_fiscal(municipality, client, cross_check=cross_check)
    records = list(census["records"]) + list(fiscal["records"])
    database_path = output_dir / "municipality.db"
    authority_path = output_dir / "authority_map.yaml"
    metadata = {
        "census_selection": census["selection"],
        "census_selection_policy": census["selection_policy"],
        "fiscal_discovery": fiscal["discovery"],
        "fiscal_cross_check": fiscal["cross_check"],
    }
    database = build_database(
        municipality, records, metadata, database_path
    )
    authority = generate_authority_map(
        municipality,
        records,
        authority_path,
        database_name=database_path.name,
        census_warning=census["selection"].get("warning"),
    )
    warnings = [
        item
        for item in [
            census["selection"].get("warning"),
            *fiscal.get("warnings", []),
        ]
        if item
    ]
    return {
        "status": "ok",
        "mode": "offline" if offline else "online",
        "municipality": {
            "name": municipality["name"],
            "prefecture": municipality["prefecture"],
            "area_code_5": municipality["area_code_5"],
            "local_government_code_6": municipality[
                "local_government_code_6"
            ],
        },
        "census": {
            "indicator_count": len(census["records"]),
            "used_fallback": census["selection"]["used_fallback"],
            "selection_reason": census["selection"]["reason"],
        },
        "fiscal": {
            "indicator_count": len(fiscal["records"]),
            "fiscal_year": fiscal["fiscal_year"],
            "primary_status": fiscal["status"],
            "cross_check_status": fiscal["cross_check"]["status"],
        },
        "database": database,
        "authority_map": authority,
        "live_request_count": client.request_count,
        "warnings": warnings,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "自治体名から公式全国データを取得しSQLiteとauthority_map.yamlを生成"
        )
    )
    parser.add_argument("municipality_name", help="自治体名（例: 太良町）")
    parser.add_argument("--prefecture", help="同名自治体を区別する都道府県名")
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="出力先（既定: bootstrap/output/<自治体名>）",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ネットワークを使わず検証済みローカルキャッシュだけを使う",
    )
    parser.add_argument(
        "--cross-check",
        action="store_true",
        help="決算カードXLSXで財政6指標を任意検算する",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run(
            args.municipality_name,
            args.prefecture,
            out_dir=args.out_dir,
            offline=args.offline,
            cross_check=args.cross_check,
        )
    except AmbiguousMunicipality as error:
        print(
            json.dumps(
                {
                    "status": "ambiguous",
                    "error": str(error),
                    "candidates": error.candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except (
        CensusError,
        DatabaseError,
        FetchError,
        FiscalError,
        ResolveError,
        XlsxError,
        KeyError,
        OSError,
        ValueError,
        sqlite3.Error,
    ) as error:
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
