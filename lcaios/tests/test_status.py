"""Tests for read-only status derivation and CLI behavior."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.status import build_status


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _create_vault(path: Path) -> None:
    (path / ".obsidian").mkdir(parents=True)
    (path / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")


def _create_manifest(
    vault: Path,
    *,
    run_id: str = "run-1",
    status: str = "incomplete",
    scaffold_status: str = "complete",
    profile_status: str = "incomplete",
    finished_at: str = "2026-07-24T01:00:00Z",
    artifact_content: str = "# Note\n",
) -> Path:
    artifact = vault / "地方議員AI運用OS.md"
    artifact.write_text(artifact_content, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "product": "local-councilor-ai-os",
        "source_revision": "test",
        "run_id": run_id,
        "status": status,
        "started_at": "2026-07-24T00:00:00Z",
        "finished_at": finished_at,
        "target": {"vault_path": str(vault), "agent": "codex"},
        "artifacts": [
            {
                "path": str(artifact),
                "sha256": hashlib.sha256(artifact_content.encode()).hexdigest(),
            }
        ],
        "checks": [{"name": "obsidian_cli_target", "status": "reuse"}],
        "scaffold_status": scaffold_status,
        "profile_status": profile_status,
    }
    path = vault / ".local-councilor-ai-os" / "runs" / f"{run_id}.json"
    _write_json(path, manifest)
    return path


def _create_bootstrap_database(
    path: Path,
    *,
    used_fallback: bool = False,
    verification_state: str = "reconciled",
    indicator_count: int = 2,
    fetched_at: str = "2026-07-23T00:00:00Z",
    schema_version: str = "1",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE municipality (
                area_code_5 TEXT,
                local_government_code_6 TEXT,
                name TEXT,
                prefecture TEXT
            );
            CREATE TABLE indicator (
                indicator_key TEXT,
                as_of TEXT,
                verification_state TEXT,
                source_name TEXT,
                fetched_at TEXT
            );
            CREATE TABLE build_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO municipality VALUES (?, ?, ?, ?)",
            ("41441", "414417", "太良町", "佐賀県"),
        )
        indicator_rows = [
            (
                (
                    "population_total"
                    if index % 2 == 0
                    else "zaiseiryoku_shisuu"
                ),
                "2020-10-01" if index % 2 == 0 else "2024年度",
                verification_state,
                "e-Stat" if index % 2 == 0 else "総務省",
                fetched_at,
            )
            for index in range(indicator_count)
        ]
        connection.executemany(
            "INSERT INTO indicator VALUES (?, ?, ?, ?, ?)",
            indicator_rows,
        )
        metadata = {
            "schema_version": schema_version,
            "built_at": "2026-07-24T00:00:00Z",
            "census_selection": json.dumps(
                {
                    "used_fallback": used_fallback,
                    "warning": "fallback warning" if used_fallback else None,
                    "reason": "latest shared survey date",
                },
                ensure_ascii=False,
            ),
            "fiscal_discovery": json.dumps(
                {
                    "index_url": "https://example.invalid/fiscal-index",
                    "year_page_url": "https://example.invalid/fiscal-2024",
                },
                ensure_ascii=False,
            ),
        }
        connection.executemany(
            "INSERT INTO build_metadata VALUES (?, ?)",
            metadata.items(),
        )


def _create_bootstrap_manifest(
    vault: Path,
    database: Path,
    *,
    run_id: str = "bootstrap-run",
    status: str = "succeeded",
    finished_at: str = "2026-07-24T02:00:00Z",
) -> Path:
    manifest = {
        "schema_version": 1,
        "product": "local-councilor-ai-os",
        "source_revision": "test",
        "run_id": run_id,
        "run_type": "bootstrap",
        "status": status,
        "started_at": "2026-07-24T01:00:00Z",
        "finished_at": finished_at,
        "target": {"output_directory": str(database.parent)},
        "scope": {"area_code_5": "41441"},
        "outputs": [
            {
                "kind": "municipality_database",
                "path": str(database),
                "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                "size_bytes": database.stat().st_size,
                "schema_version": 1,
            }
        ],
        "checks": [{"name": "sqlite_integrity", "status": "passed"}],
        "warnings": [],
        "failures": [],
    }
    path = (
        vault
        / ".local-councilor-ai-os"
        / "runs"
        / "bootstrap"
        / f"{run_id}.json"
    )
    _write_json(path, manifest)
    return path


class StatusTests(unittest.TestCase):
    def test_empty_vault_reports_not_configured_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            before = sorted(vault.rglob("*"))
            report = build_status(vault)
            self.assertEqual("incomplete", report["status"])
            self.assertEqual("not_configured", report["scaffold"]["state"])
            self.assertEqual("not_configured", report["bootstrap"]["state"])
            self.assertEqual(before, sorted(vault.rglob("*")))

    def test_latest_completed_manifest_wins_over_failed_and_running_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            completed = _create_manifest(vault, run_id="complete")
            _create_manifest(
                vault,
                run_id="failed",
                status="failed",
                finished_at="2026-07-24T03:00:00Z",
            )
            _create_manifest(
                vault,
                run_id="running",
                status="running",
                scaffold_status="",
                finished_at="",
            )
            report = build_status(vault)
            self.assertEqual("ready", report["scaffold"]["state"])
            self.assertEqual(
                str(completed.resolve()),
                report["scaffold"]["manifest"],
            )
            codes = [item["code"] for item in report["warnings"]]
            self.assertIn("onboarding_run_failed", codes)
            self.assertIn("onboarding_run_incomplete", codes)

    def test_artifact_hash_mismatch_blocks_scaffold_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            _create_manifest(vault)
            (vault / "地方議員AI運用OS.md").write_text(
                "modified\n",
                encoding="utf-8",
            )
            report = build_status(vault)
            self.assertEqual("invalid", report["scaffold"]["state"])
            self.assertEqual(
                "artifact_integrity_failed",
                report["warnings"][0]["code"],
            )

    def test_invalid_manifest_does_not_hide_valid_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            _create_manifest(vault)
            invalid = vault / ".local-councilor-ai-os" / "runs" / "bad.json"
            invalid.write_text("{", encoding="utf-8")
            report = build_status(vault)
            self.assertEqual("ready", report["scaffold"]["state"])
            self.assertIn(
                "manifest_invalid_json",
                [item["code"] for item in report["warnings"]],
            )

    def test_non_onboarding_run_is_ignored_without_target_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            _create_manifest(vault)
            _write_json(
                vault
                / ".local-councilor-ai-os"
                / "runs"
                / "bootstrap"
                / "data-run.json",
                {
                    "schema_version": 1,
                    "product": "local-councilor-ai-os",
                    "run_type": "bootstrap",
                    "run_id": "data-run",
                    "status": "succeeded",
                },
            )
            report = build_status(vault)
            self.assertEqual("ready", report["scaffold"]["state"])
            self.assertNotIn(
                "manifest_target_missing",
                [item["code"] for item in report["warnings"]],
            )

    def test_manifest_without_target_vault_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            _write_json(
                vault / ".local-councilor-ai-os" / "runs" / "missing-target.json",
                {
                    "schema_version": 1,
                    "product": "local-councilor-ai-os",
                    "run_id": "missing-target",
                    "status": "incomplete",
                    "finished_at": "2026-07-24T01:00:00Z",
                    "target": {},
                    "scaffold_status": "complete",
                    "profile_status": "incomplete",
                    "artifacts": [],
                },
            )
            report = build_status(vault)
            self.assertEqual("incomplete", report["scaffold"]["state"])
            self.assertIn(
                "manifest_target_vault_missing",
                [item["code"] for item in report["warnings"]],
            )

    def test_symlink_artifact_is_rejected_even_when_target_is_inside_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            manifest = _create_manifest(vault)
            artifact = vault / "地方議員AI運用OS.md"
            target = vault / "real.md"
            target.write_text(artifact.read_text(encoding="utf-8"), encoding="utf-8")
            artifact.unlink()
            artifact.symlink_to(target)
            report = build_status(vault)
            self.assertEqual("invalid", report["scaffold"]["state"])
            self.assertEqual(
                "artifact_integrity_failed",
                report["warnings"][0]["code"],
            )
            self.assertEqual(
                str(manifest.resolve()),
                report["scaffold"]["manifest"],
            )

    def test_instance_relative_database_is_resolved_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            _create_manifest(vault, profile_status="complete")
            database = (
                vault
                / ".local-councilor-ai-os"
                / "data"
                / "bootstrap"
                / "municipality.db"
            )
            _create_bootstrap_database(database)
            _write_json(
                vault / ".local-councilor-ai-os" / "instance.json",
                {
                    "schema_version": 1,
                    "product": "local-councilor-ai-os",
                    "paths": {
                        "bootstrap_database": (
                            ".local-councilor-ai-os/data/bootstrap/municipality.db"
                        )
                    },
                },
            )
            report = build_status(vault)
            self.assertEqual("ready", report["status"])
            self.assertEqual("ready", report["bootstrap"]["state"])
            self.assertEqual("fresh", report["bootstrap"]["freshness"]["state"])
            self.assertEqual(2, report["bootstrap"]["indicator_rows"])
            self.assertEqual("太良町", report["bootstrap"]["municipality"]["name"])

    def test_bootstrap_manifest_discovers_database_without_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            database = vault / "data" / "municipality.db"
            _create_bootstrap_database(database)
            manifest = _create_bootstrap_manifest(vault, database)
            report = build_status(vault)
            self.assertEqual("ready", report["bootstrap"]["state"])
            self.assertEqual(
                str(manifest.resolve()),
                report["bootstrap"]["manifest"]["path"],
            )
            self.assertTrue(
                report["bootstrap"]["manifest"]["artifact_valid"]
            )

    def test_tampered_bootstrap_database_is_invalid_through_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            database = vault / "data" / "municipality.db"
            _create_bootstrap_database(database)
            _create_bootstrap_manifest(vault, database)
            with database.open("ab") as handle:
                handle.write(b"tampered")
            report = build_status(vault)
            self.assertEqual("invalid", report["bootstrap"]["state"])
            self.assertIn(
                "bootstrap_artifact_integrity_failed",
                [item["code"] for item in report["warnings"]],
            )

    def test_failed_bootstrap_run_does_not_hide_latest_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            database = vault / "data" / "municipality.db"
            _create_bootstrap_database(database)
            _create_bootstrap_manifest(vault, database)
            _write_json(
                vault
                / ".local-councilor-ai-os"
                / "runs"
                / "bootstrap"
                / "failed.json",
                {
                    "schema_version": 1,
                    "product": "local-councilor-ai-os",
                    "run_id": "failed",
                    "run_type": "bootstrap",
                    "status": "failed",
                    "finished_at": "2026-07-24T03:00:00Z",
                    "outputs": [],
                },
            )
            report = build_status(vault)
            self.assertEqual("ready", report["bootstrap"]["state"])
            self.assertIn(
                "bootstrap_run_failed",
                [item["code"] for item in report["warnings"]],
            )

    def test_census_fallback_marks_freshness_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(database, used_fallback=True)
            report = build_status(temporary, bootstrap_database=database)
            self.assertEqual("ready", report["bootstrap"]["state"])
            self.assertEqual("stale", report["bootstrap"]["freshness"]["state"])
            self.assertIn(
                "bootstrap_census_fallback",
                [item["code"] for item in report["warnings"]],
            )

    def test_freshness_becomes_due_after_registry_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(
                database,
                fetched_at="2024-01-01T00:00:00Z",
            )
            report = build_status(
                temporary,
                bootstrap_database=database,
                now=datetime(2026, 7, 24, tzinfo=timezone.utc),
            )
            self.assertEqual("due", report["bootstrap"]["freshness"]["state"])
            self.assertEqual("due", report["requirements"]["tier1_data_ready"])

    def test_missing_retrieval_time_makes_freshness_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(database, fetched_at="")
            report = build_status(database.parent, bootstrap_database=database)
            self.assertEqual(
                "unknown",
                report["bootstrap"]["freshness"]["state"],
            )
            self.assertEqual(
                "incomplete",
                report["requirements"]["tier1_data_ready"],
            )

    def test_invalid_instance_is_not_used_to_resolve_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_vault(vault)
            database = vault / "municipality.db"
            _create_bootstrap_database(database)
            _write_json(
                vault / ".local-councilor-ai-os" / "instance.json",
                {
                    "schema_version": 999,
                    "product": "different-product",
                    "paths": {"bootstrap_database": "municipality.db"},
                },
            )
            report = build_status(vault)
            self.assertEqual("invalid", report["status"])
            self.assertEqual("invalid", report["instance"]["state"])
            self.assertEqual("not_configured", report["bootstrap"]["state"])
            self.assertEqual(
                {
                    "instance_product_mismatch",
                    "instance_schema_unsupported",
                },
                {
                    item["code"]
                    for item in report["warnings"]
                    if item["component"] == "instance"
                },
            )

    def test_unverified_indicator_keeps_database_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(
                database,
                verification_state="needs_review",
            )
            report = build_status(temporary, bootstrap_database=database)
            self.assertEqual("incomplete", report["bootstrap"]["state"])

    def test_newer_minor_schema_is_read_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(database, schema_version="1.2")
            report = build_status(temporary, bootstrap_database=database)
            self.assertEqual("ready", report["bootstrap"]["state"])
            self.assertEqual(
                "compatible_newer_minor",
                report["bootstrap"]["schema"]["state"],
            )
            self.assertIn(
                "bootstrap_schema_newer_minor",
                [item["code"] for item in report["warnings"]],
            )

    def test_incompatible_major_schema_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(database, schema_version="2")
            report = build_status(temporary, bootstrap_database=database)
            self.assertEqual("invalid", report["bootstrap"]["state"])
            self.assertEqual(
                "incompatible_major",
                report["bootstrap"]["schema"]["state"],
            )

    def test_corrupt_database_is_invalid_not_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            database.write_text("not sqlite", encoding="utf-8")
            report = build_status(temporary, bootstrap_database=database)
            self.assertEqual("invalid", report["bootstrap"]["state"])
            self.assertIn(
                "bootstrap_database_invalid",
                [item["code"] for item in report["warnings"]],
            )

    def test_cli_require_returns_two_only_when_gate_is_unmet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "status",
                        "--vault",
                        str(vault),
                        "--format",
                        "json",
                        "--require",
                        "tier1_data_ready",
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertEqual(
                "not_configured",
                json.loads(output.getvalue())["bootstrap"]["state"],
            )

    def test_freshness_cli_returns_source_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _create_bootstrap_database(database)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "freshness",
                        "--vault",
                        temporary,
                        "--bootstrap-db",
                        str(database),
                        "--format",
                        "json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(0, exit_code)
            self.assertEqual("fresh", payload["freshness"]["state"])
            self.assertEqual(2, len(payload["freshness"]["sources"]))

    def test_cli_invalid_requirement_is_parser_error(self) -> None:
        error = StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stderr(error):
            main(
                [
                    "status",
                    "--vault",
                    "/tmp/example",
                    "--require",
                    "unknown",
                ]
            )
        self.assertEqual(2, raised.exception.code)
        self.assertIn("未対応のrequirement", error.getvalue())


if __name__ == "__main__":
    unittest.main()
