"""Tests for the unified doctor recommendation and CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.doctor import recommend_next


def _diagnosis(mode: str = "integrate", **capabilities: str) -> dict:
    return {
        "recommended_mode": mode,
        "agent": "codex",
        "capabilities": {
            name: {"status": status}
            for name, status in capabilities.items()
        },
    }


def _status(**requirements: str) -> dict:
    base = {
        "foundation_ready": "ready",
        "scaffold_ready": "ready",
        "profile_ready": "ready",
        "tier1_data_ready": "ready",
    }
    base.update(requirements)
    return {"requirements": base}


class DoctorRecommendationTests(unittest.TestCase):
    def test_full_mode_is_handoff(self) -> None:
        result = recommend_next(_diagnosis(mode="full"), _status())
        self.assertEqual(3, result["exit_code"])
        self.assertIn("onboarding", result["next_command"])

    def test_client_selection_needs_confirmation(self) -> None:
        result = recommend_next(
            _diagnosis(ai_client_selection="needs-confirmation"),
            _status(),
        )
        self.assertEqual(2, result["exit_code"])
        self.assertIn("--agent", result["next_command"])

    def test_missing_tier1_recommends_bootstrap(self) -> None:
        result = recommend_next(
            _diagnosis(),
            _status(tier1_data_ready="not_configured"),
        )
        self.assertEqual(2, result["exit_code"])
        self.assertIn("bootstrap.cli", result["next_command"])

    def test_due_tier1_recommends_refresh(self) -> None:
        result = recommend_next(
            _diagnosis(),
            _status(tier1_data_ready="due"),
        )
        self.assertEqual(2, result["exit_code"])
        self.assertIn("bootstrap.cli", result["next_command"])

    def test_invalid_tier1_recommends_verify_database(self) -> None:
        result = recommend_next(
            _diagnosis(),
            _status(tier1_data_ready="invalid"),
        )
        self.assertIn("verify database", result["next_command"])

    def test_profile_incomplete_blocks_ready(self) -> None:
        result = recommend_next(
            _diagnosis(),
            _status(profile_ready="incomplete"),
        )
        self.assertEqual(2, result["exit_code"])
        self.assertIn("profile", result["next_command"])

    def test_all_ready_is_zero(self) -> None:
        result = recommend_next(_diagnosis(), _status())
        self.assertEqual(0, result["exit_code"])

    def test_cli_doctor_runs_read_only_and_returns_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / ".obsidian").mkdir()
            (vault / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
            before = sorted(vault.rglob("*"))
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["doctor", "--vault", str(vault), "--format", "json"]
                )
            payload = json.loads(output.getvalue())
            self.assertIn("recommendation", payload)
            self.assertIn(exit_code, (0, 2, 3))
            self.assertEqual(exit_code, payload["recommendation"]["exit_code"])
            self.assertEqual(before, sorted(vault.rglob("*")))


if __name__ == "__main__":
    unittest.main()

