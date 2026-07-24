"""Tests for minutes ingestion dry-run behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.minutes_db import ingest


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_fetches_body_or_creates_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "minutes.db"
            adapter = SimpleNamespace(
                list_meetings=lambda limit: [
                    {
                        "meeting_id": "meeting_1",
                        "source_url": "https://example.invalid/meeting.pdf",
                    }
                ],
                discovery_candidates=[
                    {
                        "source_url": "https://example.invalid/meeting.pdf",
                        "reason": "selected",
                    }
                ],
                fetch_meeting=lambda _: self.fail(
                    "dry-run must not fetch meeting bodies"
                ),
            )
            args = SimpleNamespace(
                adapter="static",
                config="config.json",
                url=None,
                db=str(database),
                limit=10,
                cache_dir=None,
                dry_run=True,
            )
            with patch("modules.minutes_db.ingest._make_adapter", return_value=adapter):
                result = ingest.ingest(args)
            self.assertEqual("dry_run", result["status"])
            self.assertFalse(result["database_created"])
            self.assertEqual(0, result["meeting_bodies_fetched"])
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
