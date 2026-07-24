#!/usr/bin/env python3
"""Run Tier 0 through Tier 1 and print one JSON run report."""

from __future__ import annotations

import argparse
import json
import os
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
from lcaios.run_manifest import (
    artifact_record,
    finish_run,
    redact_text,
    start_run,
)


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
    cache_dir: Path | None,
    offline: bool,
    refresh: bool,
    cross_check: bool,
) -> dict[str, Any]:
    """Run the complete bootstrap and return a secret-free report."""

    output_dir = (
        out_dir
        if out_dir is not None
        else BOOTSTRAP_DIR / "output" / _safe_directory_name(name)
    )
    client = HttpClient(
        cache_dir or DEFAULT_CACHE_DIR,
        offline=offline,
        refresh=refresh,
    )
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
    retrieval = client.retrieval_report()
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
        "retrieval": retrieval,
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
        "--cache-dir",
        type=Path,
        help="bootstrap共有cacheの代わりに使うcacheディレクトリ",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="検証済みcacheがあっても公式参照先を再取得する",
    )
    parser.add_argument(
        "--cross-check",
        action="store_true",
        help="決算カードXLSXで財政6指標を任意検算する",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        help="共通run manifestの保存先。指定しなければmanifestは作らない",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    if args.manifest_dir is not None:
        try:
            manifest_path, manifest = start_run(
                args.manifest_dir,
                run_type="bootstrap",
                repo_root=Path(__file__).resolve().parents[2],
                requested={
                    "municipality_name": args.municipality_name,
                    "prefecture": args.prefecture,
                    "offline": args.offline,
                    "refresh": args.refresh,
                    "cross_check": args.cross_check,
                    "cache_directory": (
                        str(args.cache_dir.expanduser().resolve(strict=False))
                        if args.cache_dir is not None
                        else str(DEFAULT_CACHE_DIR.resolve(strict=False))
                    ),
                    "output_directory": (
                        str(args.out_dir.expanduser().resolve(strict=False))
                        if args.out_dir is not None
                        else None
                    ),
                },
            )
        except OSError as error:
            print(
                json.dumps(
                    {"status": "failed", "error": str(error)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    try:
        report = run(
            args.municipality_name,
            args.prefecture,
            out_dir=args.out_dir,
            cache_dir=args.cache_dir,
            offline=args.offline,
            refresh=args.refresh,
            cross_check=args.cross_check,
        )
    except AmbiguousMunicipality as error:
        result = {
            "status": "ambiguous",
            "error": str(error),
            "candidates": error.candidates,
        }
        if manifest_path is not None and manifest is not None:
            manifest["failures"].append(
                {
                    "code": "ambiguous_municipality",
                    "message": redact_text(
                        str(error),
                        secret_values=(os.environ.get("ESTAT_APPID", ""),),
                    ),
                }
            )
            manifest["scope"] = {"candidates": error.candidates}
            finish_run(manifest_path, manifest, status="failed")
            result["manifest"] = str(manifest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
        safe_error = redact_text(
            str(error),
            secret_values=(os.environ.get("ESTAT_APPID", ""),),
        )
        result = {"status": "failed", "error": safe_error}
        if manifest_path is not None and manifest is not None:
            manifest["failures"].append(
                {
                    "code": type(error).__name__,
                    "message": safe_error,
                }
            )
            finish_run(manifest_path, manifest, status="failed")
            result["manifest"] = str(manifest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if manifest_path is not None and manifest is not None:
        try:
            database_path = Path(str(report["database"]["path"]))
            authority_path = Path(str(report["authority_map"]["path"]))
            manifest_updates = {
                "target": {
                    "output_directory": str(database_path.parent.resolve()),
                },
                "scope": {
                    **report["municipality"],
                    "mode": report["mode"],
                    "fiscal_year": report["fiscal"]["fiscal_year"],
                },
                "outputs": [
                    artifact_record(
                        database_path,
                        kind="municipality_database",
                        schema_version=1,
                    ),
                    artifact_record(
                        authority_path,
                        kind="authority_map",
                        schema_version=1,
                    ),
                ],
                "checks": [
                    {
                        "name": "sqlite_integrity",
                        "status": (
                            "passed"
                            if report["database"]["integrity_check"] == "ok"
                            else "failed"
                        ),
                        "detail": report["database"]["integrity_check"],
                    },
                    {
                        "name": "indicator_rows",
                        "status": "passed",
                        "detail": report["database"]["indicator_rows"],
                    },
                ],
                "warnings": [
                    {"code": "bootstrap_warning", "message": str(item)}
                    for item in report["warnings"]
                ],
                "retrieval": report.get("retrieval", {}),
            }
            finish_run(
                manifest_path,
                manifest,
                status="succeeded",
                updates=manifest_updates,
            )
            report["manifest"] = str(manifest_path)
        except (KeyError, OSError, ValueError) as error:
            safe_error = redact_text(
                str(error),
                secret_values=(os.environ.get("ESTAT_APPID", ""),),
            )
            manifest["failures"].append(
                {
                    "code": "manifest_finalize_failed",
                    "message": safe_error,
                }
            )
            finish_run(manifest_path, manifest, status="failed")
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error": safe_error,
                        "manifest": str(manifest_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
