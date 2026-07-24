"""Tests for bootstrap smoke-test comparison and CLI rendering."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from lcaios.cli import main
from lcaios.smoke_test import (
    SmokeTestError,
    _authority_semantic_content,
    _indicator_rows,
    run_bootstrap_smoke_test,
)


def _database(path: Path, value: str = "10") -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE indicator (
                id INTEGER PRIMARY KEY,
                municipality_code TEXT,
                indicator_key TEXT,
                value TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO indicator VALUES (1, '41205', 'fixture', ?)",
            (value,),
        )


class SmokeTestTests(unittest.TestCase):
    def test_existing_work_directory_must_be_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(SmokeTestError, "空"):
                run_bootstrap_smoke_test("伊万里市", work_dir=root)

    def test_semantic_comparison_ignores_ids_and_generated_at(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left.db"
            right = root / "right.db"
            _database(left)
            _database(right)
            left_map = root / "left.yaml"
            right_map = root / "right.yaml"
            left_map.write_text(
                'schema_version: "1"\ngenerated_at: "one"\nvalue: route\n',
                encoding="utf-8",
            )
            right_map.write_text(
                'schema_version: "1"\ngenerated_at: "two"\nvalue: route\n',
                encoding="utf-8",
            )
            self.assertEqual(_indicator_rows(left), _indicator_rows(right))
            self.assertEqual(
                _authority_semantic_content(left_map),
                _authority_semantic_content(right_map),
            )

    def test_cli_returns_verification_exit_code(self) -> None:
        report = {
            "status": "passed",
            "municipality": {"prefecture": "佐賀県", "name": "伊万里市"},
            "work_directory": "/tmp/fixture",
            "online": {
                "retrieval": {
                    "live_request_count": 4,
                    "cache_hit_count": 2,
                }
            },
            "offline": {"live_request_count": 0},
            "checks": [
                {"name": "fixture", "status": "passed", "detail": "ok"}
            ],
        }
        output = StringIO()
        with (
            patch(
                "lcaios.cli.run_bootstrap_smoke_test",
                return_value=report,
            ),
            redirect_stdout(output),
        ):
            exit_code = main(
                [
                    "smoke-test",
                    "bootstrap",
                    "伊万里市",
                    "--prefecture",
                    "佐賀県",
                ]
            )
        self.assertEqual(0, exit_code)
        self.assertIn("Bootstrap smoke test", output.getvalue())


if __name__ == "__main__":
    unittest.main()
