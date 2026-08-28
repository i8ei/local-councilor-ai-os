"""Multi-year Soumu fiscal data discovery and ingestion for local councilor settlement review."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from bootstrap.cli.fiscal import (
    FISCAL_INDEX,
    FiscalError,
    _fetch_html,
    _fiscal_year,
    _links,
    discover_overview_xlsx,
)
from bootstrap.cli.http import BOOTSTRAP_USER_AGENT, HttpClient
from modules.settlement_review.ingest_csv import main as ingest_csv_main
from modules.settlement_review.vendor_card import extract_settlement_csvs
from modules.settlement_review.verify_totals import verify


def discover_fiscal_years(
    client: HttpClient,
    *,
    max_years: int = 5,
) -> dict[int, str]:
    """Discover available fiscal years and their overview page URLs from Soumu portal."""
    result = _fetch_html(client, FISCAL_INDEX, "fiscal-index")
    candidates: dict[int, str] = {}
    for link in _links(FISCAL_INDEX, result):
        combined = link.text + " " + link.context
        year = _fiscal_year(combined)
        path = urllib.parse.urlsplit(link.url).path.lower()
        semantic_match = "市町村" in combined or "shichouson" in path
        if (
            year is not None
            and semantic_match
            and path.endswith((".html", ".htm"))
            and link.url != FISCAL_INDEX
        ):
            if year not in candidates:
                candidates[year] = link.url

    # Sort descending and limit to requested count
    sorted_years = sorted(candidates.keys(), reverse=True)[:max_years]
    return {yr: candidates[yr] for yr in sorted_years}


def fetch_and_ingest_multi_year(
    municipality_name: str,
    years: list[int],
    *,
    db_path: Path,
    out_dir: Path,
    cache_dir: Path = Path("bootstrap/.cache"),
    region_level: str = "auto",
    account_name: str = "一般会計",
) -> dict[str, Any]:
    """Fetch multi-year XLSX from Soumu, extract CSVs, ingest into settlement.db, and verify."""
    if region_level == "auto":
        region_level = "9" if (municipality_name.endswith("市") or municipality_name.endswith("区")) else "12"

    client = HttpClient(cache_dir=cache_dir, user_agent=BOOTSTRAP_USER_AGENT)
    available_pages = discover_fiscal_years(client, max_years=10)

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for year in sorted(years):
        if year not in available_pages:
            raise FiscalError(f"総務省ポータルに対象年度 {year} のページが見つかりません (利用可能: {list(available_pages.keys())})")

        page_url = available_pages[year]
        xlsx_result, meta = discover_overview_xlsx(client, page_url, year, region_level)

        year_csv_dir = out_dir / str(year)
        paths = extract_settlement_csvs(
            xlsx_result.cache_path,
            municipality_name,
            year,
            year_csv_dir,
            account_name=account_name,
        )

        # Ingest CSVs into DB
        ingest_csv_main(["summary", str(paths["summary"]), "--db", str(db_path)])
        ingest_csv_main(["revenue", str(paths["revenue"]), "--db", str(db_path)])
        ingest_csv_main(["expenditure", str(paths["expenditure"]), "--db", str(db_path)])

        results.append({
            "fiscal_year": year,
            "page_url": page_url,
            "xlsx_url": meta.get("url"),
            "csv_paths": paths,
        })

    # Verify entire multi-year database
    verify_exit_code = verify(db_path)
    passed = (verify_exit_code == 0)

    return {
        "municipality": municipality_name,
        "years": years,
        "database": db_path,
        "verified": passed,
        "runs": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and ingest multi-year fiscal data from Soumu into settlement.db."
    )
    parser.add_argument("--municipality", required=True, help="Municipality name (e.g. 太良町)")
    parser.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024], help="Fiscal years to ingest")
    parser.add_argument("--db", type=Path, default=Path("settlement.db"), help="Target SQLite database path")
    parser.add_argument("--out-dir", type=Path, default=Path("settlement_csvs"), help="CSV output directory")
    parser.add_argument("--cache-dir", type=Path, default=Path("bootstrap/.cache"), help="Cache directory")

    args = parser.parse_args(argv)
    try:
        res = fetch_and_ingest_multi_year(
            args.municipality,
            args.years,
            db_path=args.db,
            out_dir=args.out_dir,
            cache_dir=args.cache_dir,
        )
        print(f"複数年度決算データの取込が完了しました ({len(res['years'])}年度分: {res['years']})")
        print(f"  データベース: {res['database']}")
        print(f"  自動検算: {'合格 (差額0)' if res['verified'] else '不合格'}")
        return 0 if res["verified"] else 1
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
