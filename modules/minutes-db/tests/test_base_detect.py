"""Synthetic tests for polite fetching and vendor detection."""

from __future__ import annotations

import email.message
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import detect
from adapters import base


class _FakeResponse:
    def __init__(
        self,
        url: str,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
        location: str | None = None,
    ) -> None:
        self.status = status
        self._url = url
        self._body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        if location:
            self.headers["Location"] = location

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class PoliteFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        base._ROBOTS_CACHE.clear()
        base._LAST_REQUEST_AT = None
        self.interval_patch = mock.patch.object(base, "MIN_REQUEST_INTERVAL_SECONDS", 0)
        self.interval_patch.start()

    def tearDown(self) -> None:
        self.interval_patch.stop()

    def test_fetches_robots_then_page_and_reuses_cache(self) -> None:
        robots = _FakeResponse(
            "https://example.test/robots.txt",
            b"User-agent: *\nDisallow: /private\n",
        )
        page_body = '<meta charset="Shift_JIS"><p>会議録</p>'.encode("cp932")
        page = _FakeResponse(
            "https://example.test/minutes.html",
            page_body,
            content_type="text/html",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(base._OPENER, "open", side_effect=[robots, page]) as opened:
                first = base.polite_fetch(
                    "https://example.test/minutes.html",
                    cache_dir=temporary,
                )
                second = base.polite_fetch(
                    "https://example.test/minutes.html",
                    cache_dir=temporary,
                )

            self.assertEqual(opened.call_count, 2)
            for call in opened.call_args_list:
                request = call.args[0]
                self.assertEqual(request.get_header("User-agent"), base.USER_AGENT)
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(first.encoding, "cp932")
            self.assertIn("会議録", first.text())
            self.assertEqual(first.sha256, second.sha256)
            self.assertTrue(first.fetched_at.endswith("Z"))
            self.assertTrue(first.cache_path.is_file())
            metadata_files = list(Path(temporary).glob("*.json"))
            self.assertEqual(len(metadata_files), 2)
            metadata = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in metadata_files
                if json.loads(path.read_text(encoding="utf-8"))["requested_url"].endswith(
                    "/minutes.html"
                )
            ][0]
            self.assertTrue(metadata["requests"][0]["fetched_at"].endswith("Z"))

    def test_denied_path_is_not_fetched(self) -> None:
        robots = _FakeResponse(
            "https://example.test/robots.txt",
            b"User-agent: *\nDisallow: /private\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(base._OPENER, "open", return_value=robots) as opened:
                with self.assertRaises(base.RobotsDeniedError):
                    base.polite_fetch(
                        "https://example.test/private/minutes.html",
                        cache_dir=temporary,
                    )
            self.assertEqual(opened.call_count, 1)


class DetectTests(unittest.TestCase):
    def test_known_url_families_do_not_need_a_fetch(self) -> None:
        cases = [
            ("https://ssp.kaigiroku.net/tenant/observed/", "kaigiroku_net"),
            ("https://ssp.kaigiroku.net/", "kaigiroku_net"),
            ("https://town.gijiroku.com/voices/index.html", "voices"),
            ("https://smart.discussvision.net/smart/", "discuss"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    detect.detect_url(url, fetch_page=False)["verdict"],
                    expected,
                )

    def test_does_not_guess_kaigiroku_tenant(self) -> None:
        verdict = detect.detect_url(
            "https://example.test/tenant/imagined/",
            fetch_page=False,
        )
        self.assertEqual(verdict["verdict"], "unknown")

    def test_detects_vendor_link_on_official_page(self) -> None:
        page = base.FetchResult(
            url="https://official.example.test/council/",
            final_url="https://official.example.test/council/",
            body=(
                b'<a href="https://ssp.kaigiroku.net/tenant/observed/'
                b'MinuteBrowse.html">minutes</a>'
            ),
            fetched_at="2026-07-23T00:00:00Z",
            content_type="text/html",
            encoding="utf-8",
            cache_path=Path("/tmp/synthetic.body"),
            sha256="synthetic",
            from_cache=True,
        )
        with mock.patch.object(detect, "polite_fetch", return_value=page):
            verdict = detect.detect_url("https://official.example.test/council/")
        self.assertEqual(verdict["verdict"], "kaigiroku_net")
        self.assertEqual(
            verdict["evidence"][0]["matched_url"],
            "https://ssp.kaigiroku.net/tenant/observed/MinuteBrowse.html",
        )

    def test_detects_static_document_link(self) -> None:
        page = base.FetchResult(
            url="https://official.example.test/council/",
            final_url="https://official.example.test/council/",
            body='<a href="docs/opaque-42.pdf">令和6年第1回定例会会議録</a>'.encode(),
            fetched_at="2026-07-23T00:00:00Z",
            content_type="text/html",
            encoding="utf-8",
            cache_path=Path("/tmp/synthetic.body"),
            sha256="synthetic",
            from_cache=True,
        )
        with mock.patch.object(detect, "polite_fetch", return_value=page):
            verdict = detect.detect_url("https://official.example.test/council/")
        self.assertEqual(verdict["verdict"], "static_candidate")
        self.assertEqual(
            verdict["evidence"][0]["matched_url"],
            "https://official.example.test/council/docs/opaque-42.pdf",
        )


if __name__ == "__main__":
    unittest.main()
