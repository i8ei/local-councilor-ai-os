"""Synthetic tests for minutes publication-family detection."""

from __future__ import annotations

import unittest

from lcaios.http import CacheTier
from lcaios.tests.http_fakes import FakeHttpClient, make_fetch_result
from modules.minutes_db import detect


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
                    detect.detect_url(
                        url,
                        client=FakeHttpClient({}),
                        fetch_page=False,
                    )["verdict"],
                    expected,
                )

    def test_does_not_guess_kaigiroku_tenant(self) -> None:
        verdict = detect.detect_url(
            "https://example.test/tenant/imagined/",
            client=FakeHttpClient({}),
            fetch_page=False,
        )
        self.assertEqual(verdict["verdict"], "unknown")

    def test_detects_vendor_link_on_official_page(self) -> None:
        url = "https://official.example.test/council/"
        page = make_fetch_result(
            url,
            (
                '<a href="https://ssp.kaigiroku.net/tenant/observed/'
                'MinuteBrowse.html">minutes</a>'
            ),
        )
        client = FakeHttpClient({url: page})
        verdict = detect.detect_url(url, client=client)
        self.assertEqual(verdict["verdict"], "kaigiroku_net")
        self.assertEqual([(url, CacheTier.INDEX)], client.calls)
        self.assertEqual(
            verdict["evidence"][0]["matched_url"],
            "https://ssp.kaigiroku.net/tenant/observed/MinuteBrowse.html",
        )

    def test_detects_static_document_link(self) -> None:
        url = "https://official.example.test/council/"
        page = make_fetch_result(
            url,
            '<a href="docs/opaque-42.pdf">令和6年第1回定例会会議録</a>',
        )
        client = FakeHttpClient({url: page})
        verdict = detect.detect_url(url, client=client)
        self.assertEqual(verdict["verdict"], "static_candidate")
        self.assertEqual([(url, CacheTier.INDEX)], client.calls)
        self.assertEqual(
            verdict["evidence"][0]["matched_url"],
            "https://official.example.test/council/docs/opaque-42.pdf",
        )


if __name__ == "__main__":
    unittest.main()
