"""Safe backup, recovery, and generated-artifact inventory."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .database import sqlite_read_only_uri, verify_bootstrap_database
from .run_manifest import utc_now


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 of one file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            sqlite_read_only_uri(source), uri=True
        )
        source_connection.execute("PRAGMA query_only = ON")
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def backup_database(
    source: str | Path,
    output_directory: str | Path,
) -> dict[str, Any]:
    """Create and verify a non-overwriting SQLite backup."""

    source_input = Path(source).expanduser()
    if source_input.is_symlink():
        raise ValueError("source databaseがsymlinkです")
    source_path = source_input.resolve(strict=False)
    verification = verify_bootstrap_database(source_path)
    if not verification["ok"]:
        raise ValueError("source databaseの検証に失敗しました")

    output = Path(output_directory).expanduser().resolve(strict=False)
    output.mkdir(parents=True, exist_ok=True)
    source_hash = file_sha256(source_path)
    timestamp = utc_now().replace(":", "").replace("-", "")
    destination = output / (
        f"{source_path.stem}-backup-{timestamp}-{source_hash[:8]}.db"
    )
    if destination.exists():
        raise ValueError(f"backup先が既に存在します: {destination}")
    temporary = output / f".{destination.name}.new-{uuid.uuid4().hex}"
    try:
        _sqlite_backup(source_path, temporary)
        backup_verification = verify_bootstrap_database(temporary)
        if not backup_verification["ok"]:
            raise ValueError("作成したbackupの検証に失敗しました")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "created",
        "source": str(source_path),
        "source_sha256": source_hash,
        "backup": str(destination),
        "backup_sha256": file_sha256(destination),
        "created_at": utc_now(),
        "verification": backup_verification,
    }


def restore_database(
    backup: str | Path,
    target: str | Path,
    *,
    accepted_sha256: str,
) -> dict[str, Any]:
    """Restore a verified backup and preserve any existing target."""

    backup_input = Path(backup).expanduser()
    target_input = Path(target).expanduser()
    if backup_input.is_symlink() or target_input.is_symlink():
        raise ValueError("symlinkのbackupまたはtargetは復旧対象にできません")
    backup_path = backup_input.resolve(strict=False)
    target_path = target_input.resolve(strict=False)
    if backup_path == target_path:
        raise ValueError("backupとtargetが同じpathです")
    actual_hash = file_sha256(backup_path)
    if actual_hash != accepted_sha256:
        raise ValueError("確認済みbackup SHA-256と一致しません")
    backup_verification = verify_bootstrap_database(backup_path)
    if not backup_verification["ok"]:
        raise ValueError("backup databaseの検証に失敗しました")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(
        f".{target_path.name}.restore-{uuid.uuid4().hex}"
    )
    previous: Path | None = None
    try:
        _sqlite_backup(backup_path, temporary)
        restored_verification = verify_bootstrap_database(temporary)
        if not restored_verification["ok"]:
            raise ValueError("復旧用一時databaseの検証に失敗しました")
        if target_path.exists():
            previous_hash = file_sha256(target_path)
            timestamp = utc_now().replace(":", "").replace("-", "")
            previous = target_path.with_name(
                f"{target_path.stem}.previous-{timestamp}-{previous_hash[:8]}"
                f"{target_path.suffix or '.db'}"
            )
            if previous.exists():
                raise ValueError(f"旧databaseの退避先が既に存在します: {previous}")
            os.replace(target_path, previous)
        try:
            os.replace(temporary, target_path)
        except Exception:
            if previous is not None and previous.exists() and not target_path.exists():
                os.replace(previous, target_path)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "restored",
        "backup": str(backup_path),
        "backup_sha256": actual_hash,
        "target": str(target_path),
        "target_sha256": file_sha256(target_path),
        "previous": str(previous) if previous is not None else None,
        "restored_at": utc_now(),
        "verification": restored_verification,
    }


def generated_artifacts(vault: str | Path) -> dict[str, Any]:
    """List manifest-declared artifacts without deleting anything."""

    vault_path = Path(vault).expanduser().resolve(strict=False)
    runs = vault_path / ".local-councilor-ai-os" / "runs"
    artifacts: dict[str, dict[str, Any]] = {}
    invalid_manifests: list[str] = []
    if runs.is_dir():
        for manifest_path in sorted(runs.rglob("*.json")):
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                invalid_manifests.append(str(manifest_path))
                continue
            if not isinstance(manifest, dict):
                invalid_manifests.append(str(manifest_path))
                continue
            if manifest.get("product") != "local-councilor-ai-os":
                continue
            entries: list[Any] = []
            for key in ("artifacts", "outputs"):
                value = manifest.get(key)
                if isinstance(value, list):
                    entries.extend(value)
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("path"):
                    continue
                path = Path(str(entry["path"])).expanduser()
                if not path.is_absolute():
                    path = vault_path / path
                resolved = path.resolve(strict=False)
                try:
                    resolved.relative_to(vault_path)
                    location = "vault"
                except ValueError:
                    location = "external"
                artifacts[str(resolved)] = {
                    "path": str(resolved),
                    "kind": entry.get("kind") or "generated",
                    "sha256": entry.get("sha256"),
                    "exists": resolved.is_file(),
                    "location": location,
                    "declared_by": str(manifest_path),
                    "run_id": manifest.get("run_id"),
                    "run_type": manifest.get("run_type") or "onboarding",
                }
    return {
        "schema_version": 1,
        "vault": str(vault_path),
        "artifacts": [
            artifacts[path] for path in sorted(artifacts)
        ],
        "invalid_manifests": sorted(invalid_manifests),
        "read_only": True,
    }
