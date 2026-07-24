"""Tests for schema verification, backup, recovery, and inventory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.database import (
    evaluate_schema_compatibility,
    verify_bootstrap_database,
)
from lcaios.lifecycle import (
    backup_database,
    file_sha256,
    generated_artifacts,
    restore_database,
)


def _database(path: Path, *, schema_version: str = "1", value: str = "old") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE municipality (name TEXT);
            CREATE TABLE indicator (indicator_key TEXT, value TEXT);
            CREATE TABLE build_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO municipality VALUES ('太良町')")
        connection.execute(
            "INSERT INTO indicator VALUES ('fixture', ?)",
            (value,),
        )
        connection.execute(
            "INSERT INTO build_metadata VALUES ('schema_version', ?)",
            (schema_version,),
        )


def _indicator_value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection, connection:
        return str(
            connection.execute("SELECT value FROM indicator").fetchone()[0]
        )


class LifecycleTests(unittest.TestCase):
    def test_schema_major_minor_policy(self) -> None:
        self.assertEqual(
            "compatible",
            evaluate_schema_compatibility("1")["state"],
        )
        self.assertEqual(
            "compatible_newer_minor",
            evaluate_schema_compatibility("1.2")["state"],
        )
        self.assertFalse(
            evaluate_schema_compatibility("2.0")["compatible"]
        )
        self.assertEqual(
            "unknown",
            evaluate_schema_compatibility("bad")["state"],
        )

    def test_verify_rejects_incompatible_major(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "municipality.db"
            _database(database, schema_version="2")
            report = verify_bootstrap_database(database)
            self.assertFalse(report["ok"])
            self.assertEqual(
                "incompatible_major",
                report["schema"]["state"],
            )

    def test_backup_is_verified_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "municipality.db"
            _database(source)
            source_hash = file_sha256(source)
            report = backup_database(source, root / "backups")
            backup = Path(report["backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(source_hash, file_sha256(source))
            self.assertEqual(report["backup_sha256"], file_sha256(backup))
            self.assertTrue(report["verification"]["ok"])

    def test_backup_rejects_symlink_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            link = root / "linked.db"
            _database(source)
            link.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "symlink"):
                backup_database(link, root / "backups")

    def test_restore_requires_hash_and_preserves_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            target = root / "target.db"
            _database(source, value="from-backup")
            _database(target, value="current-target")
            backup = backup_database(source, root / "backups")
            target_hash_before = file_sha256(target)

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                restore_database(
                    backup["backup"],
                    target,
                    accepted_sha256="wrong",
                )
            self.assertEqual(target_hash_before, file_sha256(target))
            self.assertEqual("current-target", _indicator_value(target))

            restored = restore_database(
                backup["backup"],
                target,
                accepted_sha256=backup["backup_sha256"],
            )
            previous = Path(str(restored["previous"]))
            self.assertEqual("from-backup", _indicator_value(target))
            self.assertTrue(previous.is_file())
            self.assertEqual("current-target", _indicator_value(previous))

    def test_generated_artifacts_only_uses_manifest_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            artifact = vault / "generated.md"
            artifact.write_text("generated\n", encoding="utf-8")
            ignored = vault / "human-note.md"
            ignored.write_text("human\n", encoding="utf-8")
            manifest = {
                "product": "local-councilor-ai-os",
                "run_id": "run-1",
                "run_type": "onboarding",
                "artifacts": [
                    {
                        "path": str(artifact),
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ],
            }
            runs = vault / ".local-councilor-ai-os" / "runs"
            runs.mkdir(parents=True)
            (runs / "run-1.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (runs / "broken.json").write_text("{", encoding="utf-8")
            report = generated_artifacts(vault)
            self.assertEqual([str(artifact.resolve())], [
                item["path"] for item in report["artifacts"]
            ])
            self.assertNotIn(str(ignored.resolve()), repr(report))
            self.assertEqual(1, len(report["invalid_manifests"]))
            self.assertTrue(report["read_only"])

    def test_cli_verify_and_backup_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            target = root / "target.db"
            _database(source, value="restored")
            output = StringIO()
            with redirect_stdout(output):
                verify_exit = main(
                    [
                        "verify",
                        "database",
                        "--file",
                        str(source),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(0, verify_exit)
            self.assertTrue(json.loads(output.getvalue())["ok"])

            output = StringIO()
            with redirect_stdout(output):
                backup_exit = main(
                    [
                        "backup",
                        "database",
                        "--file",
                        str(source),
                        "--out-dir",
                        str(root / "backups"),
                        "--format",
                        "json",
                    ]
                )
            backup = json.loads(output.getvalue())
            self.assertEqual(0, backup_exit)

            output = StringIO()
            with redirect_stdout(output):
                restore_exit = main(
                    [
                        "restore",
                        "database",
                        "--backup",
                        backup["backup"],
                        "--target",
                        str(target),
                        "--accept-sha256",
                        backup["backup_sha256"],
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(0, restore_exit)
            self.assertEqual("restored", _indicator_value(target))

    def test_cli_wrong_restore_hash_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            _database(source)
            backup = backup_database(source, root / "backups")
            error = StringIO()
            with redirect_stderr(error):
                exit_code = main(
                    [
                        "restore",
                        "database",
                        "--backup",
                        backup["backup"],
                        "--target",
                        str(root / "target.db"),
                        "--accept-sha256",
                        "wrong",
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertIn("SHA-256", error.getvalue())


if __name__ == "__main__":
    unittest.main()
