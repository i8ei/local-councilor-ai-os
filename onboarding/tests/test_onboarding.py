"""Tests for read-only diagnosis, planning, safe application, and verification."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from onboarding.cli import build_parser
from onboarding.core import (
    ROOT_MOC,
    OnboardingError,
    _probe_obsidian,
    apply_scaffold,
    build_plan,
    diagnose_environment,
    public_plan,
    verify_scaffold,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
OBSIDIAN_READY = {
    "status": "reuse",
    "detail": "test",
    "command": "/mock/obsidian",
    "vault_name": "Test Vault",
}


def _create_base(vault: Path, instruction: str = "AGENTS.md") -> None:
    (vault / ".obsidian").mkdir()
    (vault / instruction).write_text("# Rules\n", encoding="utf-8")


def _which_with(*available: str):
    available_set = set(available)
    return lambda name: f"/mock/{name}" if name in available_set else None


class OnboardingTests(unittest.TestCase):
    def test_cli_defaults_to_auto_client_selection(self) -> None:
        args = build_parser().parse_args(
            ["diagnose", "--vault", "/tmp/example-vault"]
        )
        self.assertEqual("auto", args.agent)

    def test_auto_requires_one_choice_when_claude_and_codex_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            (vault / "CLAUDE.md").write_text("# Claude Rules\n", encoding="utf-8")
            with (
                patch(
                    "onboarding.core.shutil.which",
                    side_effect=_which_with("obsidian", "claude", "codex"),
                ),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault, agent="auto")
            selection = result["capabilities"]["ai_client_selection"]
            self.assertEqual("needs-confirmation", selection["status"])
            self.assertEqual("auto", result["agent"])
            self.assertEqual("diagnose", result["recommended_mode"])
            rendered_steps = "\n".join(result["next_steps"])
            self.assertIn("--agent codex", rendered_steps)
            self.assertIn("--agent claude", rendered_steps)
            plan = build_plan(result, mode="diagnose", repo_root=REPO_ROOT)
            self.assertEqual("blocked", plan["status"])

    def test_auto_selects_only_available_claude_client(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault, instruction="CLAUDE.md")
            with (
                patch(
                    "onboarding.core.shutil.which",
                    side_effect=_which_with("obsidian", "claude"),
                ),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault, agent="auto")
            self.assertEqual("claude", result["agent"])
            self.assertEqual("integrate", result["recommended_mode"])
            self.assertEqual(
                "CLAUDE.md",
                result["capabilities"]["instruction_file"]["active"],
            )
            self.assertIn(
                "Claude Code",
                result["permission_preflight"]["ai_workspace_scope"]["detail"],
            )

    def test_missing_codex_guide_has_actionable_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / ".obsidian").mkdir()
            with (
                patch(
                    "onboarding.core.shutil.which",
                    side_effect=_which_with("obsidian", "codex"),
                ),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault, agent="codex")
            steps = "\n".join(result["next_steps"])
            self.assertIn("AGENTS.md", steps)
            self.assertIn("writable roots", steps)
            self.assertIn("--agent codex", steps)
            self.assertFalse((vault / "AGENTS.md").exists())

    def test_missing_claude_guide_has_actionable_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / ".obsidian").mkdir()
            with (
                patch(
                    "onboarding.core.shutil.which",
                    side_effect=_which_with("obsidian", "claude"),
                ),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault, agent="claude")
            steps = "\n".join(result["next_steps"])
            self.assertIn("CLAUDE.md", steps)
            self.assertIn("読み書き許可", steps)
            self.assertIn("--agent claude", steps)
            self.assertFalse((vault / "CLAUDE.md").exists())

    def test_obsidian_probe_requires_exact_vault_and_targeted_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary).resolve()
            responses = [
                SimpleNamespace(
                    returncode=0,
                    stdout=f"Test Vault\t{vault}\n",
                    stderr="",
                ),
                SimpleNamespace(returncode=0, stdout=f"{vault}\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="0\n", stderr=""),
            ]
            with patch("onboarding.core.subprocess.run", side_effect=responses) as run:
                result = _probe_obsidian("/mock/obsidian", vault)
            self.assertEqual("reuse", result["status"])
            self.assertEqual("Test Vault", result["vault_name"])
            self.assertEqual(3, run.call_count)
            self.assertIn("vault=Test Vault", run.call_args_list[2].args[0])
            self.assertIn("query=__local_councilor_ai_os_probe__", run.call_args_list[2].args[0])

    def test_missing_vault_is_read_only_and_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "missing"
            result = diagnose_environment(vault, probe_obsidian=False)
            self.assertTrue(result["read_only"])
            self.assertEqual(
                "unavailable",
                result["capabilities"]["vault_directory"]["status"],
            )
            self.assertFalse(vault.exists())

    def test_diagnosis_never_serializes_estat_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            with (
                patch.dict(
                    os.environ,
                    {"ESTAT_APPID": "must-not-appear"},
                    clear=False,
                ),
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault)
            self.assertNotIn("must-not-appear", repr(result))
            self.assertEqual("reuse", result["credentials"]["ESTAT_APPID"]["status"])

    def test_codex_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            (vault / "AGENTS.override.md").write_text("# Override\n", encoding="utf-8")
            with (
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                result = diagnose_environment(vault, agent="codex")
            instruction = result["capabilities"]["instruction_file"]
            self.assertEqual("AGENTS.override.md", instruction["active"])

    def test_plan_blocks_conflicting_existing_file_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            existing = vault / ROOT_MOC
            existing.write_text("existing\n", encoding="utf-8")
            with (
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                diagnosis = diagnose_environment(vault)
            plan = build_plan(
                diagnosis,
                mode="integrate",
                features=("core",),
                repo_root=REPO_ROOT,
            )
            root_action = next(
                item for item in plan["actions"] if item["target"].endswith(ROOT_MOC)
            )
            self.assertEqual("conflict", root_action["status"])
            self.assertEqual("blocked", plan["status"])
            self.assertEqual("existing\n", existing.read_text(encoding="utf-8"))
            self.assertNotIn("_planned_files", public_plan(plan))

    def test_apply_requires_exact_plan_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            with (
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                diagnosis = diagnose_environment(vault)
                plan = build_plan(
                    diagnosis,
                    mode="integrate",
                    features=("core",),
                    repo_root=REPO_ROOT,
                )
                with self.assertRaisesRegex(OnboardingError, "ハッシュ"):
                    apply_scaffold(plan, accepted_plan_sha256="wrong")

    def test_apply_creates_scaffold_and_verify_checks_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            with (
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                diagnosis = diagnose_environment(vault)
                plan = build_plan(
                    diagnosis,
                    mode="integrate",
                    features=("core", "templates", "workflows"),
                    repo_root=REPO_ROOT,
                )
                result = apply_scaffold(
                    plan,
                    accepted_plan_sha256=plan["plan_sha256"],
                )
                verification = verify_scaffold(result["manifest"])
            self.assertEqual("incomplete", result["status"])
            self.assertEqual("complete", result["scaffold_status"])
            self.assertTrue((vault / "一般質問" / "一般質問MOC.md").is_file())
            self.assertTrue(
                (
                    vault
                    / "テンプレート"
                    / "local-councilor-ai-os"
                    / "question-draft.md"
                ).is_file()
            )
            self.assertTrue(Path(result["manifest"]).is_file())
            self.assertTrue(
                (
                    vault
                    / "地方議員AI運用OS"
                    / "workflows"
                    / "01-start-case.md"
                ).is_file()
            )
            self.assertEqual("verified", verification["scaffold_status"])
            self.assertEqual([], verification["failures"])

    def test_apply_stops_when_target_changes_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            _create_base(vault)
            with (
                patch("onboarding.core.shutil.which", return_value="/mock/command"),
                patch("onboarding.core._probe_obsidian", return_value=OBSIDIAN_READY),
            ):
                diagnosis = diagnose_environment(vault)
                plan = build_plan(
                    diagnosis,
                    mode="integrate",
                    features=("core",),
                    repo_root=REPO_ROOT,
                )
                (vault / ROOT_MOC).write_text("changed\n", encoding="utf-8")
                with self.assertRaisesRegex(OnboardingError, "再診断"):
                    apply_scaffold(
                        plan,
                        accepted_plan_sha256=plan["plan_sha256"],
                    )
            self.assertEqual("changed\n", (vault / ROOT_MOC).read_text(encoding="utf-8"))

    def test_full_mode_is_handoff_and_cannot_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            diagnosis = diagnose_environment(vault, probe_obsidian=False)
            plan = build_plan(
                diagnosis,
                mode="full",
                repo_root=REPO_ROOT,
            )
            self.assertEqual("handoff_required", plan["status"])
            with self.assertRaisesRegex(OnboardingError, "integrate"):
                apply_scaffold(
                    plan,
                    accepted_plan_sha256=plan["plan_sha256"],
                )


if __name__ == "__main__":
    unittest.main()
