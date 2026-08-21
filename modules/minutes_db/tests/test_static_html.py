"""Synthetic tests for the config-driven static minutes adapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lcaios.http import CacheTier
from lcaios.tests.http_fakes import FakeHttpClient
from modules.minutes_db.adapters.base import FetchResult
from modules.minutes_db.adapters.static_html import StaticHtmlAdapter, segment_speeches

MODULE_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
PRESETS = MODULE_DIR / "presets"
INDEX_URL = "https://example.invalid/council/index.html"
MEETING_URL = "https://example.invalid/council/minutes/meeting-1.html"
PDF_URL = "https://example.invalid/council/files/meeting-2.pdf"
YEAR_INDEX_URL = "https://www.example.jp/gikai/minutes/reiwa8/"
SESSION_ONE_URL = f"{YEAR_INDEX_URL}session-1.html"
SESSION_TWO_URL = f"{YEAR_INDEX_URL}session-2.html"


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
    def test_all_shipped_presets_are_valid_adapter_configs(self) -> None:
        expected = {
            "html-minutes.json",
            "pdf-index-all.json",
            "pdf-per-session.json",
            "year-index-two-level.json",
        }
        preset_paths = sorted(PRESETS.glob("*.json"))
        self.assertEqual(expected, {path.name for path in preset_paths})

        for path in preset_paths:
            with self.subTest(preset=path.name):
                raw = json.loads(path.read_text(encoding="utf-8"))
                comment = raw["_comment"]
                self.assertIsInstance(comment["layout"], str)
                self.assertEqual(2, len(comment["replace"]))
                self.assertIn("日程", comment["common_exclude_patterns"])
                self.assertTrue(comment["after_limit_2"])

                adapter = StaticHtmlAdapter.from_config(
                    path, client=FakeHttpClient({})
                )
                self.assertEqual(
                    [raw["index_url"]], adapter.config["index_url"]
                )
                self.assertTrue(
                    raw["index_url"].startswith("https://www.example.jp/")
                )
                self.assertEqual("例示町議会", adapter.config["council_name"])
                self.assertIn("町長", adapter.config["coverage"]["presiding_officer_titles"])

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
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": INDEX_URL,
                    "link_include_regex": r"meeting-\d+",
                    "link_exclude_regex": r"summary",
                    "pdf": False,
                    "council_name": "架空町議会",
                },
                client=client,
            )
            references = adapter.list_meetings()
            self.assertEqual(1, len(references))
            decisions = adapter.discovery_candidates
            document = adapter.fetch_meeting(references[0]["meeting_id"])

        self.assertEqual("架空町議会", document["meeting"]["council_name"])
        self.assertEqual(
            [
                (INDEX_URL, CacheTier.INDEX),
                (MEETING_URL, CacheTier.DOCUMENT),
            ],
            client.calls,
        )
        self.assertIn("selected", {item["reason"] for item in decisions})
        self.assertIn("excluded_by_regex", {item["reason"] for item in decisions})
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
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": INDEX_URL,
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                },
                client=client,
            )
            with (
                patch("modules.minutes_db.adapters.static_html.shutil.which", return_value=None),
            ):
                reference = adapter.list_meetings()[0]
                document = adapter.fetch_meeting(reference["meeting_id"])

        self.assertEqual(
            [
                (INDEX_URL, CacheTier.INDEX),
                (PDF_URL, CacheTier.DOCUMENT),
            ],
            client.calls,
        )
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
            adapter = StaticHtmlAdapter(
                {"index_url": INDEX_URL, "pdf": True},
                client=FakeHttpClient({}),
            )
            completed = SimpleNamespace(
                returncode=0,
                stdout="○議長　開会します。\f二ページ目です。".encode(),
                stderr=b"",
            )
            with (
                patch(
                    "modules.minutes_db.adapters.static_html.shutil.which",
                    return_value="/usr/bin/pdftotext",
                ),
                patch(
                    "modules.minutes_db.adapters.static_html.subprocess.run",
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
        client = FakeHttpClient({})
        adapter = StaticHtmlAdapter(
            {"index_url": INDEX_URL, "pdf": False},
            client=client,
        )
        self.assertEqual([], adapter.list_meetings(limit=0))
        self.assertEqual([], client.calls)

    def test_matches_decoded_pdf_filename_when_label_is_only_file_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            encoded_url = (
                "https://example.invalid/files/"
                "%E4%B8%80%E8%88%AC%E8%B3%AA%E5%95%8F.pdf"
            )
            index = make_result(
                INDEX_URL,
                f'<a href="{encoded_url}">（PDF455KB）</a>'.encode(),
                content_type="text/html",
                cache_path=root / "index.cache",
            )
            client = FakeHttpClient({INDEX_URL: index})
            adapter = StaticHtmlAdapter(
                {
                    "index_url": INDEX_URL,
                    "link_include_regex": "一般質問",
                    "pdf": True,
                },
                client=client,
            )
            references = adapter.list_meetings()
        self.assertEqual([(INDEX_URL, CacheTier.INDEX)], client.calls)
        self.assertEqual(1, len(references))
        self.assertEqual("一般質問.pdf", references[0]["meeting_name"])
        self.assertEqual("selected", adapter.discovery_candidates[0]["reason"])

    def test_follows_one_level_with_excludes_and_global_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                YEAR_INDEX_URL: make_result(
                    YEAR_INDEX_URL,
                    (FIXTURES / "static_year_index.html").read_bytes(),
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "year.cache",
                ),
                SESSION_ONE_URL: make_result(
                    SESSION_ONE_URL,
                    (FIXTURES / "static_session_1.html").read_bytes(),
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "session-1.cache",
                ),
                SESSION_TWO_URL: make_result(
                    SESSION_TWO_URL,
                    (FIXTURES / "static_session_2.html").read_bytes(),
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "session-2.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": YEAR_INDEX_URL,
                    "follow_link_regex": r"session-\d+\.html$|定例会|臨時会",
                    "link_include_regex": r"\.pdf$",
                    "link_exclude_regex": (
                        r"(?i)(summary|agenda|schedule|概要|日程|予定)"
                    ),
                    "pdf": True,
                },
                client=client,
            )
            references = adapter.list_meetings(limit=2)
            decisions = adapter.discovery_candidates
            coverage_candidates = adapter.coverage_candidate_sessions

        self.assertEqual(
            [
                f"{YEAR_INDEX_URL}pdf/day-1.pdf",
                f"{YEAR_INDEX_URL}pdf/day-2.pdf",
            ],
            [ref["source_url"] for ref in references],
        )
        self.assertEqual(
            [SESSION_ONE_URL, SESSION_TWO_URL],
            [ref["discovered_from"] for ref in references],
        )
        self.assertEqual(
            [YEAR_INDEX_URL, SESSION_ONE_URL, SESSION_TWO_URL],
            [url for url, _ in client.calls],
        )
        self.assertTrue(
            all(tier is CacheTier.INDEX for _, tier in client.calls)
        )
        source_urls = " ".join(ref["source_url"] for ref in references)
        self.assertNotIn("agenda.pdf", source_urls)
        self.assertNotIn("schedule.pdf", source_urls)
        self.assertIn("excluded_by_regex", {item["reason"] for item in decisions})
        self.assertEqual(
            [
                {
                    "session_key": SESSION_ONE_URL,
                    "candidate_document_links": 2,
                },
                {
                    "session_key": SESSION_TWO_URL,
                    "candidate_document_links": 2,
                },
            ],
            coverage_candidates,
        )

    def test_from_config_rejects_non_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text("index_url: invalid", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                StaticHtmlAdapter.from_config(
                    path, client=FakeHttpClient({})
                )


class StaticHtmlDepth2Test(unittest.TestCase):
    def test_depth_two_chain_lists_meeting(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        year_url = "https://example.invalid/council/year/2024.html"
        month_url = "https://example.invalid/council/year/2024/01.html"
        pdf_url = "https://example.invalid/council/year/2024/01/day1.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                index_url: make_result(
                    index_url,
                    b'<a href="https://example.invalid/council/year/2024.html">2024\xe5\xb9\xb4</a>',
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "index.cache",
                ),
                year_url: make_result(
                    year_url,
                    b'<a href="https://example.invalid/council/year/2024/01.html">2024-01</a>',
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "year.cache",
                ),
                month_url: make_result(
                    month_url,
                    f'<a href="{pdf_url}">\xe4\xbc\x9a\xe8\xad\xb0\xe9\x8c\xb2.pdf</a>'.encode(),
                    content_type="text/html; charset=utf-8",
                    cache_path=root / "month.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"2024",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                    "follow_max_depth": 2,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        self.assertEqual(1, len(refs))
        self.assertEqual(pdf_url, refs[0]["source_url"])
        self.assertEqual(month_url, refs[0]["discovered_from"])
        self.assertEqual(
            [index_url, year_url, month_url],
            [u for u, _ in client.calls],
        )

    def test_default_depth_one_does_not_reach_depth_two(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        year_url = "https://example.invalid/council/year/2024.html"
        month_url = "https://example.invalid/council/year/2024/01.html"
        pdf_url = "https://example.invalid/council/year/2024/01/day1.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                index_url: make_result(
                    index_url,
                    b'<a href="https://example.invalid/council/year/2024.html">2024</a>',
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                year_url: make_result(
                    year_url,
                    b'<a href="https://example.invalid/council/year/2024/01.html">2024-01</a>',
                    content_type="text/html",
                    cache_path=root / "year.cache",
                ),
                month_url: make_result(
                    month_url,
                    f'<a href="{pdf_url}">doc.pdf</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / "month.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"2024",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        self.assertEqual(0, len(refs))
        self.assertEqual([index_url, year_url], [u for u, _ in client.calls])
        self.assertNotIn(month_url, [u for u, _ in client.calls])

    def test_host_drift_not_followed(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        evil_url = "https://evil.example.com/council/year/2024.html"
        evil_month = "https://evil.example.com/council/year/2024/01.html"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                index_url: make_result(
                    index_url,
                    f'<a href="{evil_url}">2024</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                evil_url: make_result(
                    evil_url,
                    f'<a href="{evil_month}">2024-01</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / "evil.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"2024",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                    "follow_max_depth": 2,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        self.assertEqual(0, len(refs))
        self.assertEqual([index_url], [u for u, _ in client.calls])

    def test_pdf_link_not_followed(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        pdf_follow = "https://example.invalid/council/year/2024.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            responses = {
                index_url: make_result(
                    index_url,
                    f'<a href="{pdf_follow}">2024 pdf</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                pdf_follow: make_result(
                    pdf_follow,
                    b"%PDF-1.4 fake",
                    content_type="application/pdf",
                    cache_path=root / "pdf.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"2024",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                    "follow_max_depth": 2,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        # pdf_follow matches follow regex but must not be fetched as follow page
        self.assertEqual([index_url], [u for u, _ in client.calls])
        # but it is discovered as document on index itself (no follow needed)
        self.assertEqual(1, len(refs))
        self.assertEqual(pdf_follow, refs[0]["source_url"])

    def test_follow_max_pages_caps_globally(self) -> None:
        index_url = "https://example.invalid/council/index.html"
        year_urls = [
            f"https://example.invalid/council/year/202{i}.html" for i in range(4)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_html = "".join(
                f'<a href="{u}">202{i}</a>' for i, u in enumerate(year_urls)
            ).encode()
            responses: dict[str, FetchResult] = {
                index_url: make_result(
                    index_url,
                    index_html,
                    content_type="text/html",
                    cache_path=root / "index.cache",
                )
            }
            for i, u in enumerate(year_urls):
                pdf = f"https://example.invalid/council/year/202{i}/day.pdf"
                responses[u] = make_result(
                    u,
                    f'<a href="{pdf}">doc.pdf</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / f"year{i}.cache",
                )
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"202",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                    "follow_max_depth": 1,
                    "follow_max_pages": 2,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        self.assertEqual(2, len(refs))
        self.assertEqual(3, len(client.calls))  # index + 2 follow pages
        self.assertEqual(
            [index_url, year_urls[0], year_urls[1]],
            [u for u, _ in client.calls],
        )

    def test_level1_candidates_do_not_starve_level2(self) -> None:
        # Regression: many depth-1 candidates must not consume the whole
        # follow_max_pages budget; depth-2 pages stay reachable.
        index_url = "https://example.invalid/council/index.html"
        year_urls = [
            f"https://example.invalid/council/year/202{i}.html" for i in range(8)
        ]
        month_urls = [
            f"https://example.invalid/council/year/202{i}/03.html" for i in range(8)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_html = "".join(
                f'<a href="{u}">202{i}</a>' for i, u in enumerate(year_urls)
            ).encode()
            responses: dict[str, FetchResult] = {
                index_url: make_result(
                    index_url,
                    index_html,
                    content_type="text/html",
                    cache_path=root / "index.cache",
                )
            }
            for i, u in enumerate(year_urls):
                responses[u] = make_result(
                    u,
                    f'<a href="{month_urls[i]}">03</a>'.encode(),
                    content_type="text/html",
                    cache_path=root / f"year{i}.cache",
                )
            for i, m in enumerate(month_urls):
                responses[m] = make_result(
                    m,
                    b'<a href="https://example.invalid/council/year/2027/03/day.pdf">doc.pdf</a>',
                    content_type="text/html",
                    cache_path=root / f"month{i}.cache",
                )
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {
                    "index_url": index_url,
                    "follow_link_regex": r"20[0-9]{2}",
                    "link_include_regex": r"\.pdf$",
                    "pdf": True,
                    "follow_max_depth": 2,
                    "follow_max_pages": 8,
                },
                client=client,
            )
            refs = adapter.list_meetings()
        self.assertEqual(1, len(refs))
        fetched = [u for u, _ in client.calls]
        self.assertTrue(any(m in fetched for m in month_urls))
        self.assertLessEqual(len(fetched) - 1, 8)

    def test_follow_max_depth_validation(self) -> None:
        for bad in [0, 4, "2", True, 1.5, None]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "follow_max_depth"):
                    StaticHtmlAdapter(
                        {
                            "index_url": INDEX_URL,
                            "follow_max_depth": bad,  # type: ignore[typeddict-item]
                        },
                        client=FakeHttpClient({}),
                    )

    def test_follow_max_pages_validation(self) -> None:
        for bad in [0, -1, "2", True, 1.5]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "follow_max_pages"):
                    StaticHtmlAdapter(
                        {
                            "index_url": INDEX_URL,
                            "follow_max_pages": bad,  # type: ignore[typeddict-item]
                        },
                        client=FakeHttpClient({}),
                    )
        # None and missing are allowed (unbounded)
        for good in [None, 1, 3, 100]:
            with self.subTest(good=good):
                cfg: dict[str, object] = {"index_url": INDEX_URL}
                if good is not None:
                    cfg["follow_max_pages"] = good
                adapter = StaticHtmlAdapter(cfg, client=FakeHttpClient({}))  # type: ignore[arg-type]
                self.assertEqual(good, adapter.config["follow_max_pages"])

    def test_detect_capabilities_multi_level(self) -> None:
        client = FakeHttpClient({})
        a1 = StaticHtmlAdapter(
            {"index_url": INDEX_URL, "follow_link_regex": r"x"},
            client=client,
        )
        self.assertEqual(
            "configured_index_one_level", a1.detect_capabilities()["meeting_discovery"]
        )
        a2 = StaticHtmlAdapter(
            {"index_url": INDEX_URL, "follow_link_regex": r"x", "follow_max_depth": 2},
            client=client,
        )
        self.assertEqual(
            "configured_index_multi_level",
            a2.detect_capabilities()["meeting_discovery"],
        )
        a3 = StaticHtmlAdapter({"index_url": INDEX_URL}, client=client)
        self.assertEqual(
            "configured_index", a3.detect_capabilities()["meeting_discovery"]
        )


class StaticHtmlBSpeakerTest(unittest.TestCase):
    """湯沢町 HTML <B>話者マーク対応の回帰テスト."""

    def _fetch_with_html(self, html: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = html.encode("utf-8")
            responses = {
                INDEX_URL: make_result(
                    INDEX_URL,
                    b'<a href="https://example.invalid/council/minutes/b.html">link</a>',
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                "https://example.invalid/council/minutes/b.html": make_result(
                    "https://example.invalid/council/minutes/b.html",
                    body,
                    content_type="text/html",
                    cache_path=root / "meeting.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {"index_url": INDEX_URL, "pdf": False},
                client=client,
            )
            ref = adapter.list_meetings()[0]
            doc = adapter.fetch_meeting(ref["meeting_id"])
            return doc["speeches"]

    def test_b_speaker_is_segmented(self) -> None:
        html = (
            "<html><body>"
            "<B>議長</B><br>　皆さん、こんにちは。<br>"
            "<B>町長</B><br>　答弁します。<br>"
            "</body></html>"
        )
        speeches = self._fetch_with_html(html)
        speakers = [s["speaker"] for s in speeches]
        self.assertIn("議長", speakers)
        self.assertIn("町長", speakers)
        # strong tagも同様に扱われること
        html2 = "<html><body><strong>議長</strong><br>　発言です。<br></body></html>"
        speeches2 = self._fetch_with_html(html2)
        self.assertEqual("議長", speeches2[0]["speaker"])

    def test_b_with_anchor_is_not_speaker(self) -> None:
        html = (
            "<html><body>"
            '<B><A name="会議録署名議員の指名"></A>日程第１　会議録署名議員の指名</B><br>'
            "<B>議長</B><br>　日程第１を議題とします。<br>"
            "</body></html>"
        )
        speeches = self._fetch_with_html(html)
        for s in speeches:
            self.assertFalse(
                s["speaker"] is not None and "日程第" in s["speaker"],
                f"agenda heading became speaker: {s['speaker']}",
            )
        self.assertTrue(any(s["speaker"] == "議長" for s in speeches))

    def test_html_without_b_falls_back(self) -> None:
        html = "<html><body><p>第一段落</p><p>第二段落</p></body></html>"
        speeches = self._fetch_with_html(html)
        # fallbackは speaker null の paragraph セグメント
        self.assertTrue(all(s["speaker"] is None for s in speeches))
        self.assertGreaterEqual(len(speeches), 1)


class StaticHtmlDivSpeakerTest(unittest.TestCase):
    """旧年代 DIV 行頭話者形式の回帰テスト."""

    def _fetch_with_html(self, html: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = html.encode("utf-8")
            responses = {
                INDEX_URL: make_result(
                    INDEX_URL,
                    b'<a href="https://example.invalid/council/minutes/b.html">link</a>',
                    content_type="text/html",
                    cache_path=root / "index.cache",
                ),
                "https://example.invalid/council/minutes/b.html": make_result(
                    "https://example.invalid/council/minutes/b.html",
                    body,
                    content_type="text/html",
                    cache_path=root / "meeting.cache",
                ),
            }
            client = FakeHttpClient(responses)
            adapter = StaticHtmlAdapter(
                {"index_url": INDEX_URL, "pdf": False},
                client=client,
            )
            ref = adapter.list_meetings()[0]
            doc = adapter.fetch_meeting(ref["meeting_id"])
            return doc["speeches"]

    def test_div_speaker_is_segmented(self) -> None:
        html = "<html><body><div>議　　長　　　３番師田保議員。</div></body></html>"
        speeches = self._fetch_with_html(html)
        self.assertTrue(any(s["speaker"] == "議長" for s in speeches))
        self.assertEqual("議長", speeches[0]["speaker"])
        self.assertIn("３番師田保議員", speeches[0]["text"])

    def test_div_time_is_not_speaker(self) -> None:
        html = "<html><body><div>午前　９時３４分　　開会</div></body></html>"
        speeches = self._fetch_with_html(html)
        self.assertTrue(all(s["speaker"] is None for s in speeches))

    def test_div_indented_paragraph_is_not_speaker(self) -> None:
        html = "<html><body><div>　　おはようございます。通常の段落です。</div></body></html>"
        speeches = self._fetch_with_html(html)
        self.assertTrue(all(s["speaker"] is None for s in speeches))


if __name__ == "__main__":
    unittest.main()
