"""Write append-only run manifests with atomic updates."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
PRODUCT = "local-councilor-ai-os"
SENSITIVE_QUERY_PATTERN = re.compile(
    r"([?&](?:appId|api[_-]?key|access[_-]?token|token)=)[^&\s]+",
    flags=re.IGNORECASE,
)


def utc_now() -> str:
    """Return one UTC timestamp in the repository's canonical format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def source_revision(repo_root: Path) -> str | None:
    """Return the current Git revision without requiring a clean worktree."""

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


def redact_text(value: str, *, secret_values: Iterable[str] = ()) -> str:
    """Remove known secret values and sensitive query values from text."""

    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "REDACTED")
    return SENSITIVE_QUERY_PATTERN.sub(r"\1REDACTED", redacted)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Write JSON beside the destination and replace it atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_record(
    path: str | Path,
    *,
    kind: str,
    schema_version: str | int | None = None,
) -> dict[str, Any]:
    """Describe one existing output artifact."""

    artifact = Path(path).expanduser().resolve(strict=False)
    value: dict[str, Any] = {
        "kind": kind,
        "path": str(artifact),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size_bytes": artifact.stat().st_size,
    }
    if schema_version is not None:
        value["schema_version"] = schema_version
    return value


def start_run(
    manifest_directory: str | Path,
    *,
    run_type: str,
    repo_root: Path,
    requested: dict[str, Any],
    run_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create a running manifest and return its path and mutable value."""

    identifier = run_id or (
        f"{utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
    )
    path = Path(manifest_directory).expanduser().resolve(strict=False) / (
        f"{identifier}.json"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "source_revision": source_revision(repo_root),
        "run_id": identifier,
        "run_type": run_type,
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "producer": {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "sqlite_version": sqlite3.sqlite_version,
        },
        "requested": requested,
        "target": {},
        "scope": {},
        "inputs": [],
        "outputs": [],
        "checks": [],
        "warnings": [],
        "failures": [],
    }
    atomic_write_json(path, manifest)
    return path, manifest


def finish_run(
    path: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    updates: dict[str, Any] | None = None,
) -> None:
    """Finalize a running manifest."""

    if updates:
        manifest.update(updates)
    manifest["status"] = status
    manifest["finished_at"] = utc_now()
    atomic_write_json(path, manifest)

