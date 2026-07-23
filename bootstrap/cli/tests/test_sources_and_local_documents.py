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

from bootstrap.cli.local_documents import diagnose_index, main as local_documents_main
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


if __name__ == "__main__":
    unittest.main()
