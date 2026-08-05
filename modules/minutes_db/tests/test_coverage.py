"""Synthetic tests for post-ingest minutes coverage diagnostics."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.minutes_db import coverage, ingest
from modules.minutes_db.coverage import diagnose_coverage, validate_coverage_config
from modules.minutes_db.ingest import ensure_schema, store_meeting


def fictional_document(
    number: int,
    *,
    year: int,
    role: str | None = "議員",
    with_segments: bool = True,
) -> dict[str, object]:
    source_url = f"https://example.invalid/minutes/document-{number}.pdf"
    session_url = f"https://example.invalid/minutes/session-{number}/"
    speeches = []
    if with_segments:
        speeches = [
            {
                "seq": 1,
                "speaker": "架空太郎" if role == "議員" else role,
                "speaker_role": role,
                "text": "架空町の施策について述べます。",
                "locator": "page:1",
            }
        ]
    return {
        "meeting": {
            "council_name": "架空町議会",
            "meeting_name": f"令和架空年第{number}回定例会 第1日",
            "session": f"架空会期{number}",
            "date": f"{year:04d}-06-01",
            "source_url": source_url,
            "adapter": "fictional",
            "fetched_at": "2026-08-01T00:00:00Z",
        },
        "speeches": speeches,
        "provenance": {
            "discovered_from": session_url,
            "resolved_url": source_url,
            "fetched_at": "2026-08-01T00:00:00Z",
            "media_type": "application/pdf",
            "content_sha256": str(number).zfill(64),
            "transform": {"extractor": "fictional"},
            "status": "extracted" if with_segments else "pdf_no_text",
            "issues": [] if with_segments else ["架空の抽出失敗"],
        },
    }


class CoverageDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        ensure_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_flags_one_document_per_session_failure_shape(self) -> None:
        years = [2024] * 5 + [2025] + [2026] * 5
        candidates = []
        for number, year in enumerate(years, start=1):
            store_meeting(
                self.connection,
                fictional_document(
                    number,
                    year=year,
                    role="町長" if number == 1 else "議員",
                    with_segments=number != len(years),
                ),
            )
            candidates.append(
                {
                    "session_key": (
                        f"https://example.invalid/minutes/session-{number}/"
                    ),
                    "candidate_document_links": 5,
                }
            )
        self.connection.commit()

        result = diagnose_coverage(
            self.connection,
            candidate_sessions=candidates,
        )

        self.assertEqual("advisory", result["status"])
        self.assertTrue(result["attention_required"])
        presence = result["presiding_officer_presence"]
        self.assertEqual(10, presence["meetings_without_presiding_officer_speeches"])
        self.assertAlmostEqual(10 / 11, presence["zero_presence_share"])
        self.assertTrue(presence["flagged"])

        yearly = result["document_counts_by_year"]
        self.assertEqual(5.0, yearly["median_documents"])
        self.assertEqual([2025], yearly["flagged_years"])

        session_coverage = result["session_document_coverage"]
        self.assertEqual(11, len(session_coverage["flagged_sessions"]))
        self.assertTrue(
            all(
                item["ingested_documents"] == 1
                and item["candidate_document_links"] == 5
                for item in session_coverage["sessions"]
            )
        )

        zero_segments = result["zero_segment_documents"]
        self.assertEqual(1, zero_segments["count"])
        self.assertTrue(zero_segments["flagged"])

    def test_year_gaps_are_reported_as_zero_document_years(self) -> None:
        for number, year in enumerate((2024, 2024, 2026, 2026), start=1):
            store_meeting(
                self.connection,
                fictional_document(number, year=year, role="町長"),
            )

        yearly = diagnose_coverage(self.connection)["document_counts_by_year"]

        self.assertEqual(
            [
                {"year": 2024, "documents": 2, "flagged": False},
                {"year": 2025, "documents": 0, "flagged": True},
                {"year": 2026, "documents": 2, "flagged": False},
            ],
            yearly["counts"],
        )

    def test_presiding_officer_titles_are_configurable(self) -> None:
        store_meeting(
            self.connection,
            fictional_document(1, year=2026, role="統括官"),
        )
        default_result = diagnose_coverage(self.connection)
        custom_result = diagnose_coverage(
            self.connection,
            options=validate_coverage_config(
                {"presiding_officer_titles": ["統括官"]}
            ),
        )

        self.assertTrue(default_result["presiding_officer_presence"]["flagged"])
        self.assertFalse(custom_result["presiding_officer_presence"]["flagged"])

    def test_session_coverage_is_omitted_without_adapter_candidates(self) -> None:
        store_meeting(
            self.connection,
            fictional_document(1, year=2026, role="町長"),
        )

        result = diagnose_coverage(self.connection)

        self.assertNotIn("session_document_coverage", result)

    def test_named_thresholds_are_overridable(self) -> None:
        store_meeting(
            self.connection,
            fictional_document(1, year=2026, role="町長"),
        )
        store_meeting(
            self.connection,
            fictional_document(2, year=2026, with_segments=False),
        )
        options = validate_coverage_config(
            {
                "presiding_officer_absence_share_threshold": 0.75,
                "low_year_count_ratio": 0.25,
                "minimum_session_document_coverage_ratio": 0.5,
                "zero_segment_document_threshold": 1,
            }
        )

        result = diagnose_coverage(
            self.connection,
            options=options,
            candidate_sessions=[
                {
                    "session_key": "https://example.invalid/minutes/session-1/",
                    "candidate_document_links": 2,
                }
            ],
        )

        self.assertFalse(result["attention_required"])
        self.assertEqual(
            0.25,
            result["document_counts_by_year"]["low_year_count_ratio"],
        )

    def test_standalone_cli_reads_coverage_settings_from_adapter_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "minutes.db"
            config = root / "adapter.json"
            connection = sqlite3.connect(database)
            ensure_schema(connection)
            store_meeting(
                connection,
                fictional_document(1, year=2026, role="統括官"),
            )
            connection.commit()
            connection.close()
            config.write_text(
                json.dumps(
                    {"coverage": {"presiding_officer_titles": ["統括官"]}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = coverage.main(
                    ["--db", str(database), "--config", str(config)]
                )

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["presiding_officer_presence"]["flagged"])

    def test_ingest_manifest_keeps_advisories_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "adapter.json"
            config.write_text("{}", encoding="utf-8")
            database = root / "minutes.db"
            manifests = root / "manifests"
            adapter = SimpleNamespace(
                config={"coverage": validate_coverage_config()},
                list_meetings=lambda limit: [{"meeting_id": "fictional-1"}],
                fetch_meeting=lambda _ref: fictional_document(1, year=2026),
                coverage_candidate_sessions=None,
            )
            stdout = io.StringIO()
            with (
                patch("modules.minutes_db.ingest._make_adapter", return_value=adapter),
                redirect_stdout(stdout),
            ):
                exit_code = ingest.main(
                    [
                        "--adapter",
                        "static",
                        "--config",
                        str(config),
                        "--db",
                        str(database),
                        "--manifest-dir",
                        str(manifests),
                        "--run-id",
                        "coverage-advisory",
                    ]
                )

            manifest = json.loads(
                (manifests / "coverage-advisory.json").read_text(encoding="utf-8")
            )

        self.assertEqual(0, exit_code)
        self.assertEqual("succeeded", manifest["status"])
        diagnostics = manifest["coverage"]["diagnostics"]
        self.assertEqual("advisory", diagnostics["status"])
        self.assertTrue(diagnostics["attention_required"])
        self.assertTrue(
            diagnostics["presiding_officer_presence"]["flagged"]
        )


if __name__ == "__main__":
    unittest.main()
