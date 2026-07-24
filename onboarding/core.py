"""Functional onboarding diagnosis, planning, and safe scaffold creation."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
AUTO_AGENT = "auto"
AGENT_ORDER = ("codex", "claude")
AGENTS = {"codex", "claude"}
AGENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
}
MODES = {"integrate", "full", "components", "diagnose"}
LAYOUTS = {"scaffold", "preserve"}
FEATURES = {
    "core",
    "templates",
    "workflows",
    "bootstrap",
    "minutes",
    "regulations",
    "budget",
    "settlement",
    "grants",
}
DEFAULT_FEATURES = {
    "integrate": ("core", "templates", "workflows"),
    "full": ("core", "templates", "workflows"),
    "components": (),
    "diagnose": (),
}

SHELVES = (
    ("会期", "会期MOC.md", "会期中の案件、期限、提出物の現在地を束ねる入口", False),
    (
        "一般質問",
        "一般質問MOC.md",
        "一般質問の設計、通告前検算、答弁後追跡をつなぐ入口",
        False,
    ),
    ("予算決算", "予算決算MOC.md", "予算化、執行、決算、総額照合を追う入口", False),
    ("条例制度", "条例制度MOC.md", "法令、例規、制度要件、改廃履歴を確認する入口", False),
    ("行政視察", "行政視察MOC.md", "視察目的、確認事項、公開原典、帰着後の判断を残す入口", False),
    ("広報", "広報MOC.md", "読者別の公開説明と公開前レビューを管理する入口", False),
    (
        "住民の声",
        "住民の声MOC.md",
        "内部原典を隔離し、公開情報で検証可能な問いへの変換を追う入口",
        True,
    ),
    ("証拠台帳", "証拠台帳MOC.md", "一主張一項目で原典位置と検証状態を管理する入口", False),
)

COMMANDS = ("python3", "sqlite3", "pdftotext", "obsidian", "claude", "codex", "git")
NETWORK_BY_FEATURE = {
    "bootstrap": ("api.e-stat.go.jp", "www.e-stat.go.jp", "www.soumu.go.jp"),
    "minutes": ("利用者が確認した自治体公式議会サイト",),
    "regulations": ("利用者が確認した自治体公式例規サイト",),
    "grants": ("api.jgrants-portal.go.jp", "developers.digital.go.jp"),
}
ROOT_MOC = "地方議員AI運用OS MOC.md"
SETUP_NOTE = "inbox/地方議員AI運用OSセットアップ記録.md"
CONTROL_DIRECTORY = ".local-councilor-ai-os"
VAULT_MAP_FILE = "vault-map.yaml"
INSTANCE_FILE = "instance.json"
PRESERVE_ROLE_BASENAMES = {
    "sessions": ("会期",),
    "general_questions": ("一般質問",),
    "question_archive": ("一般質問集",),
    "budget": ("予算", "予算決算"),
    "settlement": ("決算",),
    "regulations": ("例規", "条例制度"),
    "inspections": ("行政視察",),
    "public_relations": ("広報",),
    "resident_voices": ("住民の声",),
    "evidence_ledger": ("証拠台帳",),
    "templates": ("Templates", "テンプレート"),
}
PRESERVE_ROLES = frozenset({"vault_home", *PRESERVE_ROLE_BASENAMES})


class OnboardingError(RuntimeError):
    """Raised when a safe onboarding precondition is not met."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _normalize_path_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _status(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def _active_instruction(vault: Path, agent: str) -> tuple[str | None, list[str]]:
    if agent not in AGENTS:
        raise OnboardingError(f"未対応のAIエージェントです: {agent}")
    candidates = (
        ("AGENTS.override.md", "AGENTS.md")
        if agent == "codex"
        else ("CLAUDE.md",)
    )
    found = [
        name
        for name in candidates
        if (vault / name).is_file() and (vault / name).stat().st_size > 0
    ]
    return (found[0] if found else None), found


def _select_agent(
    requested_agent: str,
    commands: dict[str, str | None],
) -> tuple[str | None, dict[str, Any]]:
    if requested_agent != AUTO_AGENT:
        if requested_agent not in AGENTS:
            raise OnboardingError(f"未対応のAIエージェントです: {requested_agent}")
        if commands[requested_agent]:
            return requested_agent, _status(
                "reuse",
                f"{AGENT_LABELS[requested_agent]}を明示指定",
                requested=requested_agent,
                selected=requested_agent,
            )
        return requested_agent, _status(
            "handoff_required",
            f"{AGENT_LABELS[requested_agent]}のCLIがPATHにない",
            requested=requested_agent,
            selected=requested_agent,
        )

    available = [agent for agent in AGENT_ORDER if commands[agent]]
    if len(available) == 1:
        selected = available[0]
        return selected, _status(
            "reuse",
            f"利用可能なAIクライアントを自動選択: {AGENT_LABELS[selected]}",
            requested=AUTO_AGENT,
            selected=selected,
            available=available,
        )
    if len(available) > 1:
        labels = "、".join(AGENT_LABELS[agent] for agent in available)
        return None, _status(
            "needs-confirmation",
            f"複数のAIクライアントを検出: {labels}。今回使うものを一度選択",
            requested=AUTO_AGENT,
            selected=None,
            available=available,
        )
    return None, _status(
        "handoff_required",
        "Claude CodeとCodexのCLIを確認できない",
        requested=AUTO_AGENT,
        selected=None,
        available=[],
    )


def _client_inventory(
    vault: Path,
    commands: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    clients: dict[str, dict[str, Any]] = {}
    for agent in AGENT_ORDER:
        active, found = _active_instruction(vault, agent)
        command = commands[agent]
        detail_parts = [f"CLI: {command}" if command else "CLI: 未検出"]
        detail_parts.append(
            f"有効ガイド: {active}" if active else "有効ガイド: 未設定"
        )
        clients[agent] = _status(
            "reuse" if command else "unavailable",
            "、".join(detail_parts),
            command=command,
            active_instruction=active,
            found_instruction_files=found,
        )
    return clients


def _handoff_steps(
    *,
    vault: Path,
    requested_agent: str,
    selected_agent: str | None,
    selection_status: str,
    active_instruction: str | None,
    registered: bool,
    obsidian_status: str,
) -> list[str]:
    quoted_vault = shlex.quote(str(vault))
    setup_url = "https://github.com/i8ei/claude-obsidian-setup"
    steps: list[str] = []

    if not registered:
        steps.append(
            "Obsidianを起動し、対象フォルダを「フォルダを保管庫として開く」で"
            "Vault登録する"
        )
    if obsidian_status != "reuse":
        steps.append(
            "Obsidianの対象Vaultを開いた状態で、CLIのVault一覧・絶対パス・検索疎通を確認する"
        )
    if selection_status == "needs-confirmation":
        steps.append("今回の導入をClaude CodeとCodexのどちらで進めるか選ぶ")
        for agent in AGENT_ORDER:
            steps.append(
                "選択後に再診断: "
                f"`python3 -m onboarding diagnose --vault {quoted_vault} "
                f"--agent {agent}`"
            )
        return steps
    if selection_status == "handoff_required":
        label = AGENT_LABELS.get(selected_agent or requested_agent, "AIクライアント")
        steps.append(f"{label}のCLIを基盤セットアップで利用可能にする: {setup_url}")
    if selected_agent and not active_instruction:
        if selected_agent == "codex":
            steps.append(
                "Vault直下に非空の`AGENTS.md`を用意する。"
                "`AGENTS.override.md`がある場合はそちらが優先される"
            )
            steps.append(
                "Codexで対象Vaultがwritable roots内か、変更時の承認方式と合わせて確認する"
            )
        else:
            steps.append("Vault直下に非空の`CLAUDE.md`を用意する")
            steps.append(
                "Claude Codeで対象Vaultの読み書き許可と変更時の承認方式を確認する"
            )
        steps.append(f"基盤手順: {setup_url}")
    if selected_agent and (
        selection_status != "reuse"
        or not active_instruction
        or not registered
        or obsidian_status != "reuse"
    ):
        steps.append(
            "完了後に再診断: "
            f"`python3 -m onboarding diagnose --vault {quoted_vault} "
            f"--agent {selected_agent}`"
        )
    return steps


def _probe_obsidian(command: str, vault: Path) -> dict[str, Any]:
    commands_run: list[list[str]] = []
    try:
        list_command = [command, "vaults", "verbose"]
        commands_run.append(list_command)
        result = subprocess.run(
            list_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _status("needs-confirmation", f"Obsidian CLI確認に失敗: {error}")

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    vault_name = None
    normalized_vault = _normalize_path_text(str(vault.resolve()))
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, listed_path = line.split("\t", 1)
        try:
            resolved_listed = str(Path(listed_path.strip()).expanduser().resolve())
        except OSError:
            continue
        if _normalize_path_text(resolved_listed) == normalized_vault:
            vault_name = name.strip()
            break
    if result.returncode != 0 or not vault_name:
        detail = "Obsidian CLIの一覧に対象Vaultの絶対パス完全一致を確認できない"
        if result.returncode != 0:
            detail += f"（終了コード {result.returncode}）"
        return _status(
            "handoff_required",
            detail,
            command=command,
            commands_run=commands_run,
            output_preview=output[:800],
        )

    path_command = [command, f"vault={vault_name}", "vault", "info=path"]
    search_command = [
        command,
        f"vault={vault_name}",
        "search",
        "query=__local_councilor_ai_os_probe__",
        "total",
    ]
    commands_run.extend((path_command, search_command))
    try:
        path_result = subprocess.run(
            path_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        search_result = subprocess.run(
            search_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _status(
            "handoff_required",
            f"対象Vaultを指定したObsidian CLI確認に失敗: {error}",
            vault_name=vault_name,
            commands_run=commands_run,
        )

    try:
        reported_path = str(Path(path_result.stdout.strip()).expanduser().resolve())
    except OSError:
        reported_path = path_result.stdout.strip()
    if (
        path_result.returncode == 0
        and search_result.returncode == 0
        and _normalize_path_text(reported_path) == normalized_vault
    ):
        return _status(
            "reuse",
            "Obsidian CLIが対象Vaultの絶対パスと検索疎通を確認",
            command=command,
            vault_name=vault_name,
            commands_run=commands_run,
        )
    detail = "対象Vaultを明示したCLIのパス確認または検索疎通に失敗"
    if result.returncode != 0:
        detail += f"（一覧終了コード {result.returncode}）"
    return _status(
        "handoff_required",
        detail,
        command=command,
        vault_name=vault_name,
        commands_run=commands_run,
        path_output=path_result.stdout[:800],
        search_output=search_result.stdout[:800],
    )


def _relative_vault_path(vault: Path, path: Path) -> str | None:
    try:
        relative = path.resolve(strict=False).relative_to(vault.resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def _preserve_role_candidates(vault: Path) -> dict[str, list[str]]:
    """Find unambiguous role candidates without reading note content."""

    if not vault.is_dir():
        return {}
    candidates: dict[str, list[str]] = {}
    home = vault / "HOME.md"
    if home.is_file() and not home.is_symlink():
        candidates["vault_home"] = ["HOME.md"]

    names_to_roles = {
        name: role
        for role, names in PRESERVE_ROLE_BASENAMES.items()
        for name in names
    }
    ignored = {".git", ".obsidian", CONTROL_DIRECTORY}
    for root_text, directories, _ in os.walk(vault, followlinks=False):
        root = Path(root_text)
        relative_root = _relative_vault_path(vault, root)
        depth = 0 if relative_root in {None, "."} else len(Path(relative_root).parts)
        directories[:] = [
            name
            for name in directories
            if name not in ignored and not name.startswith(".")
        ]
        if depth >= 3:
            directories[:] = []
            continue
        for name in directories:
            role = names_to_roles.get(name)
            if not role:
                continue
            path = root / name
            if path.is_symlink():
                continue
            relative = _relative_vault_path(vault, path)
            if relative is not None:
                candidates.setdefault(role, []).append(relative)
    return {
        role: sorted(dict.fromkeys(paths))
        for role, paths in sorted(candidates.items())
    }


def _recommended_layout(
    *,
    shelf_count: int,
    candidates: dict[str, list[str]],
) -> str:
    if shelf_count == len(SHELVES):
        return "scaffold"
    unambiguous = sum(1 for paths in candidates.values() if len(paths) == 1)
    return "preserve" if unambiguous >= 2 else "scaffold"


def _default_role_mappings(
    candidates: dict[str, list[str]],
) -> dict[str, str]:
    return {
        role: paths[0]
        for role, paths in sorted(candidates.items())
        if len(paths) == 1
    }


def _validate_preserve_mapping(
    vault: Path,
    mappings: dict[str, str],
) -> dict[str, str]:
    if not isinstance(mappings, dict) or not mappings:
        raise OnboardingError(
            "preserve layoutには1件以上の既存Vault役割対応が必要です"
        )
    normalized: dict[str, str] = {}
    for role, raw_path in sorted(mappings.items()):
        if role not in PRESERVE_ROLES:
            raise OnboardingError(f"未対応のVault役割です: {role}")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise OnboardingError(f"Vault役割 `{role}` のpathが空です")
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            raise OnboardingError(
                f"Vault役割 `{role}` はVault相対pathで指定してください"
            )
        target = vault / candidate
        current = target
        while current != vault and current != current.parent:
            if current.exists() and current.is_symlink():
                raise OnboardingError(
                    f"Vault役割 `{role}` のpathにsymlinkを含められません"
                )
            current = current.parent
        relative = _relative_vault_path(vault, target)
        if relative is None or relative in {"", "."}:
            raise OnboardingError(f"Vault役割 `{role}` がVault外を指しています")
        if not target.exists():
            raise OnboardingError(
                f"Vault役割 `{role}` の既存pathがありません: {relative}"
            )
        if role == "vault_home":
            if not target.is_file():
                raise OnboardingError("vault_homeは既存ファイルを指定してください")
        elif not target.is_dir():
            raise OnboardingError(
                f"Vault役割 `{role}` は既存ディレクトリを指定してください"
            )
        normalized[role] = relative
    return normalized


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _vault_map_content(
    mappings: dict[str, str],
    *,
    managed_namespace: str = "地方議員AI運用OS",
) -> str:
    lines = [
        "schema_version: 1",
        "product: local-councilor-ai-os",
        "layout: preserve",
        f"managed_namespace: {_yaml_string(managed_namespace)}",
        "roles:",
    ]
    lines.extend(
        f"  {role}: {_yaml_string(path)}"
        for role, path in sorted(mappings.items())
    )
    return "\n".join(lines) + "\n"


def _parse_vault_map(content: str) -> dict[str, Any]:
    value: dict[str, Any] = {}
    roles: dict[str, str] = {}
    in_roles = False
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line == "roles:":
            in_roles = True
            value["roles"] = roles
            continue
        if in_roles and raw_line.startswith("  "):
            key, separator, raw_value = raw_line.strip().partition(":")
            if not separator or key not in PRESERVE_ROLES:
                raise OnboardingError(
                    f"vault-map.yamlのrolesが不正です（{line_number}行目）"
                )
            try:
                parsed = json.loads(raw_value.strip())
            except json.JSONDecodeError as error:
                raise OnboardingError(
                    f"vault-map.yamlのpathは引用文字列で指定してください"
                    f"（{line_number}行目）"
                ) from error
            if not isinstance(parsed, str):
                raise OnboardingError(
                    f"vault-map.yamlのpathが文字列ではありません"
                    f"（{line_number}行目）"
                )
            roles[key] = parsed
            continue
        in_roles = False
        key, separator, raw_value = raw_line.partition(":")
        if not separator:
            raise OnboardingError(
                f"vault-map.yamlの形式が不正です（{line_number}行目）"
            )
        raw_value = raw_value.strip()
        if key == "schema_version":
            value[key] = int(raw_value) if raw_value.isdigit() else raw_value
        elif key in {"product", "layout"}:
            value[key] = raw_value
        elif key == "managed_namespace":
            try:
                value[key] = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise OnboardingError(
                    "vault-map.yamlのmanaged_namespaceは引用文字列で指定してください"
                ) from error
        else:
            raise OnboardingError(f"vault-map.yamlの未対応フィールドです: {key}")
    if value.get("schema_version") != 1:
        raise OnboardingError("vault-map.yamlのschema_versionが未対応です")
    if value.get("product") != "local-councilor-ai-os":
        raise OnboardingError("vault-map.yamlのproductが一致しません")
    if value.get("layout") != "preserve":
        raise OnboardingError("vault-map.yamlのlayoutがpreserveではありません")
    if not roles:
        raise OnboardingError("vault-map.yamlにrolesがありません")
    return value


def load_vault_map(
    path: str | os.PathLike[str],
    *,
    vault: str | os.PathLike[str],
) -> dict[str, str]:
    """Load the constrained, dependency-free preserve mapping format."""

    source = Path(path).expanduser().resolve()
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise OnboardingError(f"vault-mapを読み取れません: {error}") from error
    value = _parse_vault_map(content)
    return _validate_preserve_mapping(
        Path(vault).expanduser().resolve(),
        dict(value["roles"]),
    )


def _instance_content() -> str:
    return (
        "{\n"
        '  "schema_version": 1,\n'
        '  "product": "local-councilor-ai-os",\n'
        '  "paths": {}\n'
        "}\n"
    )


def diagnose_environment(
    vault_path: str | os.PathLike[str],
    *,
    agent: str = "codex",
    probe_obsidian: bool = True,
) -> dict[str, Any]:
    """Inspect onboarding capabilities without writing to disk."""

    vault = Path(vault_path).expanduser().resolve()
    exists = vault.is_dir()
    registered = exists and (vault / ".obsidian").is_dir()
    commands = {name: shutil.which(name) for name in COMMANDS}
    selected_agent, client_selection = _select_agent(agent, commands)
    clients = _client_inventory(vault, commands) if exists else {}
    active_instruction, instruction_files = (
        _active_instruction(vault, selected_agent)
        if exists and selected_agent
        else (None, [])
    )

    if commands["obsidian"] and probe_obsidian and exists:
        obsidian_cli = _probe_obsidian(str(commands["obsidian"]), vault)
    elif commands["obsidian"]:
        obsidian_cli = _status(
            "needs-confirmation",
            "CLIは存在するが対象Vaultの認識確認は未実施",
            command=commands["obsidian"],
        )
    else:
        obsidian_cli = _status(
            "handoff_required",
            "obsidianコマンドがPATHにない。基盤セットアップへ戻る",
        )

    if (
        registered
        and client_selection["status"] == "reuse"
        and active_instruction
        and obsidian_cli["status"] == "reuse"
    ):
        recommended_mode = "integrate"
    elif client_selection["status"] == "needs-confirmation":
        recommended_mode = "diagnose"
    elif exists:
        recommended_mode = "full"
    else:
        recommended_mode = "diagnose"

    shelf_count = sum(
        1 for directory, filename, _, _ in SHELVES if (vault / directory / filename).is_file()
    )
    template_count = (
        sum(
            1
            for _ in (
                vault / "テンプレート" / "local-councilor-ai-os"
            ).glob("*.md")
        )
        if (vault / "テンプレート").is_dir()
        else 0
    )
    role_candidates = _preserve_role_candidates(vault)
    recommended_layout = _recommended_layout(
        shelf_count=shelf_count,
        candidates=role_candidates,
    )
    detected_role_count = sum(
        1 for paths in role_candidates.values() if len(paths) == 1
    )
    ambiguous_roles = sorted(
        role for role, paths in role_candidates.items() if len(paths) > 1
    )
    scaffold_detail = (
        f"固定MOC {shelf_count}/{len(SHELVES)}、"
        f"既存役割 {detected_role_count}件、"
        f"推奨layout `{recommended_layout}`"
    )
    selected_agent_label = (
        AGENT_LABELS[selected_agent]
        if selected_agent is not None
        else "AIクライアント"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "read_only": True,
        "requested_agent": agent,
        "agent": selected_agent or AUTO_AGENT,
        "vault_path": str(vault),
        "recommended_mode": recommended_mode,
        "recommended_layout": recommended_layout,
        "role_candidates": role_candidates,
        "capabilities": {
            "ai_client_selection": client_selection,
            "vault_directory": _status(
                "reuse" if exists else "unavailable",
                "Vault候補のディレクトリが存在"
                if exists
                else "指定パスがディレクトリとして存在しない",
            ),
            "vault_registered": _status(
                "reuse" if registered else "handoff_required",
                ".obsidianを確認"
                if registered
                else "基盤セットアップでObsidianのVaultとして開く必要がある",
            ),
            "vault_readable": _status(
                "reuse" if exists and os.access(vault, os.R_OK) else "unavailable",
                "OS上で読み取り可能"
                if exists and os.access(vault, os.R_OK)
                else "OS上で読み取りを確認できない",
            ),
            "vault_writable": _status(
                "needs-confirmation"
                if exists and os.access(vault, os.W_OK)
                else "unavailable",
                "ホストOS上は書き込み可能。AIクライアントのworkspace境界は別途確認"
                if exists and os.access(vault, os.W_OK)
                else "OS上で書き込みを確認できない",
                host_writable=bool(exists and os.access(vault, os.W_OK)),
                agent_scope="needs-confirmation",
            ),
            "instruction_file": _status(
                (
                    "reuse"
                    if active_instruction
                    else client_selection["status"]
                    if selected_agent is None
                    else "handoff_required"
                ),
                f"{selected_agent_label}で有効なVaultガイド: {active_instruction}"
                if active_instruction
                else (
                    "AIクライアント選択後に有効なVaultガイドを確認"
                    if selected_agent is None
                    else f"{selected_agent_label}用の有効なVaultガイドがない"
                ),
                active=active_instruction,
                found=instruction_files,
            ),
            "obsidian_cli": obsidian_cli,
            "existing_scaffold": _status(
                "reuse"
                if shelf_count == len(SHELVES)
                else "preserve"
                if recommended_layout == "preserve"
                else "add",
                scaffold_detail,
                shelf_count=shelf_count,
                expected_shelf_count=len(SHELVES),
                template_count=template_count,
                detected_role_count=detected_role_count,
                ambiguous_roles=ambiguous_roles,
                recommended_layout=recommended_layout,
            ),
        },
        "ai_clients": clients,
        "commands": {
            name: _status(
                "reuse" if path else "unavailable",
                path or f"{name}がPATHにない",
            )
            for name, path in commands.items()
        },
        "credentials": {
            "ESTAT_APPID": _status(
                "reuse" if os.environ.get("ESTAT_APPID") else "add",
                "環境変数に設定済み（値は表示しない）"
                if os.environ.get("ESTAT_APPID")
                else "bootstrap選択時に必要。値はノートやログへ保存しない",
            )
        },
        "permission_preflight": {
            "ai_workspace_scope": _status(
                "needs-confirmation",
                (
                    "Codexのwritable roots、追加ディレクトリ、承認方式を確認"
                    if selected_agent == "codex"
                    else "Claude Codeの対象ディレクトリ権限と承認方式を確認"
                    if selected_agent == "claude"
                    else "使用するAIクライアントを選び、対象Vaultの権限と承認方式を確認"
                ),
            ),
            "global_changes": _status(
                "skip",
                "既存環境への統合ではグローバル設定・Skillを変更しない",
            ),
            "destructive_actions": _status(
                "skip",
                "scaffoldは既存ファイルを上書き・削除しない",
            ),
        },
        "next_steps": _handoff_steps(
            vault=vault,
            requested_agent=agent,
            selected_agent=selected_agent,
            selection_status=client_selection["status"],
            active_instruction=active_instruction,
            registered=registered,
            obsidian_status=obsidian_cli["status"],
        ),
    }


def _moc_content(title: str, description: str, internal: bool) -> str:
    frontmatter = ["---", f"description: {description}"]
    if internal:
        frontmatter.append("visibility: internal")
    frontmatter.extend(["---", "", f"# {title}", ""])
    if internal:
        frontmatter.extend(
            [
                "> [!warning]",
                "> 公開成果物からこのMOCへリンクしない。",
                "",
            ]
        )
    frontmatter.append("- 現在、登録済みの案件なし")
    return "\n".join(frontmatter) + "\n"


def _root_moc_content(include_workflows: bool) -> str:
    links = "\n".join(
        f"- [[{directory}/{filename.removesuffix('.md')}]]"
        for directory, filename, _, _ in SHELVES
    )
    workflow_section = (
        "\n## 常設規則とワークフロー\n\n"
        "- [[地方議員AI運用OS/運用MOC]]\n"
        if include_workflows
        else ""
    )
    return (
        "---\n"
        "description: 地方議員AI運用OSの業務棚、プロファイル、判断ノートへ移動する入口\n"
        "visibility: internal\n"
        "---\n\n"
        "# 地方議員AI運用OS MOC\n\n"
        "## 業務棚\n\n"
        f"{links}\n\n"
        "## 導入状態\n\n"
        "- [[inbox/地方議員AI運用OSセットアップ記録]]\n"
        "- プロファイルは本人確認が終わるまで未完了として扱う\n"
        f"{workflow_section}"
    )


def _operations_moc_content(repo_root: Path) -> str:
    principle_links = "\n".join(
        f"- [[地方議員AI運用OS/principles/{path.stem}]]"
        for path in sorted((repo_root / "principles").glob("*.md"))
    )
    workflow_links = "\n".join(
        f"- [[地方議員AI運用OS/workflows/{path.stem}]]"
        for path in sorted((repo_root / "workflows").glob("*.md"))
    )
    return (
        "---\n"
        "description: 地方議員AI運用OSの原則と七つの実務ワークフローを確認する入口\n"
        "---\n\n"
        "# 運用MOC\n\n"
        "## 常設規則\n\n"
        f"{principle_links}\n\n"
        "## ワークフロー\n\n"
        f"{workflow_links}\n"
    )


def _setup_note_content() -> str:
    return (
        "---\n"
        "description: 地方議員AI運用OSのscaffold作成結果と未完了項目を確認する記録\n"
        "status: incomplete\n"
        "---\n\n"
        "# 地方議員AI運用OS セットアップ記録\n\n"
        "> [!warning] OS全体の導入は未完了\n"
        "> 棚、MOC、テンプレートのscaffoldと、利用者プロファイル、データ基盤の"
        "設定完了は別の状態です。\n\n"
        "## scaffold\n\n"
        "- 8つの業務棚と各MOC: 作成または再利用\n"
        "- テンプレート: 作成または再利用\n"
        "- 地方議員AI運用OS MOC: 作成または再利用\n\n"
        "## 未完了\n\n"
        "- 議員本人の役割、所属、優先分野、公開境界の確認\n"
        "- 議会アダプターの公開情報による記入と検証\n"
        "- 任意データモジュールの選択\n"
        "- 既存のVault入口MOCへの接続確認\n"
    )


def _council_adapter_content() -> str:
    return (
        "---\n"
        "description: 自治体固有の議会日程、質問通告、委員会、会議録の公開経路を確認する未完了プロファイル\n"
        "status: incomplete\n"
        "---\n\n"
        "# 議会アダプター\n\n"
        "> [!warning] 未完了\n"
        "> 公開された議会運用をAIが調べ、人が原典を確認するまで完了扱いにしない。\n\n"
        "## 対象と責任範囲\n\n"
        "- 自治体: {{未確認}}\n"
        "- 都道府県: {{未確認}}\n"
        "- 最終確認日: {{未確認}}\n\n"
        "## 確認する公開情報\n\n"
        "- 会期・定例会の時期\n"
        "- 一般質問の通告様式、締切、文字数、提出方法\n"
        "- 委員会と会議種別\n"
        "- 会議録、議案、予算・決算資料の公式公開先\n"
        "- 自治体差分と共通ワークフローの境界\n"
    )


def _selected_features(mode: str, features: Iterable[str] | None) -> tuple[str, ...]:
    if mode not in MODES:
        raise OnboardingError(f"未対応の導入モードです: {mode}")
    selected = tuple(dict.fromkeys(features or DEFAULT_FEATURES[mode]))
    unknown = sorted(set(selected) - FEATURES)
    if unknown:
        raise OnboardingError(f"未対応の機能です: {', '.join(unknown)}")
    if mode == "components" and set(selected) & {"core", "templates", "workflows"}:
        raise OnboardingError(
            "componentsモードではcore、templates、workflowsを選択できません"
        )
    return selected


def _planned_files(
    vault: Path,
    repo_root: Path,
    selected: tuple[str, ...],
) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if "core" in selected:
        files.extend(
            {
                "kind": "generated",
                "target": str(vault / directory / filename),
                "content": _moc_content(filename.removesuffix(".md"), description, internal),
            }
            for directory, filename, description, internal in SHELVES
        )
        files.append(
            {
                "kind": "generated",
                "target": str(vault / ROOT_MOC),
                "content": _root_moc_content("workflows" in selected),
            }
        )
        files.extend(
            (
                {
                    "kind": "copy",
                    "source": str(repo_root / "profiles" / "councilor-profile.yaml"),
                    "target": str(vault / "プロファイル" / "councilor-profile.yaml"),
                },
                {
                    "kind": "generated",
                    "target": str(vault / "プロファイル" / "council-adapter.md"),
                    "content": _council_adapter_content(),
                },
                {
                    "kind": "generated",
                    "target": str(vault / SETUP_NOTE),
                    "content": _setup_note_content(),
                },
            )
        )
    if "workflows" in selected:
        files.append(
            {
                "kind": "generated",
                "target": str(vault / "地方議員AI運用OS" / "運用MOC.md"),
                "content": _operations_moc_content(repo_root),
            }
        )
        for directory in ("principles", "workflows"):
            for source in sorted((repo_root / directory).glob("*.md")):
                files.append(
                    {
                        "kind": "copy",
                        "source": str(source),
                        "target": str(
                            vault / "地方議員AI運用OS" / directory / source.name
                        ),
                    }
                )
    if "templates" in selected:
        for source in sorted((repo_root / "templates").glob("*.md")):
            files.append(
                {
                    "kind": "copy",
                    "source": str(source),
                    "target": str(
                        vault / "テンプレート" / "local-councilor-ai-os" / source.name
                    ),
                }
            )
    return files


def _planned_preserve_files(
    vault: Path,
    repo_root: Path,
    selected: tuple[str, ...],
    mappings: dict[str, str],
) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    control = vault / CONTROL_DIRECTORY
    if "core" in selected:
        files.append(
            {
                "kind": "generated",
                "target": str(control / VAULT_MAP_FILE),
                "content": _vault_map_content(mappings),
            }
        )
        instance = control / INSTANCE_FILE
        if not instance.exists():
            files.append(
                {
                    "kind": "generated",
                    "target": str(instance),
                    "content": _instance_content(),
                }
            )
    if "workflows" in selected:
        namespace = vault / "地方議員AI運用OS"
        files.append(
            {
                "kind": "generated",
                "target": str(namespace / "運用MOC.md"),
                "content": _operations_moc_content(repo_root),
            }
        )
        for directory in ("principles", "workflows"):
            for source in sorted((repo_root / directory).glob("*.md")):
                files.append(
                    {
                        "kind": "copy",
                        "source": str(source),
                        "target": str(namespace / directory / source.name),
                    }
                )
    if "templates" in selected:
        templates_path = mappings.get("templates")
        if templates_path:
            template_root = vault / templates_path / "local-councilor-ai-os"
            for source in sorted((repo_root / "templates").glob("*.md")):
                files.append(
                    {
                        "kind": "copy",
                        "source": str(source),
                        "target": str(template_root / source.name),
                    }
                )
    return files


def _content_for_item(item: dict[str, str]) -> str:
    if item["kind"] == "copy":
        source = Path(item["source"])
        if not source.is_file():
            raise OnboardingError(f"コピー元がありません: {source}")
        return source.read_text(encoding="utf-8")
    return item["content"]


def _target_status(vault: Path, item: dict[str, str]) -> dict[str, Any]:
    target = Path(item["target"])
    resolved_vault = vault.resolve()
    try:
        target.resolve(strict=False).relative_to(resolved_vault)
    except (OSError, ValueError):
        return _status("blocked", "対象がVault外へ解決される")

    current = target
    while current != vault and current != current.parent:
        if current.exists() and current.is_symlink():
            return _status("blocked", f"symlinkを含む対象は扱わない: {current}")
        current = current.parent

    expected = _content_for_item(item)
    expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    if not target.exists():
        return _status("add", "新規作成", expected_sha256=expected_hash)
    if target.is_symlink() or not target.is_file():
        return _status("blocked", "既存対象が通常ファイルではない")
    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return _status("blocked", f"既存ファイルを比較できない: {error}")
    existing_hash = hashlib.sha256(existing.encode("utf-8")).hexdigest()
    if existing_hash == expected_hash:
        return _status(
            "reuse",
            "同一内容の既存ファイルを再利用",
            expected_sha256=expected_hash,
            existing_sha256=existing_hash,
        )
    return _status(
        "conflict",
        "既存内容が異なるため自動統合・上書きしない",
        expected_sha256=expected_hash,
        existing_sha256=existing_hash,
    )


def build_plan(
    diagnosis: dict[str, Any],
    *,
    mode: str | None = None,
    layout: str | None = None,
    features: Iterable[str] | None = None,
    role_mappings: dict[str, str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, non-writing scaffold and permission preview."""

    selected_mode = mode or str(diagnosis["recommended_mode"])
    selected_layout = layout or str(
        diagnosis.get("recommended_layout") or "scaffold"
    )
    if selected_layout not in LAYOUTS:
        raise OnboardingError(f"未対応のVault layoutです: {selected_layout}")
    selected_features = (
        ("core",)
        if selected_mode == "integrate"
        and selected_layout == "preserve"
        and features is None
        else features
    )
    selected = _selected_features(selected_mode, selected_features)
    vault = Path(str(diagnosis["vault_path"]))
    root = (
        Path(repo_root).expanduser().resolve()
        if repo_root
        else Path(__file__).resolve().parents[1]
    )
    normalized_mappings: dict[str, str] = {}
    if selected_mode == "integrate" and selected_layout == "preserve":
        detected = diagnosis.get("role_candidates")
        proposed_mappings = role_mappings or _default_role_mappings(
            detected if isinstance(detected, dict) else {}
        )
        normalized_mappings = _validate_preserve_mapping(
            vault,
            proposed_mappings,
        )
        planned_files = _planned_preserve_files(
            vault,
            root,
            selected,
            normalized_mappings,
        )
    elif selected_mode == "integrate":
        planned_files = _planned_files(vault, root, selected)
    else:
        planned_files = []
    actions = []
    for item in planned_files:
        action = dict(item)
        action.update(_target_status(vault, item))
        action.pop("content", None)
        actions.append(action)

    blockers: list[str] = []
    capabilities = diagnosis["capabilities"]
    if capabilities["ai_client_selection"]["status"] != "reuse":
        blockers.append("今回使用するClaude CodeまたはCodexを選択・確認できていない")
    if selected_mode == "full":
        blockers.append(
            "先にclaude-obsidian-setupでObsidianとAI協働基盤を完了し、integrateで再開"
        )
    if selected_mode == "integrate":
        if capabilities["vault_directory"]["status"] != "reuse":
            blockers.append("対象Vaultディレクトリが存在しない")
        if capabilities["vault_registered"]["status"] != "reuse":
            blockers.append("対象フォルダがObsidian Vaultとして開かれていない")
        if capabilities["instruction_file"]["status"] != "reuse":
            blockers.append("Vaultで有効なAI指示ファイルを確認できない")
        if capabilities["obsidian_cli"]["status"] != "reuse":
            blockers.append("Obsidian CLIの対象Vault絶対パス・検索疎通を確認できない")
        blockers.extend(
            f"既存ファイルの安全条件を満たさない: {item['target']}"
            for item in actions
            if item["status"] in {"conflict", "blocked"}
        )
        if (
            selected_layout == "preserve"
            and "templates" in selected
            and "templates" not in normalized_mappings
        ):
            blockers.append(
                "preserve layoutでtemplatesを選ぶ場合はtemplates役割の既存pathが必要"
            )
        instance = vault / CONTROL_DIRECTORY / INSTANCE_FILE
        if selected_layout == "preserve" and instance.exists():
            try:
                instance_value = json.loads(instance.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                blockers.append(f"既存instance.jsonを検証できない: {error}")
            else:
                if not isinstance(instance_value, dict):
                    blockers.append("既存instance.jsonのトップレベルがobjectではない")
                elif instance_value.get("product") not in {
                    None,
                    "local-councilor-ai-os",
                }:
                    blockers.append("既存instance.jsonのproductが一致しない")
                elif str(instance_value.get("schema_version", "1")) != "1":
                    blockers.append("既存instance.jsonのschemaが未対応")

    network = sorted(
        {
            host
            for feature in selected
            for host in NETWORK_BY_FEATURE.get(feature, ())
        }
    )
    diagnosis_seed = {
        "vault_path": diagnosis["vault_path"],
        "agent": diagnosis["agent"],
        "recommended_layout": diagnosis.get("recommended_layout"),
        "role_candidates": diagnosis.get("role_candidates", {}),
        "capability_statuses": {
            key: value["status"]
            for key, value in diagnosis["capabilities"].items()
        },
        "command_statuses": {
            key: value["status"] for key, value in diagnosis["commands"].items()
        },
        "credential_statuses": {
            key: value["status"] for key, value in diagnosis["credentials"].items()
        },
    }
    diagnosis_digest = hashlib.sha256(
        json.dumps(diagnosis_seed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    plan_seed = {
        "schema_version": SCHEMA_VERSION,
        "vault_path": str(vault),
        "agent": diagnosis["agent"],
        "mode": selected_mode,
        "layout": selected_layout,
        "features": selected,
        "role_mappings": normalized_mappings,
        "actions": actions,
        "network": network,
        "blockers": blockers,
        "diagnosis_sha256": diagnosis_digest,
    }
    plan_sha256 = hashlib.sha256(
        json.dumps(plan_seed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if selected_mode == "full":
        plan_status = "handoff_required"
    elif blockers:
        plan_status = "blocked"
    else:
        plan_status = "ready"
    return {
        **plan_seed,
        "status": plan_status,
        "plan_sha256": plan_sha256,
        "generated_at": _utc_now(),
        "read_only_preview": True,
        "approval_preview": {
            "normal_scoped_actions": [
                (
                    "既存Vault役割対応とcontrol fileの新規作成"
                    if selected_layout == "preserve"
                    else "不足する棚・MOC・profile・テンプレートの新規作成"
                ),
                "Vault内への実行記録の新規作成",
            ],
            "separate_confirmation": [
                "既存ファイルの統合・上書き",
                "グローバル設定・Skill・パッケージの変更",
                "外部送信・公開",
                "削除・巻き戻し",
            ],
            "ai_client_permissions": [
                "対象Vaultがwritable roots内か確認",
                "Obsidianアプリ起動とCLI実行の承認方式を確認",
                *([f"外部通信: {host}" for host in network] if network else []),
            ],
        },
        "_planned_files": planned_files,
        "_repo_root": str(root),
        "_role_mappings": normalized_mappings,
    }


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def _source_revision(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else None


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_new_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise OnboardingError(f"計画後に対象が作成されたため停止: {path}") from error


def apply_scaffold(
    plan: dict[str, Any],
    *,
    accepted_plan_sha256: str,
) -> dict[str, Any]:
    """Revalidate a confirmed plan, create only new files, and record a manifest."""

    if plan["mode"] != "integrate":
        raise OnboardingError("scaffoldの適用はintegrateモードだけで実行できます")
    if plan["status"] != "ready" or plan["blockers"]:
        raise OnboardingError("適用前の停止条件: " + " / ".join(plan["blockers"]))
    if accepted_plan_sha256 != plan["plan_sha256"]:
        raise OnboardingError("確認した計画ハッシュが現在の計画と一致しません")

    vault = Path(str(plan["vault_path"]))
    diagnosis = diagnose_environment(
        vault,
        agent=str(plan["agent"]),
        probe_obsidian=True,
    )
    current_plan = build_plan(
        diagnosis,
        mode=str(plan["mode"]),
        layout=str(plan["layout"]),
        features=tuple(plan["features"]),
        role_mappings=dict(plan.get("_role_mappings") or {}),
        repo_root=str(plan["_repo_root"]),
    )
    if current_plan["status"] != "ready":
        raise OnboardingError(
            "実行直前の再診断で停止条件を検出: "
            + " / ".join(current_plan["blockers"])
        )
    if current_plan["plan_sha256"] != accepted_plan_sha256:
        raise OnboardingError(
            "診断またはファイル状態が計画確認後に変化しました。planを再確認してください"
        )

    started_at = _utc_now()
    run_id = f"{_utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
    run_dir = vault / ".local-councilor-ai-os" / "runs"
    manifest_path = run_dir / f"{run_id}.json"
    manifest_target = _target_status(
        vault,
        {
            "kind": "generated",
            "target": str(manifest_path),
            "content": "",
        },
    )
    if manifest_target["status"] != "add":
        raise OnboardingError(
            "manifestの保存先が安全条件を満たしません: "
            f"{manifest_path} ({manifest_target['status']})"
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": "local-councilor-ai-os",
        "source_revision": _source_revision(Path(str(plan["_repo_root"]))),
        "run_id": run_id,
        "run_type": "onboarding",
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "requested_mode": plan["mode"],
        "requested_layout": plan["layout"],
        "selected_features": plan["features"],
        "plan_sha256": accepted_plan_sha256,
        "plan_sha256_confirmed": True,
        "target": {
            "vault_path": str(vault),
            "agent": plan["agent"],
            "layout": plan["layout"],
            "obsidian_vault_name": diagnosis["capabilities"]["obsidian_cli"].get(
                "vault_name"
            ),
        },
        "permission_preview": plan["approval_preview"],
        "artifacts": [],
        "checks": [
            {
                "name": "obsidian_cli_target",
                "status": diagnosis["capabilities"]["obsidian_cli"]["status"],
            },
            {"name": "plan_revalidated", "status": "passed"},
        ],
        "skips": [],
        "failures": [],
    }
    _atomic_write_json(manifest_path, manifest)

    try:
        for item, action in zip(
            current_plan["_planned_files"],
            current_plan["actions"],
            strict=True,
        ):
            target = Path(item["target"])
            content = _content_for_item(item)
            expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if action["status"] == "reuse":
                manifest["skips"].append(
                    {
                        "path": str(target),
                        "reason": "same_content",
                    }
                )
                manifest["artifacts"].append(
                    {
                        "path": str(target),
                        "action": "reuse",
                        "sha256": expected_hash,
                    }
                )
                _atomic_write_json(manifest_path, manifest)
                continue
            if action["status"] != "add":
                raise OnboardingError(
                    f"安全条件を満たさない対象を検出: {target} ({action['status']})"
                )
            latest = _target_status(vault, item)
            if latest["status"] != "add":
                raise OnboardingError(
                    f"計画後に対象状態が変化: {target} ({latest['status']})"
                )
            _write_new_text(target, content)
            manifest["artifacts"].append(
                {
                    "path": str(target),
                    "action": "create",
                    "sha256": expected_hash,
                }
            )
            _atomic_write_json(manifest_path, manifest)
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at"] = _utc_now()
        manifest["failures"].append({"message": str(error)})
        _atomic_write_json(manifest_path, manifest)
        if isinstance(error, OnboardingError):
            raise
        raise OnboardingError(f"scaffold作成中に失敗: {error}") from error

    manifest["status"] = "incomplete"
    manifest["scaffold_status"] = "complete"
    manifest["profile_status"] = "incomplete"
    manifest["finished_at"] = _utc_now()
    manifest["checks"].append(
        {
            "name": "human_profile_confirmation",
            "status": "incomplete",
            "detail": "議員プロファイルと議会アダプターは人の確認が必要",
        }
    )
    _atomic_write_json(manifest_path, manifest)
    created = [
        item["path"]
        for item in manifest["artifacts"]
        if item["action"] == "create"
    ]
    reused = [
        item["path"]
        for item in manifest["artifacts"]
        if item["action"] == "reuse"
    ]
    return {
        "status": "incomplete",
        "scaffold_status": "complete",
        "profile_status": "incomplete",
        "run_id": run_id,
        "manifest": str(manifest_path),
        "created": created,
        "reused": reused,
    }


def verify_scaffold(
    manifest_path: str | os.PathLike[str],
    *,
    probe_obsidian: bool = True,
) -> dict[str, Any]:
    """Verify manifest artifacts and the target Vault without modifying them."""

    path = Path(manifest_path).expanduser().resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OnboardingError(f"manifestを読み取れません: {error}") from error
    vault = Path(str(manifest.get("target", {}).get("vault_path", ""))).resolve()
    if not vault.is_dir() or not (vault / ".obsidian").is_dir():
        raise OnboardingError("manifestの対象Vaultが存在しないか、Vaultではありません")
    agent = str(manifest.get("target", {}).get("agent", "codex"))
    diagnosis = diagnose_environment(
        vault,
        agent=agent,
        probe_obsidian=probe_obsidian,
    )
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    cli_status = diagnosis["capabilities"]["obsidian_cli"]["status"]
    checks.append({"name": "obsidian_cli_target", "status": cli_status})
    if cli_status != "reuse":
        failures.append("Obsidian CLIの対象Vault確認に失敗")

    artifacts = manifest.get("artifacts", [])
    for artifact in artifacts:
        artifact_path = Path(str(artifact.get("path", "")))
        try:
            artifact_path.resolve(strict=False).relative_to(vault)
        except (OSError, ValueError):
            failures.append(f"artifactがVault外を指している: {artifact_path}")
            continue
        expected_hash = str(artifact.get("sha256", ""))
        if not artifact_path.is_file():
            failures.append(f"artifactが存在しない: {artifact_path}")
            continue
        try:
            content = artifact_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            failures.append(f"artifactを読めない: {artifact_path}: {error}")
            continue
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            failures.append(f"artifactのhashが不一致: {artifact_path}")
        if artifact_path.suffix == ".md":
            frontmatter = content.split("---", 2)
            if len(frontmatter) < 3 or "description:" not in frontmatter[1]:
                failures.append(f"description frontmatterがない: {artifact_path}")

    layout = str(
        manifest.get("requested_layout")
        or manifest.get("target", {}).get("layout")
        or "scaffold"
    )
    if layout == "preserve":
        vault_map = vault / CONTROL_DIRECTORY / VAULT_MAP_FILE
        try:
            map_value = _parse_vault_map(vault_map.read_text(encoding="utf-8"))
            _validate_preserve_mapping(vault, dict(map_value["roles"]))
        except (OSError, UnicodeError, OnboardingError) as error:
            failures.append(f"既存Vault役割対応を検証できない: {error}")
    elif layout == "scaffold":
        shelf_paths = [
            vault / directory / filename for directory, filename, _, _ in SHELVES
        ]
        if sum(item.is_file() for item in shelf_paths) != len(SHELVES):
            failures.append("8つの業務棚MOCが揃っていない")
        root_moc = vault / ROOT_MOC
        if root_moc.is_file():
            root_content = root_moc.read_text(encoding="utf-8")
            for directory, filename, _, _ in SHELVES:
                expected_link = f"[[{directory}/{filename.removesuffix('.md')}]]"
                if expected_link not in root_content:
                    failures.append(f"OS MOCにリンクがない: {expected_link}")
        else:
            failures.append(f"OS MOCが存在しない: {root_moc}")
    else:
        failures.append(f"manifestのVault layoutが未対応: {layout}")

    checks.append(
        {
            "name": "artifact_integrity",
            "status": "passed" if not failures else "failed",
            "artifact_count": len(artifacts),
            "layout": layout,
        }
    )
    return {
        "status": "failed" if failures else "incomplete",
        "scaffold_status": "failed" if failures else "verified",
        "profile_status": "incomplete",
        "manifest": str(path),
        "checks": checks,
        "failures": failures,
    }


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a serializable plan without internal file contents."""

    return _public_plan(plan)
