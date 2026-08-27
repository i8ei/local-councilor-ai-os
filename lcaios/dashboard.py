"""Generate coverage dashboard (MOC) from local databases in read-only mode."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def _open_ro_db(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _get_tables(conn: sqlite3.Connection) -> set[str]:
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return {row[0] for row in c.fetchall()}


def inspect_database(db_path: Path) -> dict[str, Any] | None:
    """Inspect a single SQLite database and return structured coverage info."""
    conn = _open_ro_db(db_path)
    if not conn:
        return None

    try:
        tables = _get_tables(conn)

        # 1. Minutes DB
        if "meetings" in tables and "speeches" in tables:
            return _inspect_minutes_db(conn, db_path)

        # 2. Regulations DB
        if "regulation_documents" in tables:
            return _inspect_regulations_db(conn, db_path)

        # 3. Settlement DB
        if (
            "settlement_summary" in tables
            or "summary" in tables
            or "expenditure" in tables
            or "revenue" in tables
        ):
            return _inspect_settlement_db(conn, db_path, tables)

        # 4. Benchmark DB
        if "municipality" in tables and "indicator" in tables:
            return _inspect_benchmark_db(conn, db_path)
    finally:
        conn.close()

    return None


def _inspect_minutes_db(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    c = conn.cursor()
    # Meeting stats per council
    c.execute("""
        SELECT
            COALESCE(NULLIF(council_name, ''), '（自治体名未設定）') AS council_name,
            COUNT(*) AS meeting_count,
            MIN(NULLIF(date, '')) AS min_date,
            MAX(NULLIF(date, '')) AS max_date
        FROM meetings
        GROUP BY council_name
        ORDER BY meeting_count DESC;
    """)
    meeting_rows = c.fetchall()

    # Speech stats per council
    c.execute("""
        SELECT
            COALESCE(NULLIF(m.council_name, ''), '（自治体名未設定）') AS council_name,
            COUNT(s.speech_id) AS speech_count,
            SUM(CASE WHEN s.speaker IS NULL OR TRIM(s.speaker) = '' THEN 1 ELSE 0 END) AS no_speaker_count
        FROM speeches s
        JOIN meetings m ON s.meeting_id = m.meeting_id
        GROUP BY m.council_name;
    """)
    speech_rows = {row["council_name"]: row for row in c.fetchall()}

    councils = []
    total_meetings = 0
    total_speeches = 0

    for m_row in meeting_rows:
        name = m_row["council_name"]
        m_count = m_row["meeting_count"]
        s_data = speech_rows.get(name)
        s_count = s_data["speech_count"] if s_data else 0
        no_spk = s_data["no_speaker_count"] if s_data else 0
        no_spk_pct = round((no_spk / s_count * 100), 1) if s_count > 0 else 0.0

        total_meetings += m_count
        total_speeches += s_count

        councils.append({
            "council_name": name,
            "meetings": m_count,
            "speeches": s_count,
            "period_start": m_row["min_date"] or "不明",
            "period_end": m_row["max_date"] or "不明",
            "no_speaker_percent": no_spk_pct,
        })

    return {
        "db_type": "minutes",
        "db_path": str(db_path),
        "db_name": db_path.name,
        "total_councils": len(councils),
        "total_meetings": total_meetings,
        "total_speeches": total_speeches,
        "councils": councils,
    }


def _inspect_regulations_db(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    c = conn.cursor()
    c.execute("""
        SELECT
            COALESCE(NULLIF(source_name, ''), '未分類') AS source,
            COUNT(DISTINCT document_id) AS doc_count,
            MAX(fetched_at) AS last_fetch
        FROM regulation_documents
        GROUP BY source_name
        ORDER BY doc_count DESC;
    """)
    rows = c.fetchall()

    sources = []
    total_docs = 0
    for r in rows:
        count = r["doc_count"]
        total_docs += count
        sources.append({
            "source_name": r["source"],
            "documents": count,
            "last_fetched_at": r["last_fetch"] or "不明",
        })

    return {
        "db_type": "regulations",
        "db_path": str(db_path),
        "db_name": db_path.name,
        "total_sources": len(sources),
        "total_documents": total_docs,
        "sources": sources,
    }


def _inspect_settlement_db(
    conn: sqlite3.Connection,
    db_path: Path,
    tables: set[str],
) -> dict[str, Any]:
    """Aggregate settlement revenue/expenditure per fiscal year.

    Supports the three ledger shapes that appear in this repo:
    ``settlement_summary`` (side-column kan-ledger), a legacy ``summary``
    table with explicit revenue/expenditure/balance columns, and the
    operational ``expenditure``/``revenue`` pair.
    """

    records: list[dict[str, Any]] = []
    if "settlement_summary" in tables:
        records = _read_settlement_summary(conn)
    elif "summary" in tables:
        records = _read_summary_columns(conn, "summary")
    elif "revenue" in tables and "expenditure" in tables:
        records = _read_ledger_settlement(conn)

    return {
        "db_type": "settlement",
        "db_path": str(db_path),
        "db_name": db_path.name,
        "records": records,
    }


def _read_settlement_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Aggregate settlement_summary (kan ledger with a side column)."""

    c = conn.cursor()
    try:
        c.execute("""
            SELECT
                fiscal_year,
                COALESCE(SUM(CASE WHEN side = 'revenue' THEN collected_amount END), 0)
                    AS revenue_settled,
                COALESCE(SUM(CASE WHEN side = 'expenditure' THEN spent_amount END), 0)
                    AS expenditure_settled
            FROM settlement_summary
            GROUP BY fiscal_year
            ORDER BY fiscal_year ASC;
        """)
        records: list[dict[str, Any]] = []
        for row in c.fetchall():
            revenue = row["revenue_settled"] or 0
            expenditure = row["expenditure_settled"] or 0
            records.append({
                "fiscal_year": row["fiscal_year"],
                "revenue_settled": revenue,
                "expenditure_settled": expenditure,
                "balance": revenue - expenditure,
            })
        return records
    except sqlite3.Error:
        return []


def _read_summary_columns(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    """Read a legacy ``summary`` table with explicit column names."""

    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name});")
    cols = {r["name"] for r in c.fetchall()}
    if "fiscal_year" not in cols:
        return []
    rev_col = "revenue_settled" if "revenue_settled" in cols else "revenue"
    exp_col = "exp_settled" if "exp_settled" in cols else "expenditure"
    bal_col = "balance" if "balance" in cols else None
    if rev_col not in cols or exp_col not in cols:
        return []
    query = (
        f"SELECT fiscal_year, {rev_col} AS revenue_settled, "
        f"{exp_col} AS expenditure_settled"
    )
    if bal_col:
        query += f", {bal_col} AS balance"
    query += f" FROM {table_name} ORDER BY fiscal_year ASC;"
    try:
        records: list[dict[str, Any]] = []
        for row in c.execute(query):
            rec = {
                "fiscal_year": row["fiscal_year"],
                "revenue_settled": row["revenue_settled"],
                "expenditure_settled": row["expenditure_settled"],
            }
            if bal_col:
                rec["balance"] = row["balance"]
            records.append(rec)
        return records
    except sqlite3.Error:
        return []


def _read_ledger_settlement(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read the operational ``expenditure``/``revenue`` pair."""

    def _per_year(table: str, candidate_cols: tuple[str, ...]) -> dict[int, int]:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table});")
        cols = {r["name"] for r in c.fetchall()}
        if "fiscal_year" not in cols:
            return {}
        col = next((name for name in candidate_cols if name in cols), None)
        if col is None:
            return {}
        try:
            result: dict[int, int] = {}
            for row in c.execute(
                f"SELECT fiscal_year, SUM({col}) AS v FROM {table} GROUP BY fiscal_year"
            ):
                value = row["v"]
                result[row["fiscal_year"]] = int(value) if value is not None else 0
            return result
        except sqlite3.Error:
            return {}

    revenue = _per_year("revenue", ("amount_collected", "collected_amount"))
    expenditure = _per_year(
        "expenditure", ("amount_spent", "spent_amount")
    )
    records: list[dict[str, Any]] = []
    for year in sorted(set(revenue) | set(expenditure)):
        rev = revenue.get(year, 0)
        expd = expenditure.get(year, 0)
        records.append({
            "fiscal_year": year,
            "revenue_settled": rev,
            "expenditure_settled": expd,
            "balance": rev - expd,
        })
    return records


def _inspect_benchmark_db(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM municipality;")
    m_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM indicator;")
    i_count = c.fetchone()[0]

    return {
        "db_type": "benchmark",
        "db_path": str(db_path),
        "db_name": db_path.name,
        "municipalities_count": m_count,
        "indicators_count": i_count,
    }


def find_candidate_databases(search_dirs: Sequence[Path]) -> list[Path]:
    """Discover SQLite databases in target directories."""
    candidates = []
    seen = set()
    ignored_dir_names = {
        ".git",
        ".trash",
        ".cache",
        "cache",
        "archive",
        ".bak",
        "backups",
        "tmp",
        ".tmp",
    }
    for directory in search_dirs:
        if not directory.exists():
            continue
        if directory.is_file() and directory.suffix in (".db", ".sqlite", ".sqlite3"):
            resolved = directory.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(directory)
            continue
        for ext in ("*.db", "*.sqlite", "*.sqlite3"):
            for path in directory.rglob(ext):
                # Skip databases located inside ignored subdirectories
                try:
                    rel_parents = path.relative_to(directory).parent.parts
                except ValueError:
                    rel_parents = ()
                if any(p.lower() in ignored_dir_names for p in rel_parents):
                    continue

                # Skip hidden files, backups, temporary files
                name_lower = path.name.lower()
                if (
                    name_lower.startswith(".")
                    or any(
                        name_lower.endswith(s)
                        for s in (".bak", ".backup", ".tmp", ".swp", "~")
                    )
                    or any(
                        m in name_lower
                        for m in (".bak.", ".tmp.", "_backup.", ".backup.")
                    )
                ):
                    continue

                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(path)
    return sorted(candidates, key=lambda p: p.name)


def build_dashboard_report(
    db_paths: Sequence[Path],
    vault_path: Path | None = None,
) -> dict[str, Any]:
    """Collect all coverage data and return report dict."""
    now_iso = datetime.now(timezone.utc).isoformat()
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")

    entries: list[dict[str, Any]] = []
    for db_path in db_paths:
        try:
            info = inspect_database(db_path)
        except sqlite3.Error:
            # A corrupt or non-SQLite file must not abort the whole report.
            continue
        if info:
            entries.append(info)

    return {
        "schema_version": "lcaios-dashboard/1",
        "generated_at": now_iso,
        "generated_at_local": now_local,
        "vault_path": str(vault_path) if vault_path else None,
        "databases": entries,
    }


def render_dashboard_markdown(report: dict[str, Any]) -> str:
    """Render coverage dashboard report into GitHub Flavored Markdown."""
    now_str = report.get("generated_at_local", "")
    databases = report.get("databases", [])

    minutes_dbs = [d for d in databases if d["db_type"] == "minutes"]
    reg_dbs = [d for d in databases if d["db_type"] == "regulations"]
    settlement_dbs = [d for d in databases if d["db_type"] == "settlement"]
    benchmark_dbs = [d for d in databases if d["db_type"] == "benchmark"]

    total_councils = sum(d.get("total_councils", 0) for d in minutes_dbs)
    total_meetings = sum(d.get("total_meetings", 0) for d in minutes_dbs)
    total_speeches = sum(d.get("total_speeches", 0) for d in minutes_dbs)
    total_regs = sum(d.get("total_documents", 0) for d in reg_dbs)

    lines = [
        "---",
        "description: 自治体データ（議事録・例規・決算・統計）の収録状況とカバレッジ見取り図",
        "tags:",
        "  - 自治体データ",
        "  - MOC",
        "  - カバレッジ",
        "  - 地方議員AI運用OS",
        f"last_updated: {now_str}",
        "lifecycle: active",
        "---",
        "",
        "# 🏛️ 自治体公開データ 見取り図（MOC）",
        "",
        "> [!summary] データ基盤サマリ",
    ]

    if minutes_dbs:
        lines.append(
            f"> - **議事録DB**: {total_councils} 自治体 / {total_meetings:,} 会議 / {total_speeches:,} 発言"
        )
    if reg_dbs:
        lines.append(
            f"> - **例規集DB**: {sum(d.get('total_sources', 0) for d in reg_dbs)} 自治体 / {total_regs:,} 条例・規則"
        )
    if settlement_dbs:
        lines.append(
            f"> - **決算DB**: {len(settlement_dbs)} 件の決算データベース"
        )
    if benchmark_dbs:
        lines.append(
            f"> - **比較指標DB**: {sum(d.get('municipalities_count', 0) for d in benchmark_dbs)} 自治体 / {sum(d.get('indicators_count', 0) for d in benchmark_dbs)} 指標"
        )
    lines.extend([
        f"> - **最終更新**: `{now_str}`",
        "",
    ])

    # Section 1: Minutes
    if minutes_dbs:
        lines.extend([
            "## 1. 議事録 収録カバレッジ",
            "",
            "> [!note] 検索時の前提（AI・人間共通）",
            "> - **「0件ヒット＝議論されていない」ではありません**。各議会の収録期間外の議論は検索できません。",
            "> - 話者なし率が高い自治体は、PDF構造の都合で話者分離ができていないため本文検索のみ有効です。",
            "",
        ])
        for db in minutes_dbs:
            lines.extend([
                f"### 議事録: `{db['db_name']}`",
                "",
                "| 議会名 | 会議数 | 発言数 | 収録期間 | 話者なし率 |",
                "|---|---|---|---|---|",
            ])
            for c in db.get("councils", []):
                lines.append(
                    f"| **{c['council_name']}** | {c['meetings']} | {c['speeches']:,} | `{c['period_start']} 〜 {c['period_end']}` | {c['no_speaker_percent']}% |"
                )
            lines.append("")

    # Section 2: Regulations
    if reg_dbs:
        lines.extend([
            "## 2. 例規集（条例・規則） カバレッジ",
            "",
        ])
        for db in reg_dbs:
            lines.extend([
                f"### 例規: `{db['db_name']}`",
                "",
                "| 自治体 / 出典名 | 条例・規則数 | 最終取得日 |",
                "|---|---|---|",
            ])
            for s in db.get("sources", []):
                lines.append(
                    f"| **{s['source_name']}** | {s['documents']:,} 件 | `{s['last_fetched_at']}` |"
                )
            lines.append("")

    # Section 3: Settlement
    if settlement_dbs:
        lines.extend([
            "## 3. 決算 総括",
            "",
        ])
        for db in settlement_dbs:
            records = db.get("records", [])
            if records:
                lines.extend([
                    f"### 決算: `{db['db_name']}`",
                    "",
                    "| 年度 | 歳入決算額 | 歳出決算額 | 形式収支 (差引残額) |",
                    "|---|---|---|---|",
                ])
                for r in records:
                    rev = f"{r['revenue_settled']:,} 円" if r.get("revenue_settled") is not None else "-"
                    exp = f"{r['expenditure_settled']:,} 円" if r.get("expenditure_settled") is not None else "-"
                    bal = f"{r['balance']:,} 円" if r.get("balance") is not None else "-"
                    lines.append(f"| {r['fiscal_year']} | {rev} | {exp} | {bal} |")
                lines.append("")

    # Section 4: Benchmark
    if benchmark_dbs:
        lines.extend([
            "## 4. 自治体比較ベンチマーク",
            "",
        ])
        for db in benchmark_dbs:
            lines.append(
                f"- **{db['db_name']}**: {db['municipalities_count']} 自治体 / {db['indicators_count']} 指標収録"
            )
        lines.append("")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate local government data coverage dashboard (MOC)."
    )
    parser.add_argument(
        "--vault",
        type=Path,
        help="Path to Obsidian Vault or notes directory",
    )
    parser.add_argument(
        "--db",
        type=Path,
        nargs="+",
        help="Explicit database files to inspect",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        nargs="+",
        help="Directories to search for databases",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--write-vault",
        action="store_true",
        help="Write directly to 00_自治体データ見取り図.md in the vault",
    )

    args = parser.parse_args(argv)

    if args.write_vault and not args.vault:
        print(
            "ERROR: --write-vault には --vault の指定が必要です",
            file=sys.stderr,
        )
        return 2

    search_dirs: list[Path] = []
    if args.vault:
        search_dirs.append(args.vault)
        search_dirs.append(args.vault / ".local-councilor-ai-os" / "data")
    if args.data_dir:
        search_dirs.extend(args.data_dir)

    db_paths: list[Path] = []
    if args.db:
        db_paths.extend(args.db)
    if search_dirs:
        db_paths.extend(find_candidate_databases(search_dirs))

    if not db_paths:
        print(
            "ERROR: 検査対象のデータベースが見つかりませんでした。--db, --data-dir, または --vault を指定してください。",
            file=sys.stderr,
        )
        return 2

    # Deduplicate while preserving order
    unique_dbs = []
    seen = set()
    for p in db_paths:
        resolved = p.resolve()
        if resolved not in seen and resolved.is_file():
            seen.add(resolved)
            unique_dbs.append(p)

    report = build_dashboard_report(unique_dbs, vault_path=args.vault)

    if args.format == "json":
        output_content = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        output_content = render_dashboard_markdown(report)

    if args.write_vault and args.vault:
        target_file = args.vault / "00_自治体データ見取り図.md"
        target_file.write_text(output_content, encoding="utf-8")
        print(f"見取り図を更新しました: {target_file}")
    elif args.out:
        args.out.write_text(output_content, encoding="utf-8")
        print(f"見取り図を出力しました: {args.out}")
    else:
        print(output_content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
