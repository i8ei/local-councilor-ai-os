"""Tests for bootstrap run manifest creation and secret redaction."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrap.cli.main import main
from bootstrap.cli.resolve import ResolveError


def _successful_report(root: Path) -> dict[str, object]:
    database = root / "municipality.db"
    authority_map = root / "authority_map.yaml"
    database.write_bytes(b"sqlite fixture")
    authority_map.write_text("schema_version: 1\n", encoding="utf-8")
    return {
        "status": "ok",
        "mode": "offline",
        "municipality": {
            "name": "太良町",
            "prefecture": "佐賀県",
            "area_code_5": "41441",
            "local_government_code_6": "414417",
        },
        "census": {
            "indicator_count": 3,
            "used_fallback": False,
            "selection_reason": "fixture",
        },
        "fiscal": {
            "indicator_count": 6,
            "fiscal_year": "2024",
            "primary_status": "verified",
            "cross_check_status": "reconciled",
        },
        "database": {
            "path": str(database),
            "indicator_rows": 9,
            "integrity_check": "ok",
        },
        "authority_map": {
            "path": str(authority_map),
            "indicator_routes": 9,
        },
        "live_request_count": 0,
        "warnings": [],
    }


class BootstrapManifestTests(unittest.TestCase):
    def test_success_writes_manifest_and_returns_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests = root / "runs"
            output = io.StringIO()
            report = _successful_report(root)
            with (
                patch("bootstrap.cli.main.run", return_value=report),
                contextlib.redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "太良町",
                        "--offline",
                        "--manifest-dir",
                        str(manifests),
                    ]
                )
            payload = json.loads(output.getvalue())
            manifest_path = Path(payload["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(0, exit_code)
            self.assertEqual("bootstrap", manifest["run_type"])
            self.assertEqual("succeeded", manifest["status"])
            self.assertEqual("41441", manifest["scope"]["area_code_5"])
            self.assertEqual(2, len(manifest["outputs"]))
            database_output = next(
                item
                for item in manifest["outputs"]
                if item["kind"] == "municipality_database"
            )
            self.assertEqual(
                hashlib.sha256((root / "municipality.db").read_bytes()).hexdigest(),
                database_output["sha256"],
            )
            self.assertEqual("passed", manifest["checks"][0]["status"])

    def test_failure_manifest_redacts_estat_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = "must-not-appear"
            output = io.StringIO()
            error = ResolveError(
                f"request failed: https://example.invalid/?appId={secret}"
            )
            with (
                patch.dict(os.environ, {"ESTAT_APPID": secret}, clear=False),
                patch("bootstrap.cli.main.run", side_effect=error),
                contextlib.redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "太良町",
                        "--manifest-dir",
                        str(root / "runs"),
                    ]
                )
            payload = json.loads(output.getvalue())
            manifest_content = Path(payload["manifest"]).read_text(encoding="utf-8")
            self.assertEqual(1, exit_code)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, manifest_content)
            self.assertIn("REDACTED", manifest_content)
            self.assertEqual(
                "failed",
                json.loads(manifest_content)["status"],
            )

    def test_missing_output_marks_manifest_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = _successful_report(root)
            Path(str(report["database"]["path"])).unlink()
            output = io.StringIO()
            with (
                patch("bootstrap.cli.main.run", return_value=report),
                contextlib.redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "太良町",
                        "--manifest-dir",
                        str(root / "runs"),
                    ]
                )
            payload = json.loads(output.getvalue())
            manifest = json.loads(
                Path(payload["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(1, exit_code)
            self.assertEqual("failed", manifest["status"])
            self.assertEqual(
                "manifest_finalize_failed",
                manifest["failures"][0]["code"],
            )


if __name__ == "__main__":
    unittest.main()

