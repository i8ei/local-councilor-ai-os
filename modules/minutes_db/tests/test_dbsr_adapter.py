"""Synthetic tests for the dbsr.jp minutes adapter."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from lcaios.http import CacheTier
from lcaios.tests.http_fakes import FakeHttpClient
from modules.minutes_db.adapters.base import FetchResult
from modules.minutes_db.adapters.dbsr import DbsrAdapter

KANZAKI_INDEX = "https://www.city.kanzaki.saga.dbsr.jp/index.php/"
KAMIMINE_INDEX = "http://www.town.kamimine.saga.dbsr.jp/index.php/"
MIYAKI_INDEX = "https://www.town.miyaki.saga.dbsr.jp/index.php/"
MEETING_1_URL = "https://www.city.kanzaki.saga.dbsr.jp/index.php/1001"
MEETING_2_URL = "https://www.city.kanzaki.saga.dbsr.jp/index.php/1002"
PDF_URL = "https://www.city.kanzaki.saga.dbsr.jp/files/test.pdf"

MEETING_HTML = """<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>令和7年6月3日 定例会 会議録</title></head>
<body>
<h1>令和7年6月3日 定例会</h1>
<p>○議長（佐藤一郎君）　これより会議を開きます。</p>
<p>日程第一を議題とします。</p>
<p>◯山田花子君　質問します。</p>
<p>〔山田花子君登壇〕</p>
<p>地域交通について伺います。</p>
<script>doNotInclude("script")</script>
</body>
</html>"""


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


class DbsrAdapterTest(unittest.TestCase):
    def test_rejects_non_dbsr_index(self) -> None:
        with self.assertRaises(ValueError):
            DbsrAdapter("https://example.invalid/index.php/", client=FakeHttpClient({}))
        with self.assertRaises(ValueError):
            DbsrAdapter(
                "https://www.city.kanzaki.saga.dbsr.jp/other.html",
                client=FakeHttpClient({}),
            )

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(ValueError):
            DbsrAdapter(
                "ftp://www.city.kanzaki.saga.dbsr.jp/index.php/",
                client=FakeHttpClient({}),
            )

    def test_validates_all_three_observed_indices(self) -> None:
        for url in [KANZAKI_INDEX, KAMIMINE_INDEX, MIYAKI_INDEX]:
            with self.subTest(url=url):
                adapter = DbsrAdapter(url, client=FakeHttpClient({}))
                self.assertIn(url, adapter.index_urls)

    def test_discovers_minutes_and_excludes_noise(self) -> None:
        index_html = (
            f'<a href="{MEETING_1_URL}">令和7年6月定例会 会議録</a>'
            f'<a href="{MEETING_2_URL}">令和7年9月定例会 会議録</a>'
            f'<a href="{MEETING_1_URL}">重複リンク</a>'
            '<a href="https://www.city.kanzaki.saga.dbsr.jp/index.php/summary">会議概要（除外）</a>'
            '<a href="https://external.example.invalid/other.pdf">外部</a>'
            '<a href="https://www.city.kanzaki.saga.dbsr.jp/other.html">お知らせ</a>'
        ).encode()
        meeting_body = MEETING_HTML.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = {
                KANZAKI_INDEX: make_result(
                    KANZAKI_INDEX,
                    index_html,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "index.cache",
                ),
                MEETING_1_URL: make_result(
                    MEETING_1_URL,
                    meeting_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "m1.cache",
                ),
                MEETING_2_URL: make_result(
                    MEETING_2_URL,
                    meeting_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "m2.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
            refs = adapter.list_meetings()
            decisions = adapter.discovery_candidates

        self.assertEqual(2, len(refs))
        self.assertEqual(
            {MEETING_1_URL, MEETING_2_URL}, {r["source_url"] for r in refs}
        )
        reasons = {d["reason"] for d in decisions}
        self.assertIn("selected", reasons)
        self.assertIn("duplicate", reasons)
        self.assertIn("excluded_by_regex", reasons)
        self.assertIn("host_drift", reasons)
        # Only index fetch so far
        self.assertEqual([(KANZAKI_INDEX, CacheTier.INDEX)], client.calls)

    def test_limit_and_council_name(self) -> None:
        index_html = (
            f'<a href="{MEETING_1_URL}">令和7年6月定例会 会議録</a>'
            f'<a href="{MEETING_2_URL}">令和7年9月定例会 会議録</a>'
        ).encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = {
                KANZAKI_INDEX: make_result(
                    KANZAKI_INDEX,
                    index_html,
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
            refs = adapter.list_meetings(limit=1)
        self.assertEqual(1, len(refs))
        self.assertEqual(MEETING_1_URL, refs[0]["source_url"])

    def test_zero_limit_no_fetch(self) -> None:
        client = FakeHttpClient({})
        adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
        self.assertEqual([], adapter.list_meetings(limit=0))
        self.assertEqual([], client.calls)

    def test_council_name_falls_back_to_index_title(self) -> None:
        # With no explicit council_name and no baked-in map, the label is
        # derived from the observed index document title.
        index_html = (
            "<head><title>神埼市議会 会議録検索</title></head>"
            f'<a href="{MEETING_1_URL}">令和7年6月定例会 会議録</a>'
        ).encode()
        meeting_body = MEETING_HTML.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = {
                KANZAKI_INDEX: make_result(
                    KANZAKI_INDEX,
                    index_html,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "index.cache",
                ),
                MEETING_1_URL: make_result(
                    MEETING_1_URL,
                    meeting_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "meeting.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
            refs = adapter.list_meetings()
            doc = adapter.fetch_meeting(refs[0]["meeting_id"])
        self.assertEqual("神埼市議会 会議録検索", doc["meeting"]["council_name"])

    def test_fetch_meeting_normalizes_speakers_and_provenance(self) -> None:
        index_html = f'<a href="{MEETING_1_URL}">令和7年6月定例会 会議録</a>'.encode()
        meeting_body = MEETING_HTML.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = {
                KANZAKI_INDEX: make_result(
                    KANZAKI_INDEX,
                    index_html,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "index.cache",
                ),
                MEETING_1_URL: make_result(
                    MEETING_1_URL,
                    meeting_body,
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "meeting.cache",
                ),
            }
            client = FakeHttpClient(responses)
            # council_name is supplied by the caller (ingest, from the source
            # profile). The adapter itself bakes in no per-municipality names.
            adapter = DbsrAdapter(
                KANZAKI_INDEX, client=client, council_name="神埼市議会"
            )
            refs = adapter.list_meetings()
            doc = adapter.fetch_meeting(refs[0]["meeting_id"])

        self.assertEqual("神埼市議会", doc["meeting"]["council_name"])
        self.assertIn("令和7年6月", doc["meeting"]["meeting_name"])
        # date inference from title
        self.assertIsNotNone(doc["meeting"]["date"])
        self.assertEqual("dbsr", doc["meeting"]["adapter"])
        self.assertEqual("extracted", doc["provenance"]["status"])
        self.assertEqual([], doc["provenance"]["issues"])
        self.assertIn("sha256:", doc["provenance"]["content_hash"])
        self.assertEqual(MEETING_1_URL, doc["provenance"]["resolved_url"])
        self.assertEqual(KANZAKI_INDEX, doc["provenance"]["discovered_from"])
        # speeches segmented
        speeches = doc["speeches"]
        self.assertGreaterEqual(len(speeches), 2)
        # first after preamble should have speaker 佐藤一郎 / 議長
        speaker_roles = [(s["speaker"], s["speaker_role"]) for s in speeches]
        self.assertIn(("佐藤一郎", "議長"), speaker_roles)
        self.assertIn(("山田花子", "議員"), speaker_roles)
        self.assertEqual(
            [(KANZAKI_INDEX, CacheTier.INDEX), (MEETING_1_URL, CacheTier.DOCUMENT)],
            client.calls,
        )
        self.assertNotIn("script", " ".join(s["text"] for s in speeches))

    def test_fetch_meeting_pdf_without_extraction(self) -> None:
        index_html = f'<a href="{PDF_URL}">令和7年6月定例会 会議録</a>'.encode()
        pdf_body = b"%PDF-1.4 fake"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = {
                KANZAKI_INDEX: make_result(
                    KANZAKI_INDEX,
                    index_html,
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                PDF_URL: make_result(
                    PDF_URL,
                    pdf_body,
                    content_type="application/pdf",
                    cache_path=root / "test.pdf",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
            refs = adapter.list_meetings()
            self.assertEqual(1, len(refs))
            doc = adapter.fetch_meeting(refs[0]["meeting_id"])
        self.assertEqual([], doc["speeches"])
        self.assertEqual(
            "pdf_cached_pdftotext_unavailable", doc["provenance"]["status"]
        )
        self.assertTrue(doc["provenance"]["cache_path"].endswith("test.pdf"))

    def test_host_drift_on_fetch_raises(self) -> None:
        # Simulate redirect drift: final_url differs host
        index_html = f'<a href="{MEETING_1_URL}">令和7年6月定例会 会議録</a>'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_res = make_result(
                KANZAKI_INDEX,
                index_html,
                content_type="text/html",
                cache_path=root / "index.cache",
            )
            # Drift response: final_url is external
            drift_body = MEETING_HTML.encode()
            cache_path = root / "drift.cache"
            cache_path.write_bytes(drift_body)
            drift_res = FetchResult(
                url=MEETING_1_URL,
                final_url="https://external.example.invalid/drift.html",
                body=drift_body,
                fetched_at="2026-07-23T10:00:00+09:00",
                content_type="text/html",
                encoding="utf-8",
                cache_path=cache_path,
                sha256=hashlib.sha256(drift_body).hexdigest(),
                from_cache=False,
            )
            client = FakeHttpClient(
                {KANZAKI_INDEX: index_res, MEETING_1_URL: drift_res}
            )
            adapter = DbsrAdapter(KANZAKI_INDEX, client=client)
            refs = adapter.list_meetings()
            with self.assertRaises(ValueError):
                adapter.fetch_meeting(refs[0]["meeting_id"])

    def test_detect_capabilities(self) -> None:
        adapter = DbsrAdapter(KANZAKI_INDEX, client=FakeHttpClient({}))
        caps = adapter.detect_capabilities()
        self.assertEqual("dbsr", caps["adapter"])
        self.assertIn("observed_index_only", caps["meeting_discovery"])

    def test_coverage_candidate_sessions_is_none(self) -> None:
        adapter = DbsrAdapter(KANZAKI_INDEX, client=FakeHttpClient({}))
        self.assertIsNone(adapter.coverage_candidate_sessions)


if __name__ == "__main__":
    unittest.main()
