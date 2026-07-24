"""Shared helpers for module build and verification manifests."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .database import sqlite_read_only_uri
from .run_manifest import (
    artifact_record,
    finish_run,
    redact_text,
    start_run,
)


def _redact_requested(value: Any, *, secret_values: tuple[str, ...]) -> Any:
    """Return a JSON-safe requested value with known secrets removed."""

    if isinstance(value, dict):
        return {
            str(key): _redact_requested(item, secret_values=secret_values)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_requested(item, secret_values=secret_values)
            for item in value
        ]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return redact_text(value, secret_values=secret_values)
    return value


def begin_module_run(
    manifest_directory: str | Path | None,
    *,
    run_type: str,
    repo_root: Path,
    requested: dict[str, Any],
    run_id: str | None = None,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Start a module run only when a manifest directory was requested."""

    if manifest_directory is None:
        return None, None
    secret_values = (os.environ.get("ESTAT_APPID", ""),)
    return start_run(
        manifest_directory,
        run_type=run_type,
        repo_root=repo_root,
        requested=_redact_requested(
            requested,
            secret_values=secret_values,
        ),
        run_id=run_id,
    )


def input_file_record(path: str | Path, *, kind: str) -> dict[str, Any]:
    """Return a hash-pinned input record using the common artifact shape."""

    return artifact_record(path, kind=kind)


def sqlite_integrity(path: str | Path) -> str:
    """Run SQLite integrity_check without permitting writes."""

    database = Path(path).expanduser().resolve(strict=False)
    with sqlite3.connect(sqlite_read_only_uri(database), uri=True) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def finish_database_run(
    manifest_path: Path | None,
    manifest: dict[str, Any] | None,
    *,
    database: str | Path,
    artifact_kind: str,
    scope: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    inputs: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    schema_version: str | int | None = 1,
) -> None:
    """Finalize a successful database-producing module run."""

    if manifest_path is None or manifest is None:
        return
    integrity = sqlite_integrity(database)
    all_checks = [
        {
            "name": "sqlite_integrity",
            "status": "passed" if integrity == "ok" else "failed",
            "detail": integrity,
        },
        *(checks or []),
    ]
    finish_run(
        manifest_path,
        manifest,
        status=(
            "succeeded"
            if all(item.get("status") == "passed" for item in all_checks)
            else "failed"
        ),
        updates={
            "target": {"database": str(Path(database).resolve(strict=False))},
            "scope": scope or {},
            "coverage": coverage or {},
            "inputs": inputs or [],
            "outputs": [
                artifact_record(
                    database,
                    kind=artifact_kind,
                    schema_version=schema_version,
                )
            ],
            "checks": all_checks,
        },
    )


def finish_dry_run(
    manifest_path: Path | None,
    manifest: dict[str, Any] | None,
    *,
    scope: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
) -> None:
    """Finalize a read-only discovery run with no generated database."""

    if manifest_path is None or manifest is None:
        return
    finish_run(
        manifest_path,
        manifest,
        status="succeeded",
        updates={
            "scope": scope or {},
            "coverage": coverage or {},
            "outputs": [],
            "checks": [
                {
                    "name": "dry_run_no_database",
                    "status": "passed",
                    "detail": "候補確認のみ。本文・DBを生成していない",
                }
            ],
        },
    )


def finish_verification_run(
    manifest_path: Path | None,
    manifest: dict[str, Any] | None,
    *,
    database: str | Path,
    artifact_kind: str,
    verification_name: str,
    exit_code: int,
    coverage: dict[str, Any] | None = None,
) -> None:
    """Record a module verification, including failed reconciliation."""

    if manifest_path is None or manifest is None:
        return
    integrity = sqlite_integrity(database)
    passed = exit_code == 0 and integrity == "ok"
    checks = [
        {
            "name": "sqlite_integrity",
            "status": "passed" if integrity == "ok" else "failed",
            "detail": integrity,
        },
        {
            "name": verification_name,
            "status": "passed" if exit_code == 0 else "failed",
            "detail": f"exit_code={exit_code}",
        },
    ]
    finish_run(
        manifest_path,
        manifest,
        status="succeeded" if passed else "failed",
        updates={
            "target": {"database": str(Path(database).resolve(strict=False))},
            "scope": {"action": "verify"},
            "coverage": coverage or {},
            "outputs": [
                artifact_record(
                    database,
                    kind=artifact_kind,
                    schema_version=1,
                )
            ],
            "checks": checks,
        },
    )


def fail_module_run(
    manifest_path: Path | None,
    manifest: dict[str, Any] | None,
    error: Exception | str,
) -> None:
    """Finalize a failed module run without exposing known secrets."""

    if manifest_path is None or manifest is None:
        return
    message = redact_text(
        str(error),
        secret_values=(os.environ.get("ESTAT_APPID", ""),),
    )
    manifest["failures"].append(
        {
            "code": type(error).__name__ if isinstance(error, Exception) else "error",
            "message": message,
        }
    )
    finish_run(manifest_path, manifest, status="failed")
