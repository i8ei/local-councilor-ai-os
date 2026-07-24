"""Tests for optional module manifest aggregation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.status import build_status


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fixture (value TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('ok')")


def _manifest(
    vault: Path,
    run_type: str,
    database: Path | None,
    *,
    run_id: str,
    finished_at: str,
    status: str = "succeeded",
    checks: list[dict[str, object]] | None = None,
) -> Path:
    directory = (
        vault / ".local-councilor-ai-os" / "runs" / run_type
    )
    directory.mkdir(parents=True, exist_ok=True)
    outputs = []
    if database is not None:
        outputs.append(
            {
                "kind": {
                    "minutes": "minutes_database",
                    "regulations": "regulations_database",
                    "benchmark": "benchmark_database",
                    "budget": "budget_database",
                    "settlement": "settlement_database",
                }[run_type],
                "path": str(database),
                "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
            }
        )
    value = {
        "schema_version": 1,
        "product": "local-councilor-ai-os",
        "run_id": run_id,
        "run_type": run_type,
        "status": status,
        "finished_at": finished_at,
        "outputs": outputs,
        "coverage": {"fixture": 1},
        "checks": checks or [],
        "failures": [],
    }
    path = directory / f"{run_id}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ModuleStatusTests(unittest.TestCase):
    def test_minutes_manifest_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            database = vault / "minutes.db"
            _database(database)
            _manifest(
                vault,
                "minutes",
                database,
                run_id="minutes-1",
                finished_at="2026-07-24T01:00:00Z",
                checks=[
                    {
                        "name": "sqlite_integrity",
                        "status": "passed",
                        "detail": "ok",
                    },
                    {
                        "name": "meeting_rows",
                        "status": "passed",
                        "detail": 1,
                    },
                ],
            )
            report = build_status(vault)
            self.assertEqual("ready", report["modules"]["minutes"]["state"])
            self.assertEqual(
                "ready",
                report["requirements"]["module_ready:minutes"],
            )

    def test_budget_needs_reconciliation_and_latest_failure_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            database = vault / "budget.db"
            _database(database)
            _manifest(
                vault,
                "budget",
                database,
                run_id="ingest",
                finished_at="2026-07-24T01:00:00Z",
                checks=[
                    {
                        "name": "sqlite_integrity",
                        "status": "passed",
                        "detail": "ok",
                    },
                    {
                        "name": "budget_rows",
                        "status": "passed",
                        "detail": 1,
                    },
                ],
            )
            self.assertEqual(
                "incomplete",
                build_status(vault)["modules"]["budget"]["state"],
            )
            _manifest(
                vault,
                "budget",
                database,
                run_id="verify",
                finished_at="2026-07-24T02:00:00Z",
                checks=[
                    {
                        "name": "sqlite_integrity",
                        "status": "passed",
                        "detail": "ok",
                    },
                    {
                        "name": "budget_reconciliation",
                        "status": "passed",
                        "detail": "exit_code=0",
                    },
                ],
            )
            self.assertEqual(
                "ready",
                build_status(vault)["modules"]["budget"]["state"],
            )
            _manifest(
                vault,
                "budget",
                None,
                run_id="failed",
                finished_at="2026-07-24T03:00:00Z",
                status="failed",
            )
            report = build_status(vault)
            self.assertEqual("ready", report["modules"]["budget"]["state"])
            self.assertEqual(
                "verify",
                report["modules"]["budget"]["manifest"]["run_id"],
            )
            self.assertIn(
                "module_latest_run_failed",
                {item["code"] for item in report["warnings"]},
            )

    def test_failed_module_without_success_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _manifest(
                vault,
                "minutes",
                None,
                run_id="failed",
                finished_at="2026-07-24T03:00:00Z",
                status="failed",
            )
            self.assertEqual(
                "blocked",
                build_status(vault)["modules"]["minutes"]["state"],
            )

    def test_cli_can_require_optional_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "status",
                        "--vault",
                        temporary,
                        "--require",
                        "module_ready:minutes",
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertEqual(
                "not_configured",
                json.loads(output.getvalue())["modules"]["minutes"]["state"],
            )


if __name__ == "__main__":
    unittest.main()
