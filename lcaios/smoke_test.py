"""End-to-end smoke tests for live and offline bootstrap reproducibility."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

from .database import verify_bootstrap_database
from .run_manifest import redact_text, utc_now

REPO_ROOT = Path(__file__).resolve().parents[1]


class SmokeTestError(RuntimeError):
    """Raised when the smoke-test harness cannot complete."""


def _run_json_command(command: Sequence[str]) -> dict[str, Any]:
    result = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    secret = os.environ.get("ESTAT_APPID", "")
    output = result.stdout.strip()
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError as error:
        safe_stderr = redact_text(result.stderr.strip(), secret_values=(secret,))
        raise SmokeTestError(
            f"JSON出力を読み取れません（exit={result.returncode}）: "
            f"{safe_stderr or 'stdout is not JSON'}"
        ) from error
    if result.returncode != 0:
        safe_error = redact_text(
            str(payload.get("error") or result.stderr.strip() or "unknown error"),
            secret_values=(secret,),
        )
        raise SmokeTestError(
            f"bootstrapが失敗しました（exit={result.returncode}）: {safe_error}"
        )
    return payload


def _indicator_rows(path: Path) -> tuple[tuple[Any, ...], ...]:
    with closing(sqlite3.connect(path)) as connection:
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(indicator)")
            if str(row[1]) != "id"
        ]
        if not columns:
            raise SmokeTestError(f"indicator tableを確認できません: {path}")
        quoted = ", ".join(f'"{column}"' for column in columns)
        rows = connection.execute(f"SELECT {quoted} FROM indicator").fetchall()
    return tuple(
        sorted(
            (tuple(row) for row in rows),
            key=lambda row: json.dumps(row, ensure_ascii=False, default=str),
        )
    )


def _authority_semantic_content(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(
        line for line in lines if not line.startswith("generated_at:")
    ).rstrip() + "\n"


def _secret_scan(root: Path) -> dict[str, Any]:
    secret = os.environ.get("ESTAT_APPID", "").encode()
    if not secret:
        return {"status": "skipped", "reason": "ESTAT_APPID is missing"}
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and secret in path.read_bytes():
            hits.append(str(path))
    return {
        "status": "passed" if not hits else "failed",
        "files_scanned": sum(1 for path in root.rglob("*") if path.is_file()),
        "hit_count": len(hits),
    }


def run_bootstrap_smoke_test(
    municipality_name: str,
    *,
    prefecture: str | None = None,
    work_dir: str | Path | None = None,
    refresh: bool = False,
    cross_check: bool = True,
    max_live_requests: int = 40,
) -> dict[str, Any]:
    """Build online/offline artifacts and compare their semantic contents."""

    root = (
        Path(work_dir).expanduser().resolve(strict=False)
        if work_dir is not None
        else Path(tempfile.mkdtemp(prefix="lcaios-bootstrap-smoke-"))
    )
    if root.exists() and any(root.iterdir()):
        raise SmokeTestError(
            f"work directoryは空である必要があります: {root}"
        )
    root.mkdir(parents=True, exist_ok=True)
    cache = root / "cache"
    online_output = root / "online"
    offline_output = root / "offline"
    manifest_dir = (
        root
        / "vault"
        / ".local-councilor-ai-os"
        / "runs"
        / "bootstrap"
    )
    online_command = [
        sys.executable,
        "-m",
        "bootstrap.cli",
        municipality_name,
        "--out-dir",
        str(online_output),
        "--cache-dir",
        str(cache),
        "--manifest-dir",
        str(manifest_dir),
    ]
    if prefecture:
        online_command.extend(["--prefecture", prefecture])
    if cross_check:
        online_command.append("--cross-check")
    if refresh:
        online_command.append("--refresh")
    online = _run_json_command(online_command)

    offline_command = [
        sys.executable,
        "-m",
        "bootstrap.cli",
        municipality_name,
        "--out-dir",
        str(offline_output),
        "--cache-dir",
        str(cache),
        "--offline",
    ]
    if prefecture:
        offline_command.extend(["--prefecture", prefecture])
    if cross_check:
        offline_command.append("--cross-check")
    offline = _run_json_command(offline_command)

    online_database = Path(str(online["database"]["path"]))
    offline_database = Path(str(offline["database"]["path"]))
    online_authority = Path(str(online["authority_map"]["path"]))
    offline_authority = Path(str(offline["authority_map"]["path"]))
    database_verification = verify_bootstrap_database(online_database)
    indicator_match = _indicator_rows(online_database) == _indicator_rows(
        offline_database
    )
    authority_match = _authority_semantic_content(
        online_authority
    ) == _authority_semantic_content(offline_authority)
    secret_scan = _secret_scan(root)
    manifest_path = Path(str(online.get("manifest") or ""))
    manifest_ok = False
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_ok = manifest.get("status") == "succeeded"

    checks: list[dict[str, Any]] = [
        {
            "name": "online_bootstrap",
            "status": "passed" if online.get("status") == "ok" else "failed",
            "detail": online.get("status"),
        },
        {
            "name": "offline_bootstrap",
            "status": "passed" if offline.get("status") == "ok" else "failed",
            "detail": f"live_request_count={offline.get('live_request_count')}",
        },
        {
            "name": "live_request_limit",
            "status": (
                "passed"
                if int(online.get("live_request_count") or 0)
                <= max_live_requests
                else "failed"
            ),
            "detail": (
                f"{online.get('live_request_count', 0)}"
                f" <= {max_live_requests}"
            ),
        },
        {
            "name": "indicator_semantic_match",
            "status": "passed" if indicator_match else "failed",
            "detail": f"rows={online['database']['indicator_rows']}",
        },
        {
            "name": "authority_map_semantic_match",
            "status": "passed" if authority_match else "failed",
            "detail": "generated_atを比較対象から除外",
        },
        {
            "name": "database_verification",
            "status": "passed" if database_verification["ok"] else "failed",
            "detail": database_verification["schema"].get("state"),
        },
        {
            "name": "manifest",
            "status": "passed" if manifest_ok else "failed",
            "detail": str(manifest_path),
        },
        {
            "name": "secret_scan",
            "status": secret_scan["status"],
            "detail": (
                secret_scan.get("reason")
                or f"hit_count={secret_scan.get('hit_count', 0)}"
            ),
        },
    ]
    ok = all(item["status"] in {"passed", "skipped"} for item in checks)
    return {
        "schema_version": 1,
        "product": "local-councilor-ai-os",
        "test_type": "bootstrap_smoke_test",
        "generated_at": utc_now(),
        "status": "passed" if ok else "failed",
        "work_directory": str(root),
        "municipality": online["municipality"],
        "max_live_requests": max_live_requests,
        "online": {
            "database": str(online_database),
            "authority_map": str(online_authority),
            "indicator_rows": online["database"]["indicator_rows"],
            "retrieval": online.get("retrieval", {}),
            "warnings": online.get("warnings", []),
        },
        "offline": {
            "database": str(offline_database),
            "authority_map": str(offline_authority),
            "indicator_rows": offline["database"]["indicator_rows"],
            "live_request_count": offline.get("live_request_count"),
            "retrieval": offline.get("retrieval", {}),
            "warnings": offline.get("warnings", []),
        },
        "checks": checks,
        "secret_scan": secret_scan,
        "database_verification": database_verification,
    }
