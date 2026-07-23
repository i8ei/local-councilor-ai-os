"""Synthetic tests for the config-driven static minutes adapter."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from adapters.base import FetchResult  # noqa: E402
from adapters.static_html import StaticHtmlAdapter, segment_speeches  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
INDEX_URL = "https://example.invalid/council/index.html"
MEETING_URL = "https://example.invalid/council/minutes/meeting-1.html"
PDF_URL = "https://example.invalid/council/files/meeting-2.pdf"


def make_result(
    url: str,
    body: bytes,
    *,
    content_type: str,
    cache_path: Path,
) -> FetchResult:
    cache_path.write_bytes(body)
    return FetchResult(
        url=url,
        final_url=url,
        body=body,
        fetched_at="2026-07-23T10:00:00+09:00",
        content_type=content_type,
        encoding="utf-8",
        cache_path=cache_path,
        sha256=hashlib.sha256(body).hexdigest(),
        from_cache=False,
    )


class StaticHtmlAdapterTest(unittest.TestCase):
    def test_discovers_filtered_html_and_normalizes_speakers(self) -> None:
        index_body = (FIXTURES / "static_index.html").read_bytes()
        meeting_body = (FIXTURES / "static_meeting.html").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                INDEX_URL: make_result(
                    INDEX_URL,
                    index_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "index.cache",
                ),
                MEETING_URL: make_result(
                    MEETING_URL,
                    meeting_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "meeting.cache",
                ),
            }
            adapter = StaticHtmlAdapter(
                {
                    "index_url": INDEX_URL,
                    "link_include_regex": r"meeting-\d+",
                    "link_exclude_regex": r"summary",
                    "pdf": False,
                    "council_name": "架空町議会",
                },
                cache_dir=root / "cache",
            )
            with patch(
                "adapters.static_html.polite_fetch",
                side_effect=lambda url, **_: responses[url],
            ):
                references = adapter.list_meetings()
                self.assertEqual(1, len(references))
                document = adapter.fetch_meeting(references[0]["meeting_id"])

        self.assertEqual("架空町議会", document["meeting"]["council_name"])
        self.assertEqual("2026-07-23", document["meeting"]["date"])
        self.assertEqual("extracted", document["provenance"]["status"])
        self.assertEqual("佐藤一郎", document["speeches"][1]["speaker"])
        self.assertEqual("議長", document["speeches"][1]["speaker_role"])
        self.assertEqual("山田花子", document["speeches"][2]["speaker"])
        self.assertEqual("議員", document["speeches"][2]["speaker_role"])
        self.assertNotIn(
            "script text",
            "\n".join(speech["text"] for speech in document["speeches"]),
        )

    def test_pdf_without_pdftotext_is_cached_with_clear_status(self) -> None:
        pdf_body = (FIXTURES / "static_fake.pdf").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_html = (
                '<a href="files/meeting-2.pdf">令和8年第3回定例会</a>'
            ).encode()
            responses = {
                INDEX_URL: make_result(
                    INDEX_URL,
                    index_html,
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                PDF_URL: make_result(
                    PDF_URL,
                    pdf_body,
                    content_type="application/pdf",
                    cache_path=root / "meeting.pdf",
                ),
            }
            adapter = StaticHtmlAdapter(
                {
                    "index_url": INDEX_URL,
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                }
            )
            with (
                patch(
                    "adapters.static_html.polite_fetch",
                    side_effect=lambda url, **_: responses[url],
                ),
                patch("adapters.static_html.shutil.which", return_value=None),
            ):
                reference = adapter.list_meetings()[0]
                document = adapter.fetch_meeting(reference["meeting_id"])

        self.assertEqual([], document["speeches"])
        self.assertEqual(
            "pdf_cached_pdftotext_unavailable",
            document["provenance"]["status"],
        )
        self.assertIn("pdftotext", document["provenance"]["issues"][0])
        self.assertTrue(document["provenance"]["cache_path"].endswith("meeting.pdf"))

    def test_pdf_uses_pdftotext_when_available(self) -> None:
        pdf_body = (FIXTURES / "static_fake.pdf").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fetched = make_result(
                PDF_URL,
                pdf_body,
                content_type="application/pdf",
                cache_path=root / "meeting.pdf",
            )
            adapter = StaticHtmlAdapter({"index_url": INDEX_URL, "pdf": True})
            completed = SimpleNamespace(
                returncode=0,
                stdout="○議長　開会します。\f二ページ目です。".encode(),
                stderr=b"",
            )
            with (
                patch(
                    "adapters.static_html.shutil.which",
                    return_value="/usr/bin/pdftotext",
                ),
                patch(
                    "adapters.static_html.subprocess.run",
                    return_value=completed,
                ) as run,
            ):
                text, status, issues = adapter._extract_pdf(fetched)

        self.assertEqual("extracted", status)
        self.assertEqual([], issues)
        self.assertIn("\f", text)
        self.assertEqual("-layout", run.call_args.args[0][1])

    def test_paragraph_and_page_fallbacks(self) -> None:
        paragraphs = segment_speeches("第一段落\n第二段落")
        self.assertEqual(["paragraph:1", "paragraph:2"], [
            item["locator"] for item in paragraphs
        ])
        pages = segment_speeches("一ページ目\f二ページ目")
        self.assertEqual(["page:1", "page:2"], [item["locator"] for item in pages])

    def test_zero_limit_performs_no_fetch(self) -> None:
        adapter = StaticHtmlAdapter({"index_url": INDEX_URL, "pdf": False})
        with patch("adapters.static_html.polite_fetch") as fetch:
            self.assertEqual([], adapter.list_meetings(limit=0))
        fetch.assert_not_called()

    def test_from_config_rejects_non_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text("index_url: invalid", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                StaticHtmlAdapter.from_config(path)


if __name__ == "__main__":
    unittest.main()
