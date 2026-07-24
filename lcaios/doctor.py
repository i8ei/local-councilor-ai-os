"""One read-only entry that combines diagnosis, readiness, and next step."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .status import build_status


def _capability(diagnosis: dict[str, Any], name: str) -> str:
    return str(
        diagnosis.get("capabilities", {}).get(name, {}).get("status", "")
    )


def recommend_next(
    diagnosis: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    """Return the single next command from diagnosis and readiness state.

    Exit codes follow the CLI convention: 0 ready, 2 action needed, 3 handoff
    to the foundation setup repository.
    """

    handoff_keys = (
        "ai_client_selection",
        "vault_registered",
        "instruction_file",
        "obsidian_cli",
    )
    if diagnosis.get("recommended_mode") == "full" or any(
        _capability(diagnosis, key) == "handoff_required"
        for key in handoff_keys
    ):
        return {
            "next_command": "python3 -m onboarding diagnose --vault <vault>",
            "reason": (
                "Obsidian・AI基盤が未整備。claude-obsidian-setupで基盤を整えてから戻る"
            ),
            "exit_code": 3,
        }
    if _capability(diagnosis, "ai_client_selection") == "needs-confirmation":
        return {
            "next_command": (
                "python3 -m onboarding diagnose --vault <vault> "
                "--agent <claude|codex>"
            ),
            "reason": "Claude CodeとCodexの両方が利用可能。今回使うものを一度選ぶ",
            "exit_code": 2,
        }

    requirements = status.get("requirements", {})
    tier1 = requirements.get("tier1_data_ready")

    if requirements.get("foundation_ready") != "ready":
        return {
            "next_command": "python3 -m onboarding plan --vault <vault>",
            "reason": "Vault・AIガイド・Obsidian CLIの導入前提を確認する",
            "exit_code": 2,
        }
    if requirements.get("scaffold_ready") != "ready":
        return {
            "next_command": (
                "python3 -m onboarding plan --vault <vault> "
                "then scaffold --accept-plan-sha256 <hash>"
            ),
            "reason": "棚・MOC・workflow・templateのscaffoldが未検証",
            "exit_code": 2,
        }
    if tier1 in ("not_configured", "unavailable"):
        return {
            "next_command": (
                "python3 -m bootstrap.cli '<自治体名>' "
                "--manifest-dir <vault>/.local-councilor-ai-os/runs/bootstrap"
            ),
            "reason": "Tier 1の人口・財政データ基盤が未構築",
            "exit_code": 2,
        }
    if tier1 == "invalid":
        return {
            "next_command": "python3 -m lcaios verify database --file <municipality.db>",
            "reason": "Tier 1 databaseの整合・schema・artifactに問題がある",
            "exit_code": 2,
        }
    if tier1 in ("due", "stale"):
        return {
            "next_command": (
                "python3 -m bootstrap.cli '<自治体名>' "
                "--manifest-dir <vault>/.local-councilor-ai-os/runs/bootstrap"
            ),
            "reason": "Tier 1データが再確認期限を超過、またはより新しい公表期がある",
            "exit_code": 2,
        }
    if requirements.get("profile_ready") != "ready":
        return {
            "next_command": (
                "profiles/councilor-profile.yaml と council-adapter.md を本人確認で完成"
            ),
            "reason": "議員本人・議会固有運用のprofileが未完了",
            "exit_code": 2,
        }
    return {
        "next_command": (
            "python3 -m lcaios verify output --file <公開予定稿> "
            "（案件ごとの公開前検査）"
        ),
        "reason": "導入・データ・profileは整い、案件運用と公開前検査に進める",
        "exit_code": 0,
    }


def run_doctor(
    vault: str | Path,
    *,
    agent: str = "auto",
    probe_obsidian: bool = False,
    bootstrap_database: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble a read-only doctor report without modifying anything."""

    from onboarding.core import OnboardingError, diagnose_environment

    vault_path = Path(vault).expanduser()
    try:
        diagnosis = diagnose_environment(
            vault_path,
            agent=agent,
            probe_obsidian=probe_obsidian,
        )
        diagnosis_error = None
    except OnboardingError as error:
        diagnosis = {
            "recommended_mode": "full",
            "capabilities": {},
            "agent": agent,
        }
        diagnosis_error = str(error)

    status = build_status(vault_path, bootstrap_database=bootstrap_database)
    recommendation = recommend_next(diagnosis, status)
    return {
        "schema_version": 1,
        "product": "local-councilor-ai-os",
        "generated_at": status["generated_at"],
        "vault": status["vault"]["path"],
        "diagnosis": {
            "recommended_mode": diagnosis.get("recommended_mode"),
            "agent": diagnosis.get("agent"),
            "error": diagnosis_error,
        },
        "requirements": status.get("requirements", {}),
        "bootstrap": {
            "state": status["bootstrap"]["state"],
            "freshness": status["bootstrap"]["freshness"]["state"],
        },
        "recommendation": recommendation,
        "warnings": status.get("warnings", []),
    }

