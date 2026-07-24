"""Derive OS readiness from existing artifacts without modifying them."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .database import evaluate_schema_compatibility, sqlite_read_only_uri
from .freshness import evaluate_bootstrap_freshness


SCHEMA_VERSION = 1
PRODUCT = "local-councilor-ai-os"
CONTROL_DIRECTORY = ".local-councilor-ai-os"
INSTANCE_FILE = "instance.json"
MODULE_TYPES = (
    "minutes",
    "regulations",
    "benchmark",
    "budget",
    "settlement",
)
MODULE_ARTIFACT_KINDS = {
    "minutes": "minutes_database",
    "regulations": "regulations_database",
    "benchmark": "benchmark_database",
    "budget": "budget_database",
    "settlement": "settlement_database",
}
MODULE_REQUIRED_CHECKS = {
    "minutes": "meeting_rows",
    "regulations": "document_rows",
    "benchmark": "municipality_rows",
    "budget": "budget_reconciliation",
    "settlement": "settlement_reconciliation",
}
REQUIREMENTS = frozenset(
    {
        "foundation_ready",
        "scaffold_ready",
        "profile_ready",
        "tier1_data_ready",
        *(f"module_ready:{name}" for name in MODULE_TYPES),
    }
)
READY_STATES = frozenset({"ready"})
ACCEPTED_VERIFICATION_STATES = frozenset(
    {"verified", "reconciled", "verified_source_extraction"}
)
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _warning(
    severity: str,
    code: str,
    component: str,
    message: str,
    path: str | Path | None = None,
) -> dict[str, str]:
    value = {
        "severity": severity,
        "code": code,
        "component": component,
        "message": message,
    }
    if path is not None:
        value["path"] = str(path)
    return value


def _ordered_warnings(
    warnings: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for item in warnings:
        key = (
            item.get("severity", ""),
            item.get("component", ""),
            item.get("code", ""),
            item.get("path", ""),
            item.get("message", ""),
        )
        unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (
            SEVERITY_ORDER.get(item.get("severity", ""), 99),
            item.get("component", ""),
            item.get("code", ""),
            item.get("path", ""),
            item.get("message", ""),
        ),
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(value: str | Path, *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def _load_instance(vault: Path) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    path = vault / CONTROL_DIRECTORY / INSTANCE_FILE
    if not path.exists():
        return None, []
    warnings: list[dict[str, str]] = []
    try:
        value = _read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        warnings.append(
            _warning(
                "error",
                "instance_invalid_json",
                "instance",
                f"instance.jsonを読み取れません: {error}",
                path,
            )
        )
        return None, warnings
    if not isinstance(value, dict):
        warnings.append(
            _warning(
                "error",
                "instance_invalid_type",
                "instance",
                "instance.jsonのトップレベルはobjectである必要があります",
                path,
            )
        )
        return None, warnings
    valid = True
    if str(value.get("product", PRODUCT)) != PRODUCT:
        valid = False
        warnings.append(
            _warning(
                "error",
                "instance_product_mismatch",
                "instance",
                "instance.jsonのproductが一致しません",
                path,
            )
        )
    if str(value.get("schema_version", "1")) != "1":
        valid = False
        warnings.append(
            _warning(
                "error",
                "instance_schema_unsupported",
                "instance",
                "未対応のinstance schemaです",
                path,
            )
        )
    return (value if valid else None), warnings


def _bootstrap_path_from_instance(
    instance: dict[str, Any] | None,
    *,
    vault: Path,
) -> Path | None:
    if not instance:
        return None
    candidates = [
        instance.get("bootstrap_database"),
        (instance.get("paths") or {}).get("bootstrap_database")
        if isinstance(instance.get("paths"), dict)
        else None,
        (instance.get("bootstrap") or {}).get("database")
        if isinstance(instance.get("bootstrap"), dict)
        else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _resolve_path(candidate, base=vault)
    return None


def _bootstrap_path_from_manifests(
    vault: Path,
) -> tuple[Path | None, dict[str, Any] | None, list[dict[str, str]]]:
    runs = vault / CONTROL_DIRECTORY / "runs"
    warnings: list[dict[str, str]] = []
    if not runs.is_dir():
        return None, None, warnings
    completed: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(runs.rglob("*.json")):
        try:
            value = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if str(value.get("product", PRODUCT)) != PRODUCT:
            continue
        if value.get("run_type") != "bootstrap":
            continue
        run_status = str(value.get("status") or "")
        if run_status == "failed":
            warnings.append(
                _warning(
                    "warning",
                    "bootstrap_run_failed",
                    "bootstrap",
                    "失敗したbootstrap runがあります",
                    path,
                )
            )
            continue
        if run_status == "running":
            warnings.append(
                _warning(
                    "warning",
                    "bootstrap_run_incomplete",
                    "bootstrap",
                    "完了していないbootstrap runがあります",
                    path,
                )
            )
            continue
        if run_status == "succeeded":
            completed.append((path, value))
    if not completed:
        return None, None, warnings

    manifest_path, manifest = max(
        completed,
        key=lambda item: _manifest_sort_key(item[1]),
    )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        warnings.append(
            _warning(
                "error",
                "bootstrap_manifest_outputs_invalid",
                "bootstrap",
                "bootstrap manifestのoutputsが配列ではありません",
                manifest_path,
            )
        )
        return None, {
            "path": str(manifest_path),
            "run_id": manifest.get("run_id"),
            "source_revision": manifest.get("source_revision"),
            "artifact_valid": False,
        }, warnings
    database_output = next(
        (
            item
            for item in outputs
            if isinstance(item, dict)
            and item.get("kind") == "municipality_database"
        ),
        None,
    )
    if database_output is None:
        warnings.append(
            _warning(
                "error",
                "bootstrap_manifest_database_missing",
                "bootstrap",
                "bootstrap manifestにmunicipality databaseがありません",
                manifest_path,
            )
        )
        return None, {
            "path": str(manifest_path),
            "run_id": manifest.get("run_id"),
            "source_revision": manifest.get("source_revision"),
            "artifact_valid": False,
        }, warnings

    database_path = _resolve_path(
        str(database_output.get("path") or ""),
        base=manifest_path.parent,
    )
    artifact_valid = True
    if database_path.is_symlink():
        artifact_valid = False
        message = "bootstrap databaseがsymlinkです"
    elif not database_path.is_file():
        artifact_valid = False
        message = "bootstrap databaseが存在しません"
    else:
        expected = str(database_output.get("sha256") or "")
        actual = hashlib.sha256(database_path.read_bytes()).hexdigest()
        if not expected:
            artifact_valid = False
            message = "bootstrap manifestにdatabaseのsha256がありません"
        elif actual != expected:
            artifact_valid = False
            message = "bootstrap databaseがrun後に変更されています"
        else:
            message = ""
    if not artifact_valid:
        warnings.append(
            _warning(
                "error",
                "bootstrap_artifact_integrity_failed",
                "bootstrap",
                message,
                database_path,
            )
        )
    return database_path, {
        "path": str(manifest_path),
        "run_id": manifest.get("run_id"),
        "source_revision": manifest.get("source_revision"),
        "artifact_valid": artifact_valid,
    }, warnings


def _manifest_sort_key(value: dict[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("finished_at") or ""),
        str(value.get("run_id") or ""),
    )


def _module_status(
    vault: Path,
    run_type: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Derive one optional module state from its append-only manifests."""

    manifest_directory = (
        vault / CONTROL_DIRECTORY / "runs" / run_type
    )
    warnings: list[dict[str, str]] = []
    manifests: list[tuple[Path, dict[str, Any]]] = []
    if not manifest_directory.is_dir():
        return {
            "state": "not_configured",
            "detail": "run manifestがありません",
            "database": None,
            "manifest": None,
            "coverage": {},
            "checks": [],
        }, warnings
    for path in sorted(manifest_directory.glob("*.json")):
        try:
            value = _read_json(path)
        except (OSError, json.JSONDecodeError) as error:
            warnings.append(
                _warning(
                    "error",
                    "module_manifest_invalid_json",
                    run_type,
                    f"run manifestを読み取れません: {error}",
                    path,
                )
            )
            continue
        if not isinstance(value, dict):
            warnings.append(
                _warning(
                    "error",
                    "module_manifest_invalid_type",
                    run_type,
                    "run manifestのトップレベルがobjectではありません",
                    path,
                )
            )
            continue
        if value.get("product") != PRODUCT or value.get("run_type") != run_type:
            continue
        manifests.append((path, value))
    if not manifests:
        return {
            "state": "not_configured",
            "detail": "有効なrun manifestがありません",
            "database": None,
            "manifest": None,
            "coverage": {},
            "checks": [],
        }, warnings

    latest_path, latest = max(
        manifests,
        key=lambda item: _manifest_sort_key(item[1]),
    )
    latest_status = str(latest.get("status") or "")
    expected_kind = MODULE_ARTIFACT_KINDS[run_type]
    database_runs: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for path, manifest in manifests:
        if manifest.get("status") != "succeeded":
            continue
        outputs = manifest.get("outputs")
        if not isinstance(outputs, list):
            continue
        artifact = next(
            (
                item
                for item in outputs
                if isinstance(item, dict) and item.get("kind") == expected_kind
            ),
            None,
        )
        if artifact is not None:
            database_runs.append((path, manifest, artifact))
    base = {
        "database": None,
        "manifest": {
            "path": str(latest_path),
            "run_id": latest.get("run_id"),
            "source_revision": latest.get("source_revision"),
            "status": latest_status,
        },
        "coverage": latest.get("coverage")
        if isinstance(latest.get("coverage"), dict)
        else {},
        "checks": latest.get("checks")
        if isinstance(latest.get("checks"), list)
        else [],
    }
    if latest_status == "failed":
        warnings.append(
            _warning(
                "error" if not database_runs else "warning",
                "module_latest_run_failed",
                run_type,
                "最新のmodule runが失敗しています",
                latest_path,
            )
        )
        if not database_runs:
            return {
                **base,
                "state": "blocked",
                "detail": "最新runが失敗。failureとcheckを確認してください",
            }, warnings
    if latest_status == "running":
        warnings.append(
            _warning(
                "warning",
                "module_latest_run_incomplete",
                run_type,
                "最新のmodule runが完了していません",
                latest_path,
            )
        )
        if not database_runs:
            return {
                **base,
                "state": "incomplete",
                "detail": "最新runがrunningのままです",
            }, warnings
    if not database_runs:
        return {
            **base,
            "state": "partial",
            "detail": "dry-runまたは診断のみ。module databaseは未生成",
        }, warnings

    manifest_path, manifest, artifact = max(
        database_runs,
        key=lambda item: _manifest_sort_key(item[1]),
    )
    database = _resolve_path(
        str(artifact.get("path") or ""),
        base=manifest_path.parent,
    )
    checks = (
        manifest.get("checks")
        if isinstance(manifest.get("checks"), list)
        else []
    )
    coverage = (
        manifest.get("coverage")
        if isinstance(manifest.get("coverage"), dict)
        else {}
    )
    manifest_info = {
        "path": str(manifest_path),
        "run_id": manifest.get("run_id"),
        "source_revision": manifest.get("source_revision"),
        "status": manifest.get("status"),
    }
    result = {
        "state": "ready",
        "detail": "artifact hash、SQLite integrity、必須checkを確認",
        "database": str(database),
        "manifest": manifest_info,
        "coverage": coverage,
        "checks": checks,
    }
    if database.is_symlink():
        result["state"] = "invalid"
        result["detail"] = "module databaseがsymlinkです"
    elif not database.is_file():
        result["state"] = "unavailable"
        result["detail"] = "module databaseが存在しません"
    else:
        expected_hash = str(artifact.get("sha256") or "")
        actual_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        if not expected_hash or actual_hash != expected_hash:
            result["state"] = "invalid"
            result["detail"] = "module databaseのhashがmanifestと一致しません"
        else:
            try:
                with sqlite3.connect(
                    sqlite_read_only_uri(database),
                    uri=True,
                ) as connection:
                    integrity = str(
                        connection.execute("PRAGMA integrity_check").fetchone()[0]
                    )
            except sqlite3.Error as error:
                integrity = f"error: {error}"
            if integrity != "ok":
                result["state"] = "invalid"
                result["detail"] = f"SQLite integrity不合格: {integrity}"
    failed_checks = [
        item
        for item in checks
        if isinstance(item, dict) and item.get("status") != "passed"
    ]
    required_check = MODULE_REQUIRED_CHECKS[run_type]
    check_names = {
        str(item.get("name"))
        for item in checks
        if isinstance(item, dict) and item.get("status") == "passed"
    }
    if result["state"] == "ready" and failed_checks:
        result["state"] = "blocked"
        result["detail"] = "module checkに不合格があります"
    elif result["state"] == "ready" and required_check not in check_names:
        result["state"] = "incomplete"
        result["detail"] = f"必須check `{required_check}` が未実施です"
    if result["state"] not in {"ready", "not_configured", "partial"}:
        warnings.append(
            _warning(
                "error" if result["state"] in {"invalid", "blocked"} else "warning",
                "module_not_ready",
                run_type,
                result["detail"],
                database,
            )
        )
    return result, warnings


def _artifact_path(artifact: dict[str, Any], vault: Path) -> Path:
    path = Path(str(artifact.get("path", ""))).expanduser()
    if not path.is_absolute():
        path = vault / path
    return path


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _verify_artifacts(
    manifest: dict[str, Any],
    vault: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    checks: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        return [], [
            _warning(
                "error",
                "manifest_artifacts_invalid",
                "onboarding",
                "manifestのartifactsは配列である必要があります",
            )
        ]
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            warnings.append(
                _warning(
                    "error",
                    "manifest_artifact_invalid",
                    "onboarding",
                    "artifact定義がobjectではありません",
                )
            )
            continue
        path = _artifact_path(artifact, vault)
        status = "passed"
        detail = ""
        if not _inside(path, vault):
            status = "failed"
            detail = "artifactがVault外を指しています"
        elif path.is_symlink():
            status = "failed"
            detail = "artifactがsymlinkです"
        elif not path.is_file():
            status = "failed"
            detail = "artifactが存在しません"
        else:
            expected = str(artifact.get("sha256") or "")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if not expected:
                status = "failed"
                detail = "artifactのsha256がありません"
            elif actual != expected:
                status = "failed"
                detail = "artifactがrun後に変更されています"
        checks.append({"path": str(path), "status": status, "detail": detail})
        if status == "failed":
            warnings.append(
                _warning(
                    "error",
                    "artifact_integrity_failed",
                    "onboarding",
                    detail,
                    path,
                )
            )
    return checks, warnings


def _read_onboarding(
    vault: Path,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    runs = vault / CONTROL_DIRECTORY / "runs"
    warnings: list[dict[str, str]] = []
    if not runs.is_dir():
        return {
            "state": "not_configured",
            "detail": "onboarding manifestがありません",
            "manifest": None,
            "artifact_checks": [],
            "profile_state": "not_configured",
            "obsidian_cli_confirmed": False,
        }, warnings

    completed: list[tuple[Path, dict[str, Any]]] = []
    run_files = sorted(runs.rglob("*.json"))
    for path in run_files:
        try:
            value = _read_json(path)
        except (OSError, json.JSONDecodeError) as error:
            warnings.append(
                _warning(
                    "warning",
                    "manifest_invalid_json",
                    "onboarding",
                    f"run manifestを読み取れません: {error}",
                    path,
                )
            )
            continue
        if not isinstance(value, dict):
            warnings.append(
                _warning(
                    "warning",
                    "manifest_invalid_type",
                    "onboarding",
                    "run manifestのトップレベルがobjectではありません",
                    path,
                )
            )
            continue
        if str(value.get("product", PRODUCT)) != PRODUCT:
            continue
        run_type = value.get("run_type")
        if run_type not in (None, "", "onboarding"):
            continue
        target = value.get("target")
        if not isinstance(target, dict):
            warnings.append(
                _warning(
                    "warning",
                    "manifest_target_missing",
                    "onboarding",
                    "run manifestにtargetがありません",
                    path,
                )
            )
            continue
        target_vault = target.get("vault_path")
        if not isinstance(target_vault, str) or not target_vault.strip():
            warnings.append(
                _warning(
                    "warning",
                    "manifest_target_vault_missing",
                    "onboarding",
                    "run manifestに対象Vaultのpathがありません",
                    path,
                )
            )
            continue
        if _resolve_path(target_vault, base=vault) != vault:
            warnings.append(
                _warning(
                    "info",
                    "manifest_for_other_vault",
                    "onboarding",
                    "別のVaultを対象とするmanifestを無視しました",
                    path,
                )
            )
            continue
        run_status = str(value.get("status") or "")
        scaffold_status = str(value.get("scaffold_status") or "")
        if run_status == "failed":
            warnings.append(
                _warning(
                    "warning",
                    "onboarding_run_failed",
                    "onboarding",
                    "失敗したonboarding runがあります",
                    path,
                )
            )
            continue
        if run_status == "running":
            warnings.append(
                _warning(
                    "warning",
                    "onboarding_run_incomplete",
                    "onboarding",
                    "完了していないonboarding runがあります",
                    path,
                )
            )
            continue
        if scaffold_status not in {"complete", "verified"}:
            continue
        completed.append((path, value))

    if not completed:
        state = "invalid" if run_files and any(
            item["severity"] == "error" for item in warnings
        ) else "incomplete"
        return {
            "state": state,
            "detail": "検証可能な完了済みonboarding manifestがありません",
            "manifest": None,
            "artifact_checks": [],
            "profile_state": "incomplete",
            "obsidian_cli_confirmed": False,
        }, warnings

    path, manifest = max(completed, key=lambda item: _manifest_sort_key(item[1]))
    artifact_checks, artifact_warnings = _verify_artifacts(manifest, vault)
    warnings.extend(artifact_warnings)
    failed_artifacts = any(item["status"] == "failed" for item in artifact_checks)
    checks = manifest.get("checks") if isinstance(manifest.get("checks"), list) else []
    obsidian_confirmed = any(
        isinstance(item, dict)
        and item.get("name") == "obsidian_cli_target"
        and item.get("status") in {"reuse", "passed", "verified"}
        for item in checks
    )
    state = "invalid" if failed_artifacts else "ready"
    return {
        "state": state,
        "detail": (
            "scaffold artifactの完全性を確認"
            if state == "ready"
            else "scaffold artifactの完全性に問題があります"
        ),
        "manifest": str(path),
        "run_id": manifest.get("run_id"),
        "source_revision": manifest.get("source_revision"),
        "artifact_checks": artifact_checks,
        "profile_state": str(manifest.get("profile_status") or "incomplete"),
        "obsidian_cli_confirmed": obsidian_confirmed,
    }, warnings


def _decode_metadata(rows: Iterable[tuple[str, str]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, raw_value in rows:
        try:
            metadata[str(key)] = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            metadata[str(key)] = raw_value
    return metadata


def _read_bootstrap(
    path: Path | None,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    empty = {
        "state": "not_configured",
        "detail": "Tier 1 databaseが設定されていません",
        "database": str(path) if path else None,
        "freshness": {"state": "unknown", "reason": "database_not_configured"},
        "municipality": None,
        "indicator_rows": 0,
        "source_periods": [],
        "verification_states": {},
        "metadata": {},
        "schema": {"state": "unknown", "compatible": False},
        "checks": [],
    }
    if path is None:
        return empty, warnings
    if not path.is_file():
        empty.update(
            {
                "state": "unavailable",
                "detail": "指定されたTier 1 databaseが存在しません",
            }
        )
        warnings.append(
            _warning(
                "error",
                "bootstrap_database_missing",
                "bootstrap",
                "指定されたTier 1 databaseが存在しません",
                path,
            )
        )
        return empty, warnings

    checks: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(sqlite_read_only_uri(path), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity_rows = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        integrity_ok = integrity_rows == ["ok"]
        checks.append(
            {
                "name": "sqlite_integrity",
                "status": "passed" if integrity_ok else "failed",
                "detail": "; ".join(integrity_rows),
            }
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_tables = {"municipality", "indicator", "build_metadata"}
        missing_tables = sorted(required_tables - tables)
        checks.append(
            {
                "name": "required_tables",
                "status": "passed" if not missing_tables else "failed",
                "detail": ", ".join(missing_tables),
            }
        )
        if not integrity_ok or missing_tables:
            raise ValueError("SQLite integrityまたは必須tableの確認に失敗")

        metadata = _decode_metadata(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT key, value FROM build_metadata ORDER BY key"
            )
        )
        schema_version = str(metadata.get("schema_version") or "")
        schema = evaluate_schema_compatibility(schema_version)
        checks.append(
            {
                "name": "schema_version",
                "status": "passed" if schema["compatible"] else "failed",
                "detail": (
                    f"{schema.get('schema_version')} ({schema['state']})"
                ),
            }
        )
        if not schema["compatible"]:
            raise ValueError(schema["reason"])
        if schema["state"] == "compatible_newer_minor":
            warnings.append(
                _warning(
                    "info",
                    "bootstrap_schema_newer_minor",
                    "bootstrap",
                    schema["reason"],
                    path,
                )
            )

        municipality_rows = connection.execute(
            """
            SELECT area_code_5, local_government_code_6, name, prefecture
            FROM municipality
            ORDER BY area_code_5
            """
        ).fetchall()
        indicator_rows = int(
            connection.execute("SELECT count(*) FROM indicator").fetchone()[0]
        )
        source_periods = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT as_of FROM indicator ORDER BY as_of"
            )
        ]
        verification_states = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT verification_state, count(*)
                FROM indicator
                GROUP BY verification_state
                ORDER BY verification_state
                """
            )
        }
        indicator_provenance = [
            {
                "indicator_key": str(row[0]),
                "as_of": str(row[1]),
                "source_name": str(row[2]),
                "fetched_at": str(row[3]),
            }
            for row in connection.execute(
                """
                SELECT indicator_key, as_of, source_name, fetched_at
                FROM indicator
                ORDER BY indicator_key, as_of
                """
            )
        ]
    except (sqlite3.Error, OSError, ValueError) as error:
        warnings.append(
            _warning(
                "error",
                "bootstrap_database_invalid",
                "bootstrap",
                str(error),
                path,
            )
        )
        empty.update(
            {
                "state": "invalid",
                "detail": "Tier 1 databaseを検証できません",
                "schema": (
                    schema
                    if "schema" in locals()
                    else {"state": "unknown", "compatible": False}
                ),
                "checks": checks,
            }
        )
        return empty, warnings
    finally:
        if "connection" in locals():
            connection.close()

    if len(municipality_rows) != 1:
        warnings.append(
            _warning(
                "error",
                "bootstrap_municipality_count",
                "bootstrap",
                f"municipality行は1件必要です: {len(municipality_rows)}",
                path,
            )
        )
        state = "invalid"
    elif indicator_rows == 0:
        warnings.append(
            _warning(
                "warning",
                "bootstrap_empty_indicators",
                "bootstrap",
                "indicatorが0件です",
                path,
            )
        )
        state = "incomplete"
    elif set(verification_states) - ACCEPTED_VERIFICATION_STATES:
        warnings.append(
            _warning(
                "warning",
                "bootstrap_verification_incomplete",
                "bootstrap",
                "未検証または要確認のindicatorがあります",
                path,
            )
        )
        state = "incomplete"
    else:
        state = "ready"

    try:
        freshness = evaluate_bootstrap_freshness(
            indicator_provenance,
            metadata,
            now=now,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        freshness = {
            "state": "unknown",
            "reason": "freshness_registry_unavailable",
            "evaluated_at": None,
            "sources": [],
        }
        warnings.append(
            _warning(
                "error",
                "freshness_registry_invalid",
                "bootstrap",
                f"鮮度registryを利用できません: {error}",
            )
        )

    census_selection = metadata.get("census_selection")
    if (
        isinstance(census_selection, dict)
        and census_selection.get("used_fallback")
    ):
        warning_text = str(census_selection.get("warning") or "国勢調査fallbackを使用")
        warnings.append(
            _warning(
                "warning",
                "bootstrap_census_fallback",
                "bootstrap",
                warning_text,
                path,
            )
        )

    municipality = (
        dict(municipality_rows[0]) if len(municipality_rows) == 1 else None
    )
    return {
        "state": state,
        "detail": {
            "ready": "SQLite integrity、schema、指標検証状態を確認",
            "incomplete": "DBは読めますが未完了の確認項目があります",
            "invalid": "自治体行またはDB構造が不正です",
        }[state],
        "database": str(path),
        "freshness": freshness,
        "municipality": municipality,
        "indicator_rows": indicator_rows,
        "source_periods": source_periods,
        "verification_states": verification_states,
        "metadata": metadata,
        "schema": schema,
        "checks": checks,
    }, warnings


def _foundation(
    vault: Path,
    onboarding: dict[str, Any],
) -> dict[str, Any]:
    guides = [
        name
        for name in ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md")
        if (vault / name).is_file()
    ]
    if not vault.is_dir():
        state = "unavailable"
        detail = "Vault候補が存在しません"
    elif not (vault / ".obsidian").is_dir():
        state = "incomplete"
        detail = "Obsidian Vaultとして登録されていません"
    elif not guides:
        state = "incomplete"
        detail = "有効なAIガイドがありません"
    elif not onboarding.get("obsidian_cli_confirmed"):
        state = "incomplete"
        detail = "Obsidian CLIの対象Vault確認が記録されていません"
    else:
        state = "ready"
        detail = "Vault、AIガイド、Obsidian CLI確認済みmanifestを確認"
    return {"state": state, "detail": detail, "guides": guides}


def _overall_state(states: Iterable[str]) -> str:
    values = set(states)
    if "invalid" in values:
        return "invalid"
    if "unavailable" in values:
        return "incomplete"
    if values == {"ready"}:
        return "ready"
    return "incomplete"


def build_status(
    vault_path: str | Path,
    *,
    bootstrap_database: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a complete read-only status report."""

    vault = Path(vault_path).expanduser().resolve(strict=False)
    warnings: list[dict[str, str]] = []
    instance, instance_warnings = _load_instance(vault)
    warnings.extend(instance_warnings)
    onboarding, onboarding_warnings = _read_onboarding(vault)
    warnings.extend(onboarding_warnings)
    bootstrap_manifest_path: Path | None = None
    bootstrap_manifest: dict[str, Any] | None = None

    if bootstrap_database is not None:
        bootstrap_path = _resolve_path(bootstrap_database, base=Path.cwd())
    else:
        bootstrap_path = _bootstrap_path_from_instance(instance, vault=vault)
        if bootstrap_path is None:
            (
                bootstrap_manifest_path,
                bootstrap_manifest,
                bootstrap_manifest_warnings,
            ) = _bootstrap_path_from_manifests(vault)
            warnings.extend(bootstrap_manifest_warnings)
            bootstrap_path = bootstrap_manifest_path
        conventional = (
            vault
            / CONTROL_DIRECTORY
            / "data"
            / "bootstrap"
            / "municipality.db"
        )
        if bootstrap_path is None and conventional.is_file():
            bootstrap_path = conventional.resolve()

    bootstrap, bootstrap_warnings = _read_bootstrap(
        bootstrap_path,
        now=now,
    )
    warnings.extend(bootstrap_warnings)
    bootstrap["manifest"] = bootstrap_manifest
    if (
        bootstrap_manifest is not None
        and not bootstrap_manifest.get("artifact_valid", False)
    ):
        bootstrap["state"] = "invalid"
        bootstrap["detail"] = "bootstrap manifestとdatabaseの完全性が一致しません"
    foundation = _foundation(vault, onboarding)
    scaffold = {
        "state": onboarding["state"],
        "detail": onboarding["detail"],
        "manifest": onboarding.get("manifest"),
        "artifact_checks": onboarding.get("artifact_checks", []),
    }
    profile_manifest_state = onboarding.get("profile_state")
    profile_state = (
        "ready"
        if profile_manifest_state in {"ready", "complete", "verified"}
        else (
            "not_configured"
            if profile_manifest_state == "not_configured"
            else "incomplete"
        )
    )
    profile = {
        "state": profile_state,
        "detail": (
            "議員・議会profileを人が確認済み"
            if profile_state == "ready"
            else "議員・議会profileは人の確認が必要"
        ),
    }
    if bootstrap["state"] == "ready":
        freshness_state = bootstrap["freshness"]["state"]
        tier1_state = {
            "fresh": "ready",
            "due": "due",
            "stale": "stale",
            "unknown": "incomplete",
        }.get(freshness_state, "incomplete")
    else:
        tier1_state = bootstrap["state"]
    requirements = {
        "foundation_ready": foundation["state"],
        "scaffold_ready": scaffold["state"],
        "profile_ready": profile["state"],
        "tier1_data_ready": tier1_state,
    }
    modules: dict[str, dict[str, Any]] = {}
    for run_type in MODULE_TYPES:
        module, module_warnings = _module_status(vault, run_type)
        modules[run_type] = module
        warnings.extend(module_warnings)
        requirements[f"module_ready:{run_type}"] = module["state"]
    instance_state = (
        "invalid"
        if any(
            item["severity"] == "error"
            and item["component"] == "instance"
            for item in warnings
        )
        else ("ready" if instance is not None else "not_configured")
    )
    overall_inputs = [
        requirements["foundation_ready"],
        requirements["scaffold_ready"],
        requirements["profile_ready"],
        requirements["tier1_data_ready"],
    ]
    if instance_state == "invalid":
        overall_inputs.append("invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT,
        "generated_at": _utc_now(),
        "status": _overall_state(overall_inputs),
        "vault": {
            "path": str(vault),
            "state": "ready" if vault.is_dir() else "unavailable",
        },
        "instance": {
            "path": str(vault / CONTROL_DIRECTORY / INSTANCE_FILE),
            "state": instance_state,
            "value": instance,
        },
        "foundation": foundation,
        "scaffold": scaffold,
        "profile": profile,
        "bootstrap": bootstrap,
        "modules": modules,
        "requirements": requirements,
        "warnings": _ordered_warnings(warnings),
    }


def requirements_met(
    report: dict[str, Any],
    requirements: Iterable[str],
) -> bool:
    """Return whether every requested gate is ready."""

    values = report.get("requirements", {})
    return all(values.get(requirement) in READY_STATES for requirement in requirements)
