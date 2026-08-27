"""Multi-year budget and settlement analytics engine (Read-Only).

Identifies persistent unused budget items, carryover chains, and revenue
collection bottlenecks across multiple fiscal years with zero LLM tokens.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from lcaios.database import sqlite_read_only_uri


class BridgeError(Exception):
    """Raised when database validation fails or analysis cannot proceed."""


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BridgeError(f"指定されたデータベースファイルが存在しません: {path}")
    uri = sqlite_read_only_uri(path.resolve())
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_tables(conn: sqlite3.Connection) -> set[str]:
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return {row[0] for row in c.fetchall()}


def _detect_expenditure_schema(tables: set[str], conn: sqlite3.Connection) -> dict[str, str] | None:
    if "expenditure" in tables:
        c = conn.cursor()
        c.execute("PRAGMA table_info(expenditure);")
        cols = {row[1] for row in c.fetchall()}
        if {"fiscal_year", "amount_budget_final", "amount_spent", "amount_unused"}.issubset(cols):
            return {
                "table": "expenditure",
                "year": "fiscal_year",
                "kuan": "kuan",
                "kou": "kou",
                "moku": "moku",
                "budget": "amount_budget_final",
                "spent": "amount_spent",
                "carryover": "amount_carried_forward",
                "unused": "amount_unused",
                "kuan_no": "kuan_no",
                "kou_no": "kou_no",
                "moku_no": "moku_no",
            }
    if "settlement_expenditure" in tables:
        c = conn.cursor()
        c.execute("PRAGMA table_info(settlement_expenditure);")
        cols = {row[1] for row in c.fetchall()}
        required = {
            "fiscal_year",
            "kan_code",
            "kan_name",
            "ko_code",
            "ko_name",
            "moku_code",
            "moku_name",
            "item_budget_current_amount",
            "item_spent_amount",
            "item_carryover_amount",
            "item_unused_amount",
        }
        if required.issubset(cols):
            return {
                "table": "settlement_expenditure",
                "year": "fiscal_year",
                "kuan": "kan_name",
                "kou": "ko_name",
                "moku": "moku_name",
                "budget": "item_budget_current_amount",
                "spent": "item_spent_amount",
                "carryover": "item_carryover_amount",
                "unused": "item_unused_amount",
                "kuan_no": "kan_code",
                "kou_no": "ko_code",
                "moku_no": "moku_code",
            }
    return None


def _detect_revenue_schema(tables: set[str], conn: sqlite3.Connection) -> dict[str, str] | None:
    if "revenue" in tables:
        c = conn.cursor()
        c.execute("PRAGMA table_info(revenue);")
        cols = {row[1] for row in c.fetchall()}
        if {"fiscal_year", "amount_settled", "amount_collected"}.issubset(cols):
            return {
                "table": "revenue",
                "year": "fiscal_year",
                "kuan": "kuan",
                "kou": "kou",
                "kuan_no": "kuan_no",
                "kou_no": "kou_no",
                "budget": "amount_budget",
                "settled": "amount_settled",
                "collected": "amount_collected",
                "uncollectible": "amount_uncollectible",
                "outstanding": "amount_outstanding",
            }
    if "settlement_revenue" in tables:
        c = conn.cursor()
        c.execute("PRAGMA table_info(settlement_revenue);")
        cols = {row[1] for row in c.fetchall()}
        required = {
            "fiscal_year",
            "kan_code",
            "kan_name",
            "ko_code",
            "ko_name",
            "budget_current_amount",
            "collected_amount",
            "uncollectible_amount",
            "outstanding_amount",
        }
        if required.issubset(cols):
            return {
                "table": "settlement_revenue",
                "year": "fiscal_year",
                "kuan": "kan_name",
                "kou": "ko_name",
                "kuan_no": "kan_code",
                "kou_no": "ko_code",
                "budget": "budget_current_amount",
                "settled": "budget_current_amount",
                "collected": "collected_amount",
                "uncollectible": "uncollectible_amount",
                "outstanding": "outstanding_amount",
            }
    return None


def validate_database(conn: sqlite3.Connection) -> dict[str, Any]:
    """Validate database readiness and integrity for multi-year analysis."""
    tables = _get_tables(conn)
    exp_schema = _detect_expenditure_schema(tables, conn)
    if not exp_schema:
        raise BridgeError("有効な歳出決算テーブル（expenditure または settlement_expenditure）が見つかりません。")

    c = conn.cursor()
    c.execute(f"SELECT COUNT(*), COUNT(DISTINCT {exp_schema['year']}) FROM {exp_schema['table']};")
    exp_count, year_count = c.fetchone()
    if exp_count == 0:
        raise BridgeError("歳出決算テーブルにデータが存在しません。")

    rev_schema = _detect_revenue_schema(tables, conn)
    return {
        "exp_schema": exp_schema,
        "rev_schema": rev_schema,
        "total_records": exp_count,
        "fiscal_years_count": year_count,
    }


def analyze_persistent_unused(
    conn: sqlite3.Connection,
    schema: dict[str, str],
    *,
    min_years: int = 2,
    min_unused_amount: int = 1_000_000,
    min_unused_rate: float = 0.15,
) -> list[dict[str, Any]]:
    """Identify budget items with persistent unused amounts across multiple fiscal years."""
    table = schema["table"]
    y_col = schema["year"]
    b_col = schema["budget"]
    s_col = schema["spent"]
    u_col = schema["unused"]
    k_col = schema["kuan"]
    ko_col = schema["kou"]
    m_col = schema["moku"]
    kn_col = schema["kuan_no"]
    kon_col = schema["kou_no"]
    mn_col = schema["moku_no"]

    c = conn.cursor()
    query = f"""
        SELECT
            {y_col},
            COALESCE(NULLIF({kn_col}, ''), '0') AS kn,
            COALESCE(NULLIF({k_col}, ''), '未分類') AS kuan,
            COALESCE(NULLIF({kon_col}, ''), '0') AS kon,
            COALESCE(NULLIF({ko_col}, ''), '未分類') AS kou,
            COALESCE(NULLIF({mn_col}, ''), '0') AS mn,
            COALESCE(NULLIF({m_col}, ''), '未分類') AS moku,
            {b_col},
            {s_col},
            {u_col}
        FROM (
            SELECT
                {y_col}, {kn_col}, {k_col}, {kon_col}, {ko_col}, {mn_col}, {m_col},
                {b_col}, {s_col}, {u_col},
                ROW_NUMBER() OVER (
                    PARTITION BY {y_col}, {kn_col}, {kon_col}, {mn_col}
                    ORDER BY id
                ) as rn
            FROM {table}
            WHERE {b_col} > 0
        )
        WHERE rn = 1
        ORDER BY kn, kon, mn, {y_col};
    """
    c.execute(query)
    rows = c.fetchall()

    moku_history: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = f"{r['kn']}.{r['kuan']} > {r['kon']}.{r['kou']} > {r['mn']}.{r['moku']}"
        budget = r[b_col] or 0
        spent = r[s_col] or 0
        unused = r[u_col] or 0
        rate = round((unused / budget * 100), 1) if budget > 0 else 0.0

        if key not in moku_history:
            moku_history[key] = []
        moku_history[key].append({
            "fiscal_year": r[y_col],
            "budget": budget,
            "spent": spent,
            "unused": unused,
            "unused_rate": rate,
        })

    results = []
    for key, history in moku_history.items():
        flagged_years = [
            h for h in history
            if h["unused"] >= min_unused_amount and (h["unused_rate"] / 100.0) >= min_unused_rate
        ]
        if len(flagged_years) >= min_years:
            avg_unused = sum(h["unused"] for h in flagged_years) // len(flagged_years)
            avg_rate = round(sum(h["unused_rate"] for h in flagged_years) / len(flagged_years), 1)
            results.append({
                "moku_name": key,
                "flagged_years_count": len(flagged_years),
                "total_years_recorded": len(history),
                "avg_unused_amount": avg_unused,
                "avg_unused_rate": avg_rate,
                "yearly_breakdown": history,
            })

    results.sort(key=lambda x: x["avg_unused_amount"], reverse=True)
    return results


def analyze_carried_forward_chains(
    conn: sqlite3.Connection,
    schema: dict[str, str],
    *,
    min_years: int = 2,
) -> list[dict[str, Any]]:
    """Identify budget items with consecutive multi-year carryovers."""
    table = schema["table"]
    y_col = schema["year"]
    b_col = schema["budget"]
    c_col = schema["carryover"]
    k_col = schema["kuan"]
    ko_col = schema["kou"]
    m_col = schema["moku"]
    kn_col = schema["kuan_no"]
    kon_col = schema["kou_no"]
    mn_col = schema["moku_no"]

    c = conn.cursor()
    query = f"""
        SELECT
            {y_col},
            COALESCE(NULLIF({kn_col}, ''), '0') AS kn,
            COALESCE(NULLIF({k_col}, ''), '未分類') AS kuan,
            COALESCE(NULLIF({kon_col}, ''), '0') AS kon,
            COALESCE(NULLIF({ko_col}, ''), '未分類') AS kou,
            COALESCE(NULLIF({mn_col}, ''), '0') AS mn,
            COALESCE(NULLIF({m_col}, ''), '未分類') AS moku,
            {c_col},
            {b_col}
        FROM (
            SELECT
                {y_col}, {kn_col}, {k_col}, {kon_col}, {ko_col}, {mn_col}, {m_col},
                {c_col}, {b_col},
                ROW_NUMBER() OVER (
                    PARTITION BY {y_col}, {kn_col}, {kon_col}, {mn_col}
                    ORDER BY id
                ) as rn
            FROM {table}
            WHERE {c_col} IS NOT NULL AND {c_col} > 0
        )
        WHERE rn = 1
        ORDER BY kn, kon, mn, {y_col};
    """
    c.execute(query)
    rows = c.fetchall()

    chains: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = f"{r['kn']}.{r['kuan']} > {r['kon']}.{r['kou']} > {r['mn']}.{r['moku']}"
        if key not in chains:
            chains[key] = []
        chains[key].append({
            "fiscal_year": r[y_col],
            "carried_forward": r[c_col] or 0,
            "budget": r[b_col] or 0,
        })

    results = []
    for key, history in chains.items():
        if len(history) >= min_years:
            results.append({
                "moku_name": key,
                "carried_years_count": len(history),
                "yearly_breakdown": history,
            })
    def _sort_key(item: dict[str, Any]) -> int:
        val = item.get("carried_years_count")
        return val if isinstance(val, int) else 0

    results.sort(key=_sort_key, reverse=True)
    return results


def analyze_revenue_uncollected(
    conn: sqlite3.Connection,
    schema: dict[str, str] | None,
    *,
    min_outstanding_amount: int = 1_000_000,
) -> list[dict[str, Any]]:
    """Identify revenue categories with significant uncollected or uncollectible amounts."""
    if not schema:
        return []

    table = schema["table"]
    y_col = schema["year"]
    k_col = schema["kuan"]
    ko_col = schema["kou"]
    kn_col = schema["kuan_no"]
    kon_col = schema["kou_no"]
    s_col = schema["settled"]
    c_col = schema["collected"]
    u_col = schema["uncollectible"]
    o_col = schema["outstanding"]

    c = conn.cursor()
    query = f"""
        SELECT
            {y_col},
            COALESCE(NULLIF({kn_col}, ''), '0') AS kn,
            COALESCE(NULLIF({k_col}, ''), '未分類') AS kuan,
            COALESCE(NULLIF({kon_col}, ''), '') AS kon,
            COALESCE(NULLIF({ko_col}, ''), '') AS kou,
            {s_col},
            {c_col},
            COALESCE({u_col}, 0) AS uncollectible,
            COALESCE({o_col}, 0) AS outstanding
        FROM {table}
        WHERE COALESCE({o_col}, 0) >= ? OR COALESCE({u_col}, 0) > 0
        ORDER BY {y_col} DESC, outstanding DESC;
    """
    c.execute(query, (min_outstanding_amount,))
    rows = c.fetchall()

    results = []
    for r in rows:
        kou_str = f"{r['kon']}.{r['kou']}" if r["kon"] else "（総括）"
        key = f"{r['kn']}.{r['kuan']} > {kou_str}"
        settled = r[s_col] or 0
        collected = r[c_col] or 0
        uncollectible = r["uncollectible"] or 0
        outstanding = r["outstanding"] or 0
        collect_rate = round((collected / settled * 100), 1) if settled > 0 else 0.0

        results.append({
            "fiscal_year": r[y_col],
            "revenue_name": key,
            "amount_settled": settled,
            "amount_collected": collected,
            "amount_uncollectible": uncollectible,
            "amount_outstanding": outstanding,
            "collection_rate": collect_rate,
        })
    return results


def run_bridge_analysis(
    db_path: Path,
    *,
    min_unused_years: int = 2,
    min_unused_amount: int = 1_000_000,
    min_unused_rate: float = 0.15,
    min_carryover_years: int = 2,
    min_outstanding_amount: int = 1_000_000,
) -> dict[str, Any]:
    """Execute complete multi-year budget & settlement bridge analysis."""
    conn = _open_read_only(db_path)
    try:
        val = validate_database(conn)
        exp_schema = val["exp_schema"]
        rev_schema = val["rev_schema"]

        persistent_unused = analyze_persistent_unused(
            conn,
            exp_schema,
            min_years=min_unused_years,
            min_unused_amount=min_unused_amount,
            min_unused_rate=min_unused_rate,
        )

        carried_chains = analyze_carried_forward_chains(
            conn,
            exp_schema,
            min_years=min_carryover_years,
        )

        revenue_issues = analyze_revenue_uncollected(
            conn,
            rev_schema,
            min_outstanding_amount=min_outstanding_amount,
        )

        return {
            "schema_version": "lcaios-bridge/1",
            "database": str(db_path),
            "fiscal_years_count": val["fiscal_years_count"],
            "total_expenditure_records": val["total_records"],
            "parameters": {
                "min_unused_years": min_unused_years,
                "min_unused_amount": min_unused_amount,
                "min_unused_rate": min_unused_rate,
                "min_carryover_years": min_carryover_years,
                "min_outstanding_amount": min_outstanding_amount,
            },
            "persistent_unused": persistent_unused,
            "carried_forward_chains": carried_chains,
            "revenue_uncollected": revenue_issues,
        }
    finally:
        conn.close()


def render_bridge_markdown(report: dict[str, Any]) -> str:
    """Render bridge analysis report as GitHub Flavored Markdown."""
    lines = [
        "---",
        "description: 予算決算多年度ブリッジ分析（不用額常態化・繰越・未収金）",
        "tags:",
        "  - 決算審査",
        "  - 予算審査",
        "  - 地方議員AI運用OS",
        "---",
        "",
        "# 📊 予算決算 多年度ブリッジ分析レポート",
        "",
        "> [!summary] 分析概要",
        f"> - **対象DB**: `{Path(report['database']).name}`",
        f"> - **収録年度数**: {report['fiscal_years_count']} 年度",
        f"> - **不用額常態化の検出基準**: 複数年度で不用額 {report['parameters']['min_unused_amount']:,} 円以上 かつ 不用率 {int(report['parameters']['min_unused_rate']*100)}% 以上",
        "",
        f"## 1. 不用額が常態化している事業（{len(report['persistent_unused'])} 件）",
        "",
    ]

    if not report["persistent_unused"]:
        lines.append("基準を満たす不用額常態化事業は検出されませんでした。\n")
    else:
        for idx, item in enumerate(report["persistent_unused"], 1):
            lines.append(f"### {idx}. {item['moku_name']}")
            lines.append(f"- **平均不用額**: `{item['avg_unused_amount']:,} 円`（平均不用率: `{item['avg_unused_rate']}%` / {item['flagged_years_count']}年該当）")
            lines.append("")
            lines.append("| 年度 | 予算現額 | 支出済額 | 不用額 | 不用率 |")
            lines.append("|---|---|---|---|---|")
            for y in item["yearly_breakdown"]:
                lines.append(f"| {y['fiscal_year']} | {y['budget']:,} 円 | {y['spent']:,} 円 | **{y['unused']:,} 円** | `{y['unused_rate']}%` |")
            lines.append("")

    lines.append(f"## 2. 複数年連続で繰越（事業遅延・継続）が発生している事業（{len(report['carried_forward_chains'])} 件）\n")
    if not report["carried_forward_chains"]:
        lines.append("基準を満たす連続繰越事業は検出されませんでした。\n")
    else:
        for idx, item in enumerate(report["carried_forward_chains"], 1):
            lines.append(f"### {idx}. {item['moku_name']}（{item['carried_years_count']}年連続）")
            lines.append("")
            lines.append("| 年度 | 繰越額 | 予算現額 |")
            lines.append("|---|---|---|")
            for y in item["yearly_breakdown"]:
                lines.append(f"| {y['fiscal_year']} | **{y['carried_forward']:,} 円** | {y['budget']:,} 円 |")
            lines.append("")

    lines.append(f"## 3. 歳入の収入未済・不納欠損が多額な科目（{len(report['revenue_uncollected'])} 件）\n")
    if not report["revenue_uncollected"]:
        lines.append("基準を満たす多額未収科目は検出されませんでした。\n")
    else:
        lines.append("| 年度 | 科目 | 調定額 | 収入済額 | 徴収率 | 収入未済額 | 不納欠損額 |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in report["revenue_uncollected"]:
            lines.append(
                f"| {item['fiscal_year']} | **{item['revenue_name']}** | {item['amount_settled']:,} 円 | "
                f"{item['amount_collected']:,} 円 | `{item['collection_rate']}%` | "
                f"**{item['amount_outstanding']:,} 円** | {item['amount_uncollectible']:,} 円 |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="多年度の決算データから不用額常態化・繰越・未収金を高速分析する計算機"
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="検査対象の決算SQLiteデータベースパス",
    )
    parser.add_argument(
        "--min-years",
        type=int,
        default=2,
        help="不用額常態化とみなす最小年数（デフォルト: 2年）",
    )
    parser.add_argument(
        "--min-unused-amount",
        type=int,
        default=1_000_000,
        help="不用額常態化と判定する最小不用額（デフォルト: 100万円）",
    )
    parser.add_argument(
        "--min-unused-rate",
        type=float,
        default=0.15,
        help="不用額常態化と判定する最小不用率（デフォルト: 0.15 = 15%%）",
    )
    parser.add_argument(
        "--min-outstanding-amount",
        type=int,
        default=1_000_000,
        help="歳入で未収金を抽出する最小金額（デフォルト: 100万円）",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="出力形式（markdown または json）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="結果を出力するファイルパス（省略時は標準出力）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        report = run_bridge_analysis(
            args.db,
            min_unused_years=args.min_years,
            min_unused_amount=args.min_unused_amount,
            min_unused_rate=args.min_unused_rate,
            min_outstanding_amount=args.min_outstanding_amount,
        )

        if args.format == "json":
            output_content = json.dumps(report, ensure_ascii=False, indent=2)
        else:
            output_content = render_bridge_markdown(report)

        if args.out:
            args.out.write_text(output_content, encoding="utf-8")
            print(f"分析レポートを出力しました: {args.out}")
        else:
            print(output_content)
        return 0
    except (BridgeError, sqlite3.Error) as error:
        prefix = "ERROR" if isinstance(error, BridgeError) else "ERROR: データベースエラー"
        print(f"{prefix}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
