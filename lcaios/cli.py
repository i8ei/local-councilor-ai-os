"""Unified read-only command-line interface."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from .database import verify_bootstrap_database
from .doctor import run_doctor
from .lifecycle import (
    backup_database,
    generated_artifacts,
    restore_database,
)
from .output_check import check_output_file
from .smoke_test import SmokeTestError, run_bootstrap_smoke_test
from .status import REQUIREMENTS, build_status, requirements_met


def _requirements(value: str) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
    )
    unknown = sorted(set(values) - REQUIREMENTS)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"未対応のrequirement: {', '.join(unknown)}"
        )
    return values


def _escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace(
        "\n", " "
    )


def render_markdown(report: dict[str, Any]) -> str:
    """Render the status report with deterministic section and row ordering."""

    lines = [
        "# local-councilor-ai-os status",
        "",
        f"- 状態: `{report['status']}`",
        f"- Vault: `{report['vault']['path']}`",
        f"- 生成日時: `{report['generated_at']}`",
        "",
        "## 利用可能状態",
        "",
        "| ゲート | 状態 | 詳細 |",
        "|---|---|---|",
    ]
    gate_details = {
        "foundation_ready": report["foundation"].get("detail", ""),
        "scaffold_ready": report["scaffold"].get("detail", ""),
        "profile_ready": report["profile"].get("detail", ""),
        "tier1_data_ready": report["bootstrap"].get("detail", ""),
    }
    for name, module in report.get("modules", {}).items():
        gate_details[f"module_ready:{name}"] = module.get("detail", "")
    for name in sorted(report["requirements"]):
        state = report["requirements"][name]
        lines.append(
            f"| `{name}` | `{state}` | {_escape(gate_details.get(name, ''))} |"
        )

    bootstrap = report["bootstrap"]
    lines.extend(
        [
            "",
            "## Tier 1データ",
            "",
            f"- 状態: `{bootstrap['state']}`",
            f"- 鮮度: `{bootstrap['freshness']['state']}`",
            f"- Database: `{bootstrap.get('database') or '未設定'}`",
        ]
    )
    if bootstrap.get("municipality"):
        municipality = bootstrap["municipality"]
        lines.extend(
            [
                f"- 自治体: `{municipality.get('prefecture', '')}"
                f"{municipality.get('name', '')}`",
                f"- 標準地域コード: `{municipality.get('area_code_5', '')}`",
                f"- 指標行数: `{bootstrap.get('indicator_rows', 0)}`",
                f"- 対象期: `{', '.join(bootstrap.get('source_periods', []))}`",
            ]
        )
    if bootstrap["freshness"].get("sources"):
        lines.extend(
            [
                "",
                "| 参照先 | 鮮度 | 対象期 | 最終確認 | 次回確認期限 |",
                "|---|---|---|---|---|",
            ]
        )
        for source in sorted(
            bootstrap["freshness"]["sources"],
            key=lambda item: item["source_id"],
        ):
            lines.append(
                f"| `{source['source_id']}` | `{source['state']}` | "
                f"{_escape(', '.join(source['source_periods']))} | "
                f"`{source.get('checked_at') or ''}` | "
                f"`{source.get('check_due_at') or ''}` |"
            )

    lines.extend(
        [
            "",
            "## Modules",
            "",
            "| Module | 状態 | Database | Coverage |",
            "|---|---|---|---|",
        ]
    )
    for name in sorted(report.get("modules", {})):
        module = report["modules"][name]
        lines.append(
            f"| `{name}` | `{module['state']}` | "
            f"`{module.get('database') or ''}` | "
            f"{_escape(json.dumps(module.get('coverage', {}), ensure_ascii=False, sort_keys=True))} |"
        )

    lines.extend(["", "## 警告", ""])
    if report["warnings"]:
        for item in report["warnings"]:
            path = f" (`{item['path']}`)" if item.get("path") else ""
            lines.append(
                f"- **{item['severity']}** `{item['code']}`: "
                f"{item['message']}{path}"
            )
    else:
        lines.append("- なし")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m lcaios",
        description="地方議員AI運用OSの統一された読み取り専用状態確認",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser(
        "doctor",
        help="診断・readiness・次の一手を1コマンドで読み取り専用に確認",
    )
    doctor.add_argument("--vault", required=True, type=Path)
    doctor.add_argument("--bootstrap-db", type=Path)
    doctor.add_argument("--agent", default="auto")
    doctor.add_argument(
        "--probe-obsidian",
        action="store_true",
        help="Obsidian CLIの対象Vault疎通も確認する",
    )
    doctor.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    status = subparsers.add_parser(
        "status",
        help="Vault、Tier 1、各module manifestの状態を再計算",
    )
    status.add_argument("--vault", required=True, type=Path)
    status.add_argument(
        "--bootstrap-db",
        type=Path,
        help="Tier 1 municipality.db。未指定時はinstance.jsonから解決",
    )
    status.add_argument(
        "--require",
        type=_requirements,
        action="append",
        default=[],
        help=(
            "foundation_ready,scaffold_ready,profile_ready,"
            "tier1_data_ready,module_ready:<name>を指定。未達なら終了コード2"
        ),
    )
    status.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    freshness = subparsers.add_parser(
        "freshness",
        help="Tier 1 DBの参照先別鮮度だけを読み取り専用で表示",
    )
    freshness.add_argument("--vault", required=True, type=Path)
    freshness.add_argument("--bootstrap-db", type=Path)
    freshness.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    verify = subparsers.add_parser(
        "verify",
        help="データベースや公開予定稿の読み取り専用検査",
    )
    verify_subparsers = verify.add_subparsers(
        dest="verify_command",
        required=True,
    )
    output = verify_subparsers.add_parser(
        "output",
        help="公開予定稿から内部リンク・path・秘密値候補等を検出",
    )
    output.add_argument("--file", required=True, type=Path)
    output.add_argument(
        "--fail-on",
        choices=("error", "warning", "never"),
        default="error",
        help="終了コード2にする最低severity",
    )
    output.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    database = verify_subparsers.add_parser(
        "database",
        help="Tier 1 SQLiteのintegrityとschema互換を検証",
    )
    database.add_argument("--file", required=True, type=Path)
    database.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    backup = subparsers.add_parser(
        "backup",
        help="検証済みSQLiteの非上書きbackup",
    )
    backup_subparsers = backup.add_subparsers(
        dest="backup_command",
        required=True,
    )
    backup_database_parser = backup_subparsers.add_parser("database")
    backup_database_parser.add_argument("--file", required=True, type=Path)
    backup_database_parser.add_argument("--out-dir", required=True, type=Path)
    backup_database_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    restore = subparsers.add_parser(
        "restore",
        help="SHA-256確認済みbackupからSQLiteを復旧",
    )
    restore_subparsers = restore.add_subparsers(
        dest="restore_command",
        required=True,
    )
    restore_database_parser = restore_subparsers.add_parser("database")
    restore_database_parser.add_argument("--backup", required=True, type=Path)
    restore_database_parser.add_argument("--target", required=True, type=Path)
    restore_database_parser.add_argument(
        "--accept-sha256",
        required=True,
    )
    restore_database_parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    generated = subparsers.add_parser(
        "generated-files",
        help="manifestが宣言した生成物を削除せず一覧",
    )
    generated.add_argument("--vault", required=True, type=Path)
    generated.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    smoke_test = subparsers.add_parser(
        "smoke-test",
        help="オンライン／オフライン再構築と意味差分を一括検証",
    )
    smoke_subparsers = smoke_test.add_subparsers(
        dest="smoke_command",
        required=True,
    )
    smoke_bootstrap = smoke_subparsers.add_parser(
        "bootstrap",
        help="自治体Tier 0〜1のライブ・再現性・秘密値境界を検証",
    )
    smoke_bootstrap.add_argument("municipality_name")
    smoke_bootstrap.add_argument("--prefecture")
    smoke_bootstrap.add_argument("--work-dir", type=Path)
    smoke_bootstrap.add_argument(
        "--refresh",
        action="store_true",
        help="cold-cache相当として公式参照先を再取得",
    )
    smoke_bootstrap.add_argument(
        "--no-cross-check",
        action="store_true",
        help="決算カードによる財政クロスチェックを省略",
    )
    smoke_bootstrap.add_argument(
        "--max-live-requests",
        type=int,
        default=40,
        help="オンライン実行のHTTPリクエスト上限",
    )
    smoke_bootstrap.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
    )
    return parser


def _render_freshness(report: dict[str, Any]) -> str:
    freshness = report["bootstrap"]["freshness"]
    lines = [
        "# Tier 1 freshness",
        "",
        f"- 状態: `{freshness['state']}`",
        f"- Database: `{report['bootstrap'].get('database') or '未設定'}`",
        f"- 評価日時: `{freshness.get('evaluated_at') or report['generated_at']}`",
        "",
        "| 参照先 | 状態 | 対象期 | 最終確認 | 次回確認期限 | 理由 |",
        "|---|---|---|---|---|---|",
    ]
    for source in sorted(
        freshness.get("sources", []),
        key=lambda item: item["source_id"],
    ):
        lines.append(
            f"| `{source['source_id']}` | `{source['state']}` | "
            f"{_escape(', '.join(source['source_periods']))} | "
            f"`{source.get('checked_at') or ''}` | "
            f"`{source.get('check_due_at') or ''}` | "
            f"{_escape(source['reason'])} |"
        )
    if not freshness.get("sources"):
        lines.append("| — | `unknown` |  |  |  | DB未設定または評価不能 |")
    return "\n".join(lines).rstrip() + "\n"


def _render_output_check(report: dict[str, Any]) -> str:
    lines = [
        "# 公開前output安全検査",
        "",
        f"- 対象: `{report['file']}`",
        f"- 判定: `{report['status']}`",
        f"- Error: `{report['counts']['error']}`",
        f"- Warning: `{report['counts']['warning']}`",
        "- この検査は読み取り専用で、自動修正・公開・送信を行いません。",
        "",
        "| Severity | Code | 行 | 理由 | 該当断片 |",
        "|---|---|---:|---|---|",
    ]
    for item in report["findings"]:
        lines.append(
            f"| `{item['severity']}` | `{item['code']}` | "
            f"{item['line']} | {_escape(item['message'])} | "
            f"`{_escape(item['snippet'])}` |"
        )
    if not report["findings"]:
        lines.append("| — | — | — | 機械検査の検出項目なし | — |")
    lines.extend(
        [
            "",
            "> [!warning]",
            "> 検出0件は内容の正確性、数値4点セット、再識別不可能性を保証しません。議員本人の最終レビューが必要です。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _output_check_failed(report: dict[str, Any], threshold: str) -> bool:
    if threshold == "never":
        return False
    if threshold == "warning":
        return bool(
            report["counts"]["error"] or report["counts"]["warning"]
        )
    return bool(report["counts"]["error"])


def _render_database_verification(report: dict[str, Any]) -> str:
    lines = [
        "# Database検証",
        "",
        f"- Database: `{report['database']}`",
        f"- 判定: `{'passed' if report['ok'] else 'failed'}`",
        f"- Schema: `{report['schema'].get('schema_version') or 'unknown'}`",
        f"- 互換性: `{report['schema'].get('state')}`",
        "",
        "| Check | 状態 | 詳細 |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        lines.append(
            f"| `{item['name']}` | `{item['status']}` | "
            f"{_escape(item.get('detail', ''))} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_backup(report: dict[str, Any]) -> str:
    return (
        "# Database backup\n\n"
        f"- 状態: `{report['status']}`\n"
        f"- Source: `{report['source']}`\n"
        f"- Backup: `{report['backup']}`\n"
        f"- Backup SHA-256: `{report['backup_sha256']}`\n"
        f"- 作成日時: `{report['created_at']}`\n"
    )


def _render_restore(report: dict[str, Any]) -> str:
    return (
        "# Database recovery\n\n"
        f"- 状態: `{report['status']}`\n"
        f"- Backup: `{report['backup']}`\n"
        f"- Target: `{report['target']}`\n"
        f"- Target SHA-256: `{report['target_sha256']}`\n"
        f"- 旧DB退避: `{report.get('previous') or 'なし'}`\n"
        f"- 復旧日時: `{report['restored_at']}`\n"
    )


def _render_generated_files(report: dict[str, Any]) -> str:
    lines = [
        "# Manifest生成物一覧",
        "",
        f"- Vault: `{report['vault']}`",
        f"- Artifact数: `{len(report['artifacts'])}`",
        "- この一覧は読み取り専用で、ファイルを削除しません。",
        "",
        "| 種別 | 場所 | 存在 | Path | Run |",
        "|---|---|---|---|---|",
    ]
    for item in report["artifacts"]:
        lines.append(
            f"| `{item['kind']}` | `{item['location']}` | "
            f"`{str(item['exists']).lower()}` | `{item['path']}` | "
            f"`{item.get('run_id') or ''}` |"
        )
    if not report["artifacts"]:
        lines.append("| — | — | — | manifest生成物なし | — |")
    if report["invalid_manifests"]:
        lines.extend(["", "## 読み取れないmanifest", ""])
        lines.extend(f"- `{path}`" for path in report["invalid_manifests"])
    return "\n".join(lines).rstrip() + "\n"


def _render_doctor(report: dict[str, Any]) -> str:
    recommendation = report["recommendation"]
    lines = [
        "# local-councilor-ai-os doctor",
        "",
        f"- Vault: `{report['vault']}`",
        f"- 推奨モード: `{report['diagnosis'].get('recommended_mode')}`",
        f"- AIクライアント: `{report['diagnosis'].get('agent')}`",
        "",
        "## Readiness",
        "",
        "| ゲート | 状態 |",
        "|---|---|",
    ]
    for name in sorted(report["requirements"]):
        lines.append(f"| `{name}` | `{report['requirements'][name]}` |")
    lines.extend(
        [
            "",
            f"- Tier 1: `{report['bootstrap']['state']}` / "
            f"鮮度 `{report['bootstrap']['freshness']}`",
            "",
            "## Modules",
            "",
            "| Module | 状態 |",
            "|---|---|",
        ]
    )
    for name in sorted(report.get("modules", {})):
        lines.append(f"| `{name}` | `{report['modules'][name]['state']}` |")
    lines.extend(
        [
            "",
            "## 次の一手",
            "",
            f"- コマンド: `{recommendation['next_command']}`",
            f"- 理由: {_escape(recommendation['reason'])}",
        ]
    )
    if report["diagnosis"].get("error"):
        lines.append(f"- 診断メモ: {_escape(report['diagnosis']['error'])}")
    return "\n".join(lines).rstrip() + "\n"


def _render_smoke_test(report: dict[str, Any]) -> str:
    lines = [
        "# Bootstrap smoke test",
        "",
        f"- 状態: `{report['status']}`",
        f"- 自治体: `{report['municipality']['prefecture']}"
        f"{report['municipality']['name']}`",
        f"- Work directory: `{report['work_directory']}`",
        f"- Online requests: "
        f"`{report['online']['retrieval'].get('live_request_count', 0)}`",
        f"- Online cache hits: "
        f"`{report['online']['retrieval'].get('cache_hit_count', 0)}`",
        f"- Offline requests: `{report['offline']['live_request_count']}`",
        "",
        "| Check | 状態 | 詳細 |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        lines.append(
            f"| `{item['name']}` | `{item['status']}` | "
            f"{_escape(item.get('detail', ''))} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            report = run_doctor(
                args.vault,
                agent=args.agent,
                probe_obsidian=args.probe_obsidian,
                bootstrap_database=args.bootstrap_db,
            )
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_doctor(report), end="")
            return int(report["recommendation"]["exit_code"])
        if args.command == "verify" and args.verify_command == "output":
            report = check_output_file(args.file)
            report["status"] = (
                "blocked"
                if _output_check_failed(report, args.fail_on)
                else "passed"
            )
            report["fail_on"] = args.fail_on
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_output_check(report), end="")
            return 2 if report["status"] == "blocked" else 0
        if args.command == "verify" and args.verify_command == "database":
            report = verify_bootstrap_database(args.file)
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_database_verification(report), end="")
            return 0 if report["ok"] else 2
        if args.command == "backup" and args.backup_command == "database":
            report = backup_database(args.file, args.out_dir)
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_backup(report), end="")
            return 0
        if args.command == "restore" and args.restore_command == "database":
            report = restore_database(
                args.backup,
                args.target,
                accepted_sha256=args.accept_sha256,
            )
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_restore(report), end="")
            return 0
        if args.command == "generated-files":
            report = generated_artifacts(args.vault)
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_generated_files(report), end="")
            return 0
        if args.command == "smoke-test" and args.smoke_command == "bootstrap":
            report = run_bootstrap_smoke_test(
                args.municipality_name,
                prefecture=args.prefecture,
                work_dir=args.work_dir,
                refresh=args.refresh,
                cross_check=not args.no_cross_check,
                max_live_requests=args.max_live_requests,
            )
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(_render_smoke_test(report), end="")
            return 0 if report["status"] == "passed" else 2

        report = build_status(
            args.vault,
            bootstrap_database=args.bootstrap_db,
        )
        if args.command == "freshness":
            freshness_report = {
                "schema_version": report["schema_version"],
                "product": report["product"],
                "generated_at": report["generated_at"],
                "database": report["bootstrap"].get("database"),
                "freshness": report["bootstrap"]["freshness"],
                "warnings": [
                    item
                    for item in report["warnings"]
                    if item["component"] == "bootstrap"
                ],
            }
            if args.format == "json":
                print(json.dumps(freshness_report, ensure_ascii=False, indent=2))
            else:
                print(_render_freshness(report), end="")
            return 0
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_markdown(report), end="")
        required = tuple(
            dict.fromkeys(
                requirement
                for group in args.require
                for requirement in group
            )
        )
        return 0 if requirements_met(report, required) else 2
    except (SmokeTestError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
