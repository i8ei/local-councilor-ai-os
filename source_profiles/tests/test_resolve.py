"""Tests for the resolve subcommand (synthetic fixtures only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lcaios.http import FetchError
from source_profiles.cli import _cmd_resolve


def _write_profile(root: Path, status: str = "ready") -> Path:
    muni_dir = root / "41-test"
    muni_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "schema_version": 1,
        "area_code_5": "99901",
        "prefecture": "テスト県",
        "municipality": "テスト市",
        "official_home_url": "https://www.city.test.example/",
        "sources": {
            "minutes": {"status": "not_evaluated", "adapter": None},
            "regulations": {"status": "not_evaluated", "adapter": None},
            "budget": {
                "status": status,
                "adapter": "official_document_index",
                "index_url": "https://www.city.test.example/zaisei/yosan.html",
                "verified_at": "2026-08-23T00:00:00Z" if status == "ready" else None,
                "verified_by": "test" if status == "ready" else None,
                "evidence": (
                    [
                        {
                            "url": "https://www.city.test.example/zaisei/yosan.html",
                            "observed_on": "https://www.city.test.example/",
                            "sha256": "a" * 64,
                            "fetched_at": "2026-08-23T00:00:00Z",
                        }
                    ]
                    if status == "ready"
                    else []
                ),
                "notes": None,
            },
            "settlement": {"status": "not_evaluated", "adapter": None},
        },
    }
    path = muni_dir / "99901-test.json"
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return root


_INDEX_HTML = """
<html><body>
<a href="/site_files/file/r8-yosan.pdf">令和8年度一般会計当初予算書</a>
<a href="kessan.xlsx">令和7年度決算</a>
<a href="https://other.example/doc.pdf">他サイト文書（除外される）</a>
<a href="#top">ページ内リンク（除外される）</a>
</body></html>
""".encode("utf-8")


class _FakeResult:
    def __init__(self, body: bytes, url: str) -> None:
        self.url = url
        self.final_url = url
        self.body = body
        self.fetched_at = "2026-08-23T00:00:00Z"
        self.content_type = "application/pdf" if body.startswith(b"%PDF") else "text/html"
        self.encoding = "utf-8"
        self.cache_path = Path("/tmp/fake-cache/document")
        self.sha256 = "b" * 64
        self.from_cache = False


class _RobotsDeniedError(FetchError):
    pass


class _FakeClient:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str]] = []

    def fetch(self, url: str, *, tier):  # type: ignore[no-untyped-def]
        self.calls.append((url, tier.value))
        if url in self.pages:
            return _FakeResult(self.pages[url], url)
        raise LookupError(f"not found: {url}")


class _DenyClient:
    def fetch(self, url: str, *, tier):  # type: ignore[no-untyped-def]
        raise _RobotsDeniedError(f"robots.txt により取得できません: {url}")


def _run_capture(args: list[str], client_factory=None) -> tuple[int, dict]:
    import contextlib
    import io

    from source_profiles.cli import build_parser

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _cmd_resolve(
            build_parser().parse_args(["resolve"] + args),
            client_factory=client_factory,
        )
    return code, json.loads(buf.getvalue())


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "profiles"
        _write_profile(self.root)
        self.base = [
            "--municipality",
            "テスト市",
            "--kind",
            "budget",
            "--cache-dir",
            str(Path(self._tmp.name) / "cache"),
            "--profiles-dir",
            str(self.root.parent),
        ]

    def test_lists_same_host_documents_only(self) -> None:
        client = _FakeClient(
            {"https://www.city.test.example/zaisei/yosan.html": _INDEX_HTML}
        )
        code, report = _run_capture(self.base, client_factory=lambda: client)
        self.assertEqual(code, 0)
        self.assertEqual(report["result"], "ok")
        urls = [d["url"] for d in report["documents"]]
        self.assertEqual(len(urls), 2)
        self.assertIn(
            "https://www.city.test.example/site_files/file/r8-yosan.pdf", urls
        )
        self.assertTrue(all(u.startswith("https://www.city.test.example") for u in urls))
        labels = [d["label"] for d in report["documents"]]
        self.assertIn("令和8年度一般会計当初予算書", labels)

    def test_get_downloads_document_and_reports_local_path(self) -> None:
        pdf = b"%PDF-1.4 fake"
        client = _FakeClient(
            {
                "https://www.city.test.example/zaisei/yosan.html": _INDEX_HTML,
                "https://www.city.test.example/site_files/file/r8-yosan.pdf": pdf,
                "https://www.city.test.example/kessan.xlsx": b"xlsx-bytes",
            }
        )
        code, report = _run_capture(
            self.base + ["--get", "1"], client_factory=lambda: client
        )
        self.assertEqual(code, 0)
        fetched = report["fetched_document"]
        self.assertEqual(fetched["local_path"], "/tmp/fake-cache/document")
        self.assertEqual(fetched["sha256"], "b" * 64)
        # document tier used for the second fetch
        self.assertEqual(client.calls[1][1], "document")

    def test_robots_denied_fails_cleanly(self) -> None:
        code, report = _run_capture(
            self.base, client_factory=lambda: _DenyClient()
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["result"], "failed")
        self.assertIn("robots_denied", report["reason"])

    def test_non_ready_status_adds_warning_but_still_resolves(self) -> None:
        _write_profile(self.root, status="needs_review")
        client = _FakeClient(
            {"https://www.city.test.example/zaisei/yosan.html": _INDEX_HTML}
        )
        code, report = _run_capture(self.base, client_factory=lambda: client)
        self.assertEqual(code, 0)
        self.assertIn("warnings", report)

    def test_missing_municipality_fails(self) -> None:
        code, report = _run_capture(
            [
                "--municipality",
                "存在しない市",
                "--kind",
                "budget",
                "--cache-dir",
                "/tmp/resolve-test-cache",
                "--profiles-dir",
                str(self.root.parent),
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["result"], "failed")


if __name__ == "__main__":
    unittest.main()
