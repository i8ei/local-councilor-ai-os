"""Confirm human-reviewed local profile files without copying their contents."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .run_manifest import artifact_record, finish_run, start_run


PROFILE_PLACEHOLDER = re.compile(r"<[^>\n]+>")
PROFILE_REQUIRED = (
    re.compile(r"^schema_version:\s*1\s*$", re.MULTILINE),
    re.compile(r"^status:\s*(?:confirmed|ready|complete)\s*$", re.MULTILINE),
    re.compile(r"^council:\s*$", re.MULTILINE),
    re.compile(r"^working_style:\s*$", re.MULTILINE),
    re.compile(r"^privacy:\s*$", re.MULTILINE),
)
MAX_PROFILE_BYTES = 1_000_000


def _vault_file(
    vault: Path,
    value: str | Path,
    *,
    label: str,
    suffixes: tuple[str, ...],
) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = vault / raw
    lexical_candidate = Path(os.path.abspath(raw))
    if lexical_candidate.is_symlink():
        raise ValueError(f"{label}にsymlinkは使用できません: {lexical_candidate}")
    candidate = lexical_candidate.resolve(strict=False)
    vault_absolute = vault.resolve(strict=True)
    try:
        relative = candidate.relative_to(vault_absolute)
    except ValueError as error:
        raise ValueError(f"{label}はVault内に置く必要があります") from error
    current = vault_absolute
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label}にsymlinkは使用できません: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"{label}が存在しません: {candidate}")
    resolved_vault = vault_absolute.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(resolved_vault)
    except ValueError as error:
        raise ValueError(f"{label}がVault外へ解決されます") from error
    if resolved.suffix.lower() not in suffixes:
        expected = " / ".join(suffixes)
        raise ValueError(f"{label}の拡張子は{expected}を使用してください")
    if resolved.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError(f"{label}が大きすぎます")
    return resolved


def _read_profile_text(path: Path, *, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label}はUTF-8で保存してください") from error
    if not text.strip():
        raise ValueError(f"{label}が空です")
    if PROFILE_PLACEHOLDER.search(text) or "執筆予定" in text:
        raise ValueError(f"{label}に未記入placeholderが残っています")
    return text


def _validate_councilor_profile(path: Path) -> list[dict[str, str]]:
    text = _read_profile_text(path, label="councilor profile")
    missing = [
        pattern.pattern
        for pattern in PROFILE_REQUIRED
        if pattern.search(text) is None
    ]
    if missing:
        raise ValueError(
            "councilor profileに必須項目がありません: "
            + ", ".join(missing)
        )
    return [
        {
            "name": "profile_contract",
            "status": "passed",
            "detail": "schema、確認状態、必須sectionを確認",
        }
    ]


def _validate_council_adapter(path: Path) -> list[dict[str, str]]:
    text = _read_profile_text(path, label="council adapter")
    heading_1 = re.findall(r"^#\s+\S.+$", text, flags=re.MULTILINE)
    heading_2 = re.findall(r"^##\s+\S.+$", text, flags=re.MULTILINE)
    if not heading_1 or len(heading_2) < 2:
        raise ValueError(
            "council adapterには見出し1件と確認済みsection 2件以上が必要です"
        )
    if len(text.strip()) < 100:
        raise ValueError("council adapterの確認内容が短すぎます")
    return [
        {
            "name": "council_adapter_contract",
            "status": "passed",
            "detail": "placeholder除去と文書構造を確認",
        }
    ]


def confirm_profile(
    vault: str | Path,
    *,
    profile: str | Path,
    council_adapter: str | Path,
    human_reviewed: bool,
) -> dict[str, Any]:
    """Validate local profile files and append a hash-only confirmation run."""

    if not human_reviewed:
        raise ValueError("--confirm-human-reviewedが必要です")
    vault_path = Path(vault).expanduser().resolve(strict=True)
    if not vault_path.is_dir() or not (vault_path / ".obsidian").is_dir():
        raise ValueError("対象は登録済みObsidian Vaultではありません")
    profile_path = _vault_file(
        vault_path,
        profile,
        label="councilor profile",
        suffixes=(".yaml", ".yml"),
    )
    adapter_path = _vault_file(
        vault_path,
        council_adapter,
        label="council adapter",
        suffixes=(".md",),
    )
    checks = [
        *_validate_councilor_profile(profile_path),
        *_validate_council_adapter(adapter_path),
        {
            "name": "human_profile_confirmation",
            "status": "passed",
            "detail": "本人が内容と公開・内部情報の分離を確認",
        },
    ]
    repo_root = Path(__file__).resolve().parents[1]
    manifest_path, manifest = start_run(
        vault_path / ".local-councilor-ai-os" / "runs" / "profile",
        run_type="profile",
        repo_root=repo_root,
        requested={
            "profile": str(profile_path),
            "council_adapter": str(adapter_path),
            "human_reviewed": True,
        },
    )
    inputs = [
        artifact_record(profile_path, kind="councilor_profile"),
        artifact_record(adapter_path, kind="council_adapter"),
    ]
    finish_run(
        manifest_path,
        manifest,
        status="succeeded",
        updates={
            "target": {"vault_path": str(vault_path)},
            "inputs": inputs,
            "checks": checks,
        },
    )
    return {
        "status": "confirmed",
        "vault": str(vault_path),
        "profile": str(profile_path),
        "council_adapter": str(adapter_path),
        "manifest": str(manifest_path),
        "checks": checks,
    }
