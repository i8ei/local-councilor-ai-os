"""Tests for source registry and local document preflight."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bootstrap.cli.local_documents import (
    diagnose_index,
)
from bootstrap.cli.local_documents import (
    main as local_documents_main,
)
from bootstrap.cli.sources import DEFAULT_REGISTRY, load_registry


class SourceRegistryTests(unittest.TestCase):
    def test_runtime_registry_contains_estat_and_jgrants_boundaries(self) -> None:
        registry = load_registry(DEFAULT_REGISTRY)
        sources = registry["sources"]
        self.assertIn("estat-api-v3", sources)
        self.assertIn("jgrants-public-api", sources)
        self.assertEqual(
            "live_with_cache",
            sources["jgrants-public-api"]["persistence"]["default"],
        )
        self.assertIn(
            "not_sufficient_for",
            sources["jgrants-public-api"]["use_boundary"],
        )
        self.assertIn("soumu-municipal-fiscal-overview", sources)
        for source in sources.values():
            self.assertGreater(
                source["freshness"]["recommended_check_interval_days"],
                0,
            )
            self.assertTrue(source["freshness"]["check_method"])

    def test_invalid_registry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_text('{"sources":{"broken":{}}}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "title"):
                load_registry(path)


class LocalDocumentDiagnosisTests(unittest.TestCase):
    def test_diagnosis_lists_candidates_without_downloading_documents(self) -> None:
        result = diagnose_index(
            index_url="https://example.go.jp/finance/",
            final_url="https://example.go.jp/finance/index.html",
            html="""
                <a href="files/r8-budget.pdf">令和8年度 当初予算書</a>
                <a href="/files/r7-settlement.xlsx">令和7年度 決算書</a>
                <a href="/about.html">財政課について</a>
            """,
            fetched_at="2026-07-24T00:00:00Z",
            content_hash="sha256:test",
        )
        self.assertEqual("needs_confirmation", result["status"])
        self.assertEqual(2, result["candidate_count"])
        self.assertEqual(0, result["documents_downloaded"])
        self.assertFalse(result["database_created"])
        self.assertEqual(
            ["later", "sample", "targeted", "full"],
            [item["id"] for item in result["next_options"]],
        )

    def test_index_heading_classifies_year_only_document_links(self) -> None:
        result = diagnose_index(
            index_url="https://example.go.jp/finance/settlement/",
            final_url="https://example.go.jp/finance/settlement/index.html",
            html="""
                <title>決算 | Example City</title>
                <h1>決算</h1>
                <a href="files/r6.pdf">令和6年度</a>
                <a href="/about.html">財政課について</a>
            """,
            fetched_at="2026-07-24T00:00:00Z",
            content_hash="sha256:test",
        )
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(["settlement"], result["candidates"][0]["kinds"])
        self.assertEqual(
            "official_index_context_and_document_extension",
            result["candidates"][0]["reason"],
        )

    def test_navigation_keywords_do_not_classify_unrelated_page(self) -> None:
        result = diagnose_index(
            index_url="https://example.go.jp/finance/contact/",
            final_url="https://example.go.jp/finance/contact/index.html",
            html="""
                <title>財政課へのお問い合わせ | Example City</title>
                <h1>財政課へのお問い合わせ</h1>
                <a href="/finance/budget/">予算</a>
                <a href="/finance/settlement/">決算</a>
                <a href="files/contact-guide.pdf">問い合わせ案内</a>
            """,
            fetched_at="2026-07-24T00:00:00Z",
            content_hash="sha256:test",
        )
        self.assertEqual(0, result["candidate_count"])

    def test_duplicate_icon_link_uses_later_human_readable_title(self) -> None:
        result = diagnose_index(
            index_url="https://example.go.jp/finance/settlement/",
            final_url="https://example.go.jp/finance/settlement/index.html",
            html="""
                <title>決算 | Example City</title>
                <a href="files/r6.pdf"><img src="pdf.png"></a>
                <a href="files/r6.pdf">令和6年度決算カード</a>
            """,
            fetched_at="2026-07-24T00:00:00Z",
            content_hash="sha256:test",
        )
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(
            "令和6年度決算カード",
            result["candidates"][0]["title"],
        )
        self.assertEqual(
            "official_index_keyword_and_document_extension",
            result["candidates"][0]["reason"],
        )

    def test_cli_fetches_only_the_index_url(self) -> None:
        fetched = SimpleNamespace(
            final_url="https://example.go.jp/finance/",
            content_type="text/html",
            fetched_at="2026-07-24T00:00:00Z",
            sha256="abc",
            text=lambda: '<a href="budget.pdf">予算書</a>',
        )
        client = SimpleNamespace(fetch=lambda url: fetched)
        output = io.StringIO()
        with (
            patch(
                "bootstrap.cli.local_documents.HttpClient",
                return_value=client,
            ) as client_class,
            redirect_stdout(output),
        ):
            status = local_documents_main(
                [
                    "diagnose",
                    "--index-url",
                    "https://example.go.jp/finance/",
                ]
            )
        self.assertEqual(0, status)
        client_class.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(0, result["documents_downloaded"])
        self.assertEqual(
            "https://example.go.jp/finance/budget.pdf",
            result["candidates"][0]["url"],
        )

    def test_sample_downloads_selected_candidate_without_creating_database(
        self,
    ) -> None:
        index = SimpleNamespace(
            final_url="https://example.go.jp/finance/",
            content_type="text/html",
            fetched_at="2026-07-24T00:00:00Z",
            sha256="index",
            text=lambda: (
                '<a href="budget.pdf">予算書</a>'
                '<a href="settlement.xlsx">決算書</a>'
            ),
        )
        document = SimpleNamespace(
            final_url="https://example.go.jp/finance/budget.pdf",
            content_type="application/pdf",
            fetched_at="2026-07-24T00:01:00Z",
            body=b"%PDF-1.7 fixture",
            from_cache=False,
        )

        class FakeClient:
            def fetch(self, url: str, **kwargs: object) -> object:
                return index if url.endswith("/finance/") else document

            def retrieval_report(self) -> dict[str, int]:
                return {"live_request_count": 2}

        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with (
                patch(
                    "bootstrap.cli.local_documents.HttpClient",
                    return_value=FakeClient(),
                ),
                patch(
                    "bootstrap.cli.local_documents._pdf_text_quality",
                    return_value={
                        "status": "unavailable",
                        "tool": "pdftotext",
                    },
                ),
                redirect_stdout(output),
            ):
                status = local_documents_main(
                    [
                        "sample",
                        "--index-url",
                        "https://example.go.jp/finance/",
                        "--output-dir",
                        temporary,
                        "--candidate",
                        "1",
                    ]
                )
            result = json.loads(output.getvalue())
            self.assertEqual(0, status)
            self.assertEqual("sampled", result["status"])
            self.assertEqual(1, result["documents_downloaded"])
            self.assertFalse(result["database_created"])
            self.assertEqual(
                "passed",
                result["samples"][0]["format_check"]["status"],
            )
            self.assertTrue(Path(result["samples"][0]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
