"""Regression tests over the real municipal profile dataset.

Covers whole-inventory invariants that schema unit tests (synthetic data)
cannot: every checked-in profile must validate, and known quality anomalies
must not grow beyond their current bounds.

Run from repo root: python3 -m unittest source_profiles.tests.test_inventory_quality
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from source_profiles.schema import validate_profile  # type: ignore[import-not-found]

MUNI_DIR = Path(__file__).resolve().parents[2] / "municipalities"

# An entry whose notes say "Observed ..." was BLOCKED *after* finding the
# entrance URL (robots.txt or page-limit guard). Those are promotable leftovers:
# if the count grows, re-verification stopped working for new municipalities.
MAX_OBSERVED_BUT_BLOCKED = 76


def _profiles() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(MUNI_DIR.glob("*/*.json"))
    ]


class InventoryQualityTests(unittest.TestCase):
    def test_every_profile_validates(self) -> None:
        errors: list[str] = []
        for path, data in _profiles():
            for err in validate_profile(data):
                errors.append(f"{path.relative_to(MUNI_DIR)}: {err}")
        self.assertEqual(errors, [], "schema errors in real dataset:\n" + "\n".join(errors[:20]))

    def test_budget_settlement_ready_is_never_machine_granted(self) -> None:
        """`ready` for budget/settlement means records were actually ingested,
        which only a human can attest: the repo ships no generic extractor for
        these kinds. A verifier stamping `ready` here is the drift that made
        2,206 doc-structure verdicts indistinguishable from adapter-extracted
        ready (2026-08-28)."""
        offenders: list[str] = []
        for path, data in _profiles():
            for kind in ("budget", "settlement"):
                entry = data.get("sources", {}).get(kind) or {}
                if entry.get("status") != "ready":
                    continue
                verified_by = str(entry.get("verified_by") or "")
                if verified_by.startswith("verify") or verified_by.endswith("scout"):
                    offenders.append(f"{path.name} {kind}: verified_by={verified_by!r}")
        self.assertEqual(
            [],
            offenders,
            "budget/settlement ready must follow a human-attested ingestion; "
            "machine verification tops out at document_confirmed:\n"
            + "\n".join(offenders[:10]),
        )

    def test_document_confirmed_only_on_budget_settlement(self) -> None:
        offenders: list[str] = []
        for path, data in _profiles():
            for kind, entry in data.get("sources", {}).items():
                if entry.get("status") == "document_confirmed" and kind not in (
                    "budget",
                    "settlement",
                ):
                    offenders.append(f"{path.name} {kind}")
        self.assertEqual([], offenders, "\n".join(offenders[:10]))

    def test_observed_but_blocked_does_not_grow(self) -> None:
        observed: list[str] = []
        for path, data in _profiles():
            for kind, entry in data.get("sources", {}).items():
                if entry.get("status") == "blocked" and "Observed" in (entry.get("notes") or ""):
                    observed.append(f"{path.name} {kind}: {entry.get('notes')}")
        self.assertLessEqual(
            len(observed),
            MAX_OBSERVED_BUT_BLOCKED,
            "observed-but-blocked grew; re-verify these instead of adding more:\n"
            + "\n".join(observed[:10]),
        )


if __name__ == "__main__":
    unittest.main()
