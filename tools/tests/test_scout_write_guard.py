"""The scout must not overwrite findings a person or `verify` established."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tools.scout_profiles import (
    _apply_candidate,
    _may_write,
    scout_municipality,
)


def _entry(status, **extra):
    entry = {
        "status": status,
        "adapter": None,
        "verified_at": None,
        "verified_by": None,
        "evidence": [],
        "notes": "",
    }
    entry.update(extra)
    return entry


class MayWriteTests(unittest.TestCase):
    def test_never_evaluated_entries_are_writable(self):
        self.assertTrue(_may_write(_entry("not_evaluated"), overwrite=False))

    def test_existing_finding_needs_overwrite(self):
        entry = _entry(
            "needs_review",
            notes="Observed g_reiki at https://example.test/ (label: 例規集)",
        )
        self.assertFalse(_may_write(entry, overwrite=False))
        self.assertTrue(_may_write(entry, overwrite=True))

    def test_verification_outcomes_are_protected_even_with_overwrite(self):
        for status in ("ready", "unsupported"):
            with self.subTest(status=status):
                self.assertFalse(_may_write(_entry(status), overwrite=True))

    def test_verified_stamp_protects_any_status(self):
        entry = _entry(
            "needs_review",
            verified_at="2026-08-21T00:00:00Z",
            verified_by="verify --live",
        )
        self.assertFalse(_may_write(entry, overwrite=True))

    def test_hand_written_note_protects_the_entry(self):
        entry = _entry(
            "needs_review",
            notes="静的HTMLでは議会会議録へのリンクが確認できず、要ブラウザ確認",
        )
        self.assertFalse(_may_write(entry, overwrite=True))

    def test_scout_authored_notes_stay_redoable_with_overwrite(self):
        for note in (
            "Observed g_reiki at https://example.test/ (label: 例規集)",
            "Verified vendor minutes probe at https://example.test/ for 例町",
            "No budget entrance found within page limit",
            "official_home robots.txt denied",
            "",
        ):
            with self.subTest(note=note):
                entry = _entry("needs_review", notes=note)
                self.assertFalse(_may_write(entry, overwrite=False))
                self.assertTrue(_may_write(entry, overwrite=True))

    def test_missing_entry_is_writable(self):
        self.assertTrue(_may_write(None, overwrite=False))


class SagaFixtureTests(unittest.TestCase):
    """The hand-curated prefecture is protected by provenance, not by name."""

    def test_no_41_saga_entry_is_writable_even_with_overwrite(self):
        root = Path(__file__).resolve().parents[2]
        saga = sorted((root / "source_profiles" / "municipalities" / "41-saga").glob("*.json"))
        self.assertEqual(len(saga), 20)
        for path in saga:
            sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
            for kind, entry in sources.items():
                with self.subTest(profile=path.name, kind=kind):
                    self.assertFalse(_may_write(entry, overwrite=True))


class ApplyCandidateTests(unittest.TestCase):
    def test_protected_entry_survives_a_candidate(self):
        sources = {"minutes": _entry("ready", notes="verified by hand")}
        _apply_candidate(
            sources, "minutes", {"status": "needs_review"}, "council minutes", False
        )
        self.assertEqual(sources["minutes"]["status"], "ready")
        self.assertEqual(sources["minutes"]["notes"], "verified by hand")

    def test_unevaluated_entry_without_candidate_becomes_not_found(self):
        sources = {"budget": _entry("not_evaluated")}
        _apply_candidate(sources, "budget", None, "budget", False)
        self.assertEqual(sources["budget"]["status"], "not_found")


class ScoutSkipsFullyProtectedProfileTests(unittest.TestCase):
    def _profile(self, statuses):
        return {
            "schema_version": 1,
            "area_code_5": "41441",
            "prefecture": "佐賀県",
            "municipality": "太良町",
            "official_home_url": "http://www.town.tara.lg.jp/",
            "sources": {k: _entry(v) for k, v in statuses.items()},
        }

    def test_no_request_is_made_when_every_kind_is_protected(self):
        profile = self._profile(
            {
                "minutes": "ready",
                "regulations": "ready",
                "budget": "unsupported",
                "settlement": "ready",
            }
        )
        client = MagicMock()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "41441-tara.json"
            path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = path.read_text(encoding="utf-8")
            scout_municipality(path, client)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        client.fetch.assert_not_called()

    def test_one_writable_kind_is_enough_to_start_crawling(self):
        # `unsupported` protects the entry without the extra fields the
        # schema demands of `ready`, keeping the fixture minimal.
        profile = self._profile(
            {
                "minutes": "unsupported",
                "regulations": "unsupported",
                "budget": "not_evaluated",
                "settlement": "unsupported",
            }
        )
        client = MagicMock()
        client.fetch.side_effect = RuntimeError("stop after the first fetch")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "41441-tara.json"
            path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            scout_municipality(path, client)
        client.fetch.assert_called()


if __name__ == "__main__":
    unittest.main()
