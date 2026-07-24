"""Tests for cache controls and retrieval reporting."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrap.cli.http import (
    HttpClient,
    OfflineCacheMiss,
    _RawResponse,
    _cache_files,
)


class HttpRetrievalReportingTests(unittest.TestCase):
    def test_cached_fetch_is_reported_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            key = "fixture:data"
            body = b"fixture"
            body_path, metadata_path = _cache_files(cache, key)
            body_path.write_bytes(body)
            metadata_path.write_text(
                json.dumps(
                    {
                        "requested_url": "https://example.test/data",
                        "final_url": "https://example.test/data",
                        "status": 200,
                        "fetched_at": "2026-07-24T00:00:00Z",
                        "content_type": "text/plain",
                        "encoding": "utf-8",
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            client = HttpClient(cache, offline=True)
            result = client.fetch(
                "https://example.test/data",
                cache_key=key,
            )
            report = client.retrieval_report()
            self.assertTrue(result.from_cache)
            self.assertEqual(1, report["cache_hit_count"])
            self.assertEqual(0, report["live_request_count"])
            self.assertFalse(report["latestness_rechecked_this_run"])
            self.assertEqual("cache_hit", report["accesses"][0]["status"])

    def test_offline_miss_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, offline=True)
            with self.assertRaises(OfflineCacheMiss):
                client.fetch(
                    "https://example.test/missing",
                    cache_key="fixture:missing",
                )
            report = client.retrieval_report()
            self.assertEqual(1, report["cache_miss_count"])
            self.assertEqual("cache_miss", report["accesses"][0]["status"])

    def test_offline_and_refresh_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "同時"):
            HttpClient("/tmp/unused-lcaios-cache", offline=True, refresh=True)

    def test_robots_http_400_is_unavailable_under_rfc_9309(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary)
            client._request_once = lambda _: _RawResponse(  # type: ignore[method-assign]
                url="https://example.test/robots.txt",
                status=400,
                body=b"",
                headers={"Content-Type": "text/plain"},
                fetched_at="2026-07-24T00:00:00Z",
            )
            parser = client._robots_parser("https://example.test/data")
            self.assertTrue(
                parser.can_fetch(
                    "local-councilor-ai-os",
                    "https://example.test/data",
                )
            )

    def test_robots_redirect_can_cross_authority_under_rfc_9309(self) -> None:
        responses = iter(
            [
                _RawResponse(
                    url="https://old.example.test/robots.txt",
                    status=301,
                    body=b"",
                    headers={
                        "Location": "https://new.example.test/robots.txt"
                    },
                    fetched_at="2026-07-24T00:00:00Z",
                ),
                _RawResponse(
                    url="https://new.example.test/robots.txt",
                    status=200,
                    body=b"User-agent: *\nDisallow: /private\n",
                    headers={"Content-Type": "text/plain"},
                    fetched_at="2026-07-24T00:00:01Z",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary)
            with patch.object(client, "_request_once", side_effect=lambda _: next(responses)):
                parser = client._robots_parser(
                    "https://old.example.test/public"
                )
        self.assertTrue(
            parser.can_fetch(
                "local-councilor-ai-os",
                "https://old.example.test/public",
            )
        )
        self.assertFalse(
            parser.can_fetch(
                "local-councilor-ai-os",
                "https://old.example.test/private",
            )
        )


if __name__ == "__main__":
    unittest.main()
