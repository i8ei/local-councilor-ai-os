"""Command-line interface for diagnosis and safe onboarding scaffolding."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .core import (
    AUTO_AGENT,
    AGENTS,
    FEATURES,
    LAYOUTS,
    MODES,
    OnboardingError,
    apply_scaffold,
    build_plan,
    diagnose_environment,
    load_vault_map,
    public_plan,
    verify_scaffold,
)


def _feature_list(value: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = sorted(set(values) - FEATURES)
    if unknown:
        raise argparse.ArgumentTypeError(f"未対応の機能: {', '.join(unknown)}")
    return values


def _render_status_table(title: str, values: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", "", "| 項目 | 状態 | 詳細 |", "|---|---|---|"]
    for name, item in values.items():
        detail = str(item.get("detail", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{name}` | `{item.get('status', '')}` | {detail} |")
    lines.append("")
    return lines


def _render_diagnosis(value: dict[str, Any]) -> str:
    requested_agent = value.get("requested_agent", value["agent"])
    selected_agent = value["agent"]
    lines = [
        "# 導入診断",
        "",
        f"- Vault: `{value['vault_path']}`",
        f"- AIクライアント指定: `{requested_agent}`",
        f"- 選択結果: `{selected_agent}`",
        f"- 推奨モード: `{value['recommended_mode']}`",
        f"- 推奨layout: `{value['recommended_layout']}`",
        "- この診断は読み取り専用です。",
        "",
    ]
    lines.extend(_render_status_table("能力", value["capabilities"]))
    lines.extend(_render_status_table("AIクライアント", value["ai_clients"]))
    lines.extend(_render_status_table("コマンド", value["commands"]))
    lines.extend(_render_status_table("認証情報", value["credentials"]))
    lines.extend(_render_status_table("権限プリフライト", value["permission_preflight"]))
    if value["next_steps"]:
        lines.extend(["## 次の操作", ""])
        lines.extend(f"{index}. {step}" for index, step in enumerate(value["next_steps"], 1))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_plan(value: dict[str, Any]) -> str:
    lines = [
        "# 導入計画プレビュー",
        "",
        f"- Plan SHA-256: `{value['plan_sha256']}`",
        f"- 状態: `{value['status']}`",
        f"- Vault: `{value['vault_path']}`",
        f"- モード: `{value['mode']}`",
        f"- layout: `{value['layout']}`",
        f"- 選択機能: `{', '.join(value['features']) or 'なし'}`",
        "",
    ]
    if value["blockers"]:
        lines.extend(["## 停止条件", ""])
        lines.extend(f"- {item}" for item in value["blockers"])
        lines.append("")
    if value.get("role_mappings"):
        lines.extend(
            [
                "## 既存Vault役割対応",
                "",
                "| 役割 | 既存path |",
                "|---|---|",
            ]
        )
        lines.extend(
            f"| `{role}` | `{path}` |"
            for role, path in value["role_mappings"].items()
        )
        lines.append("")
    lines.extend(["## ファイル操作", "", "| 状態 | 対象 |", "|---|---|"])
    lines.extend(f"| `{item['status']}` | `{item['target']}` |" for item in value["actions"])
    lines.append("")
    lines.extend(["## 外部通信", ""])
    lines.extend(f"- {item}" for item in value["network"])
    if not value["network"]:
        lines.append("- なし")
    lines.extend(["", "## 個別確認を残す操作", ""])
    lines.extend(
        f"- {item}" for item in value["approval_preview"]["separate_confirmation"]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_verification(value: dict[str, Any]) -> str:
    lines = [
        "# scaffold検証",
        "",
        f"- 状態: `{value['status']}`",
        f"- scaffold: `{value['scaffold_status']}`",
        f"- profile: `{value['profile_status']}`",
        f"- Manifest: `{value['manifest']}`",
        "",
    ]
    if value["failures"]:
        lines.extend(["## 失敗", ""])
        lines.extend(f"- {item}" for item in value["failures"])
    else:
        lines.extend(
            [
                "ファイル完全性とObsidian CLIの対象Vault疎通を確認しました。",
                "利用者・議会プロファイルの確認は未完了です。",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _print(value: dict[str, Any], output_format: str, kind: str) -> None:
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif kind == "diagnosis":
        print(_render_diagnosis(value), end="")
    elif kind == "plan":
        print(_render_plan(value), end="")
    else:
        print(_render_verification(value), end="")


def _diagnosis_exit_code(value: dict[str, Any]) -> int:
    selection_status = value["capabilities"]["ai_client_selection"]["status"]
    if selection_status == "needs-confirmation":
        return 2
    handoff_keys = (
        "ai_client_selection",
        "vault_registered",
        "instruction_file",
        "obsidian_cli",
    )
    if any(
        value["capabilities"][key]["status"] == "handoff_required"
        for key in handoff_keys
    ):
        return 3
    if value["capabilities"]["vault_directory"]["status"] != "reuse":
        return 2
    return 0


def _plan_exit_code(value: dict[str, Any]) -> int:
    if value["status"] == "handoff_required":
        return 3
    if value["status"] != "ready":
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m onboarding",
        description="地方議員AI運用OSの読み取り専用診断と安全なVault scaffold",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose = subparsers.add_parser("diagnose", help="環境を読み取り専用で診断")
    diagnose.add_argument("--vault", required=True, help="対象Vaultの絶対パス")
    agent_choices = sorted((*AGENTS, AUTO_AGENT))
    diagnose.add_argument("--agent", choices=agent_choices, default=AUTO_AGENT)
    diagnose.add_argument(
        "--skip-obsidian-probe",
        action="store_true",
        help="対象VaultのCLI疎通確認を省略（診断結果は未確認になる）",
    )
    diagnose.add_argument("--format", choices=("markdown", "json"), default="markdown")

    plan = subparsers.add_parser("plan", help="決定的な変更計画を読み取り専用で作成")
    plan.add_argument("--vault", required=True, help="対象Vaultの絶対パス")
    plan.add_argument("--agent", choices=agent_choices, default=AUTO_AGENT)
    plan.add_argument("--mode", choices=sorted(MODES), default=None)
    plan.add_argument("--layout", choices=sorted(LAYOUTS), default=None)
    plan.add_argument(
        "--vault-map",
        help="preserve layoutで使う確認済みvault-map.yaml",
    )
    plan.add_argument(
        "--features",
        type=_feature_list,
        default=None,
        help="core,templates,workflows等をカンマ区切りで指定",
    )
    plan.add_argument("--format", choices=("markdown", "json"), default="markdown")

    scaffold = subparsers.add_parser(
        "scaffold",
        help="確認済み計画と一致する場合だけ不足ファイルを新規作成",
    )
    scaffold.add_argument("--vault", required=True, help="対象Vaultの絶対パス")
    scaffold.add_argument("--agent", choices=agent_choices, default=AUTO_AGENT)
    scaffold.add_argument("--mode", choices=("integrate",), default="integrate")
    scaffold.add_argument("--layout", choices=sorted(LAYOUTS), default=None)
    scaffold.add_argument(
        "--vault-map",
        help="planと同じ確認済みvault-map.yaml",
    )
    scaffold.add_argument(
        "--features",
        type=_feature_list,
        default=None,
        help="planと同じ機能をカンマ区切りで指定",
    )
    scaffold.add_argument(
        "--accept-plan-sha256",
        required=True,
        help="直前にplanで確認したSHA-256",
    )
    scaffold.add_argument("--format", choices=("markdown", "json"), default="markdown")

    verify = subparsers.add_parser("verify", help="実行記録とscaffoldを読み取り専用で検証")
    verify.add_argument("--manifest", required=True, help="run manifestの絶対パス")
    verify.add_argument(
        "--skip-obsidian-probe",
        action="store_true",
        help="Obsidian CLI疎通確認を省略",
    )
    verify.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_scaffold(
                args.manifest,
                probe_obsidian=not args.skip_obsidian_probe,
            )
            _print(result, args.format, "verification")
            return 2 if result["status"] == "failed" else 0

        diagnosis = diagnose_environment(
            args.vault,
            agent=args.agent,
            probe_obsidian=not getattr(args, "skip_obsidian_probe", False),
        )
        if args.command == "diagnose":
            _print(diagnosis, args.format, "diagnosis")
            return _diagnosis_exit_code(diagnosis)

        role_mappings = (
            load_vault_map(args.vault_map, vault=args.vault)
            if getattr(args, "vault_map", None)
            else None
        )
        plan = build_plan(
            diagnosis,
            mode=args.mode,
            layout=args.layout,
            features=args.features,
            role_mappings=role_mappings,
        )
        if args.command == "plan":
            _print(public_plan(plan), args.format, "plan")
            return _plan_exit_code(plan)

        result = apply_scaffold(
            plan,
            accepted_plan_sha256=args.accept_plan_sha256,
        )
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("# scaffold作成\n")
            print(f"- 状態: `{result['status']}`")
            print(f"- scaffold: `{result['scaffold_status']}`")
            print(f"- profile: `{result['profile_status']}`")
            print(f"- Manifest: `{result['manifest']}`")
        return 0
    except OnboardingError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
