"""Tests for shared optional-module run manifests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_database_run,
)


class ModuleManifestTests(unittest.TestCase):
    def test_database_run_records_hash_integrity_and_redacts_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "module.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE fixture (value TEXT)")
            secret = "test-secret-do-not-store"
            with patch.dict(os.environ, {"ESTAT_APPID": secret}):
                path, manifest = begin_module_run(
                    root / "runs",
                    run_type="minutes",
                    repo_root=Path(__file__).resolve().parents[2],
                    run_id="test-run",
                    requested={
                        "url": f"https://example.test/data?appId={secret}",
                        "database": database,
                    },
                )
                finish_database_run(
                    path,
                    manifest,
                    database=database,
                    artifact_kind="minutes_database",
                    checks=[
                        {
                            "name": "meeting_rows",
                            "status": "passed",
                            "detail": 1,
                        }
                    ],
                )

            self.assertIsNotNone(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("succeeded", value["status"])
            self.assertEqual("passed", value["checks"][0]["status"])
            self.assertEqual(64, len(value["outputs"][0]["sha256"]))
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))
            self.assertIn("appId=REDACTED", value["requested"]["url"])

    def test_failed_run_is_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, manifest = begin_module_run(
                root / "runs",
                run_type="regulations",
                repo_root=Path(__file__).resolve().parents[2],
                run_id="failed-run",
                requested={"action": "ingest"},
            )
            fail_module_run(path, manifest, ValueError("fixture failure"))
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("failed", value["status"])
            self.assertEqual("ValueError", value["failures"][0]["code"])


if __name__ == "__main__":
    unittest.main()
