"""Tests for hash-only human profile confirmation."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.profile import confirm_profile
from lcaios.status import build_status

PROFILE_TEXT = """\
schema_version: 1
status: confirmed

council:
  role: councilor

working_style:
  decision_owner: councilor

privacy:
  internal_information_policy: local-rules
"""

ADAPTER_TEXT = """\
# 議会アダプター

## 公開情報

議会日程と公開資料の入口は、議員本人が公式サイトで確認して記録する。

## ローカル運用

質問通告、内部確認、公開前レビューの順序を分離し、公開操作は本人が行う。
"""


def _create_vault(root: Path) -> tuple[Path, Path]:
    (root / ".obsidian").mkdir()
    profile = root / "プロファイル" / "councilor-profile.yaml"
    adapter = root / "プロファイル" / "council-adapter.md"
    profile.parent.mkdir()
    profile.write_text(PROFILE_TEXT, encoding="utf-8")
    adapter.write_text(ADAPTER_TEXT, encoding="utf-8")
    return profile, adapter


class ProfileConfirmationTests(unittest.TestCase):
    def test_confirmation_records_hashes_and_status_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            before = (profile.read_bytes(), adapter.read_bytes())
            result = confirm_profile(
                vault,
                profile=profile,
                council_adapter=adapter,
                human_reviewed=True,
            )
            self.assertEqual("confirmed", result["status"])
            self.assertEqual(before, (profile.read_bytes(), adapter.read_bytes()))
            report = build_status(vault)
            self.assertEqual("ready", report["profile"]["state"])
            manifest_text = Path(result["manifest"]).read_text(encoding="utf-8")
            self.assertNotIn("decision_owner", manifest_text)
            self.assertNotIn("質問通告", manifest_text)
            manifest = json.loads(manifest_text)
            self.assertEqual([], manifest["outputs"])
            self.assertEqual(
                ["councilor_profile", "council_adapter"],
                [item["kind"] for item in manifest["inputs"]],
            )

    def test_explicit_human_confirmation_is_required_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            with self.assertRaisesRegex(ValueError, "confirm-human-reviewed"):
                confirm_profile(
                    vault,
                    profile=profile,
                    council_adapter=adapter,
                    human_reviewed=False,
                )
            self.assertFalse(
                (vault / ".local-councilor-ai-os").exists()
            )

    def test_placeholder_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            profile.write_text(
                PROFILE_TEXT.replace("councilor", "<council-role>", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "placeholder"):
                confirm_profile(
                    vault,
                    profile=profile,
                    council_adapter=adapter,
                    human_reviewed=True,
                )

    def test_profile_outside_vault_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            vault.mkdir()
            profile, adapter = _create_vault(vault)
            outside = root / "outside.yaml"
            outside.write_text(PROFILE_TEXT, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Vault内"):
                confirm_profile(
                    vault,
                    profile=outside,
                    council_adapter=adapter,
                    human_reviewed=True,
                )
            self.assertTrue(profile.is_file())

    def test_changed_profile_invalidates_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            confirm_profile(
                vault,
                profile=profile,
                council_adapter=adapter,
                human_reviewed=True,
            )
            profile.write_text(PROFILE_TEXT + "\n# changed\n", encoding="utf-8")
            report = build_status(vault)
            self.assertEqual("invalid", report["profile"]["state"])
            self.assertIn(
                "profile_confirmation_invalid",
                [item["code"] for item in report["warnings"]],
            )

    def test_symlink_replacement_invalidates_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            confirm_profile(
                vault,
                profile=profile,
                council_adapter=adapter,
                human_reviewed=True,
            )
            replacement = profile.with_name("replacement.yaml")
            replacement.write_text(PROFILE_TEXT, encoding="utf-8")
            profile.unlink()
            profile.symlink_to(replacement)
            report = build_status(vault)
            self.assertEqual("invalid", report["profile"]["state"])
            profile_check = next(
                item
                for item in report["profile"]["artifact_checks"]
                if item["kind"] == "councilor_profile"
            )
            self.assertIn("symlink", profile_check["detail"])

    def test_cli_confirm_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            profile, adapter = _create_vault(vault)
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "profile",
                        "confirm",
                        "--vault",
                        str(vault),
                        "--profile",
                        str(profile),
                        "--council-adapter",
                        str(adapter),
                        "--confirm-human-reviewed",
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(0, exit_code, stderr.getvalue())
            self.assertEqual("confirmed", json.loads(stdout.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
