"""Tests for the unified conservative HTTP client."""

# ruff: noqa: E402, I001

from __future__ import annotations

import os
import sys

# ``unittest discover lcaios`` adds lcaios/ as a top-level import directory,
# where this feature's http.py would otherwise shadow the stdlib http package.
_DISCOVERY_ROOT = os.path.dirname(os.path.dirname(__file__))
if _DISCOVERY_ROOT in sys.path:
    sys.path.remove(_DISCOVERY_ROOT)

import concurrent.futures
import email.message
import hashlib
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import lcaios.http as http
from lcaios.http import (
    BOOTSTRAP_USER_AGENT,
    MINUTES_USER_AGENT,
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchError,
    HttpClient,
    OfflineCacheMiss,
    RobotsDeniedError,
    RobotsUnavailableError,
    _RawResponse,
    _cache_files,
)
from lcaios.tests.http_fakes import (
    FakeHttpClient,
    UnexpectedUrlError,
    make_fetch_result,
)


class _FakeResponse:
    def __init__(
        self,
        url: str,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
        location: str | None = None,
        content_length: str | None = None,
    ) -> None:
        self.status = status
        self._url = url
        self._body = body
        self.read_sizes: list[int] = []
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        if location is not None:
            self.headers["Location"] = location
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            return self._body
        return self._body[:size]

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _raw_response(
    url: str,
    body: bytes = b"fresh",
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    fetched_at: str = "2026-07-25T00:00:00Z",
) -> _RawResponse:
    return _RawResponse(
        url=url,
        status=status,
        body=body,
        headers=headers or {"Content-Type": "text/plain; charset=utf-8"},
        fetched_at=fetched_at,
    )


def _write_cached(
    cache_dir: Path,
    cache_key: str,
    url: str,
    *,
    body: bytes = b"cached",
    fetched_at: str = "2000-01-01T00:00:00Z",
    status: int = 200,
) -> tuple[Path, Path]:
    body_path, metadata_path = _cache_files(cache_dir, cache_key)
    body_path.write_bytes(body)
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requested_url": url,
                "final_url": url,
                "status": status,
                "fetched_at": fetched_at,
                "content_type": "text/plain",
                "encoding": "utf-8",
                "sha256": hashlib.sha256(body).hexdigest(),
                "requests": [],
            }
        ),
        encoding="utf-8",
    )
    return body_path, metadata_path


class HttpClientTests(unittest.TestCase):
    def setUp(self) -> None:
        http.reset_throttle_state()

    def test_required_user_agent_and_tier_have_no_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(TypeError):
                HttpClient(temporary)  # type: ignore[call-arg]
            client = HttpClient(temporary, user_agent="test")
            with self.assertRaises(TypeError):
                client.fetch("https://example.test/")  # type: ignore[call-arg]

    def test_user_agent_constants_match_existing_clients(self) -> None:
        self.assertEqual(
            BOOTSTRAP_USER_AGENT,
            (
                "local-councilor-ai-os bootstrap/0.1 "
                "(official public-data research; low rate)"
            ),
        )
        self.assertEqual(
            MINUTES_USER_AGENT,
            "local-councilor-ai-os minutes ingester (research; low rate)",
        )
        self.assertEqual(
            REGULATIONS_USER_AGENT,
            "local-councilor-ai-os regulations ingester (research; low rate)",
        )

    def test_fetches_robots_then_page_and_reuses_on_disk_cache(self) -> None:
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
            with mock.patch.object(
                http._OPENER, "open", side_effect=[robots, page]
            ) as opened:
                first_client = HttpClient(
                    temporary,
                    user_agent=MINUTES_USER_AGENT,
                    min_interval_seconds=0,
                )
                first = first_client.fetch(
                    "https://example.test/minutes.html",
                    tier=CacheTier.DOCUMENT,
                )
                second_client = HttpClient(
                    temporary,
                    user_agent=MINUTES_USER_AGENT,
                    offline=True,
                )
                second = second_client.fetch(
                    "https://example.test/minutes.html",
                    tier=CacheTier.DOCUMENT,
                )

            self.assertEqual(opened.call_count, 2)
            for call in opened.call_args_list:
                request = call.args[0]
                self.assertEqual(
                    request.get_header("User-agent"),
                    MINUTES_USER_AGENT,
                )
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(first.encoding, "cp932")
            self.assertIn("会議録", first.text())
            self.assertEqual(first.sha256, second.sha256)
            self.assertTrue(first.fetched_at.endswith("Z"))
            self.assertTrue(first.cache_path.is_file())
            metadata_files = list(Path(temporary).glob("*.json"))
            self.assertEqual(len(metadata_files), 2)
            page_metadata = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in metadata_files
                if json.loads(path.read_text(encoding="utf-8"))[
                    "requested_url"
                ].endswith("/minutes.html")
            ][0]
            self.assertEqual(page_metadata["schema_version"], 1)
            self.assertTrue(
                page_metadata["requests"][0]["fetched_at"].endswith("Z")
            )

    def test_undeclared_euc_jp_body_decodes_as_euc_jp_not_cp932(self) -> None:
        # core-01: EUC-JP bytes decode "successfully" under cp932 (dense
        # variable-width encodings overlap), silently producing mojibake.
        # The fallback must try EUC-JP before cp932 so declared-free pages
        # are not corrupted; cp932-only bytes (SJIS trail < 0xA1) must still
        # resolve to cp932.
        eucjp_body = "会議録を公開しております".encode("euc-jp")
        self.assertEqual(
            http._detect_encoding(eucjp_body, "text/html"), "euc-jp"
        )
        # 議 in Shift_JIS = 0x8C 0x7C; trail 0x7C is invalid in EUC-JP, so
        # the euc-jp attempt must raise and fall through to cp932.
        sjis_body = "会議録".encode("cp932")
        self.assertEqual(
            http._detect_encoding(sjis_body, "text/html"), "cp932"
        )
        # Declared charset still wins over the fallback.
        self.assertEqual(
            http._detect_encoding(sjis_body, "text/html; charset=x-sjis"),
            "cp932",
        )
        self.assertEqual(
            http._detect_encoding(eucjp_body, "text/html; charset=x-euc-jp"),
            "euc-jp",
        )
        self.assertEqual(
            http._detect_encoding(sjis_body, "text/html; charset=unknown-xyz"),
            "utf-8",
        )

    def test_denied_path_is_not_fetched(self) -> None:
        robots = _FakeResponse(
            "https://example.test/robots.txt",
            b"User-agent: *\nDisallow: /private\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                temporary,
                user_agent=MINUTES_USER_AGENT,
                min_interval_seconds=0,
            )
            with mock.patch.object(
                http._OPENER, "open", return_value=robots
            ) as opened:
                with self.assertRaises(RobotsDeniedError):
                    client.fetch(
                        "https://example.test/private/minutes.html",
                        tier=CacheTier.DOCUMENT,
                    )
            self.assertEqual(opened.call_count, 1)

    def test_cached_fetch_is_reported_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            key = "fixture:data"
            _write_cached(
                cache,
                key,
                "https://example.test/data",
            )
            client = HttpClient(
                cache,
                user_agent=BOOTSTRAP_USER_AGENT,
                offline=True,
            )
            result = client.fetch(
                "https://example.test/data",
                tier=CacheTier.INDEX,
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
            client = HttpClient(
                temporary,
                user_agent=BOOTSTRAP_USER_AGENT,
                offline=True,
            )
            with self.assertRaises(OfflineCacheMiss):
                client.fetch(
                    "https://example.test/missing",
                    tier=CacheTier.INDEX,
                    cache_key="fixture:missing",
                )
            report = client.retrieval_report()
            self.assertEqual(1, report["cache_miss_count"])
            self.assertEqual("cache_miss", report["accesses"][0]["status"])

    def test_offline_and_refresh_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "同時"):
            HttpClient(
                "/tmp/unused-lcaios-cache",
                user_agent=BOOTSTRAP_USER_AGENT,
                offline=True,
                refresh=True,
            )

    def test_invalid_cached_sha256_is_refetched(self) -> None:
        url = "https://example.test/document"
        key = "fixture:document"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            body_path, _ = _write_cached(cache, key, url)
            body_path.write_bytes(b"tampered")
            client = HttpClient(
                cache,
                user_agent="test",
                min_interval_seconds=0,
            )
            with (
                mock.patch.object(client, "_assert_robots_allowed"),
                mock.patch.object(
                    client,
                    "_request_once",
                    return_value=_raw_response(url, b"verified"),
                ) as requested,
            ):
                result = client.fetch(
                    url,
                    tier=CacheTier.DOCUMENT,
                    cache_key=key,
                )
            requested.assert_called_once_with(url)
            self.assertFalse(result.from_cache)
            self.assertEqual(result.body, b"verified")

    def test_cache_key_is_separate_and_sensitive_query_is_redacted(self) -> None:
        url = "https://example.test/data?token=secret&year=2026"
        key = "fixture:stable-key"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            client = HttpClient(
                cache,
                user_agent="test",
                min_interval_seconds=0,
            )
            with (
                mock.patch.object(client, "_assert_robots_allowed"),
                mock.patch.object(
                    client,
                    "_request_once",
                    return_value=_raw_response(url),
                ),
            ):
                result = client.fetch(
                    url,
                    tier=CacheTier.INDEX,
                    cache_key=key,
                    sensitive_query_keys={"token"},
                )
            _, metadata_path = _cache_files(cache, key)
            metadata_text = metadata_path.read_text(encoding="utf-8")
            self.assertTrue(metadata_path.is_file())
            self.assertNotIn("secret", metadata_text)
            self.assertNotIn("secret", result.url)
            self.assertIn("token=REDACTED", result.url)

    def test_index_entry_older_than_ttl_is_refetched(self) -> None:
        url = "https://example.test/index"
        key = "fixture:index"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            _write_cached(cache, key, url, body=b"old")
            client = HttpClient(
                cache,
                user_agent="test",
                index_ttl_seconds=60,
                min_interval_seconds=0,
            )
            with (
                mock.patch.object(client, "_assert_robots_allowed"),
                mock.patch.object(
                    client,
                    "_request_once",
                    return_value=_raw_response(url, b"new"),
                ) as requested,
            ):
                result = client.fetch(
                    url,
                    tier=CacheTier.INDEX,
                    cache_key=key,
                )
            requested.assert_called_once_with(url)
            self.assertFalse(result.from_cache)
            self.assertEqual(result.body, b"new")

    def test_index_entry_within_ttl_is_served_from_cache(self) -> None:
        url = "https://example.test/index"
        key = "fixture:index"
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            _write_cached(cache, key, url, fetched_at=fetched_at)
            client = HttpClient(
                cache,
                user_agent="test",
                index_ttl_seconds=60,
            )
            result = client.fetch(
                url,
                tier=CacheTier.INDEX,
                cache_key=key,
            )
            self.assertTrue(result.from_cache)
            self.assertEqual(client.request_count, 0)

    def test_document_entry_older_than_ttl_is_served_from_cache(self) -> None:
        url = "https://example.test/document"
        key = "fixture:document"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            _write_cached(cache, key, url)
            client = HttpClient(
                cache,
                user_agent="test",
                index_ttl_seconds=0,
            )
            result = client.fetch(
                url,
                tier=CacheTier.DOCUMENT,
                cache_key=key,
            )
            self.assertTrue(result.from_cache)
            self.assertEqual(client.request_count, 0)

    def test_offline_serves_ttl_expired_index_entry(self) -> None:
        url = "https://example.test/index"
        key = "fixture:index"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            _write_cached(cache, key, url)
            client = HttpClient(
                cache,
                user_agent="test",
                offline=True,
                index_ttl_seconds=0,
            )
            result = client.fetch(
                url,
                tier=CacheTier.INDEX,
                cache_key=key,
            )
            self.assertTrue(result.from_cache)
            self.assertEqual(client.request_count, 0)

    def test_refresh_refetches_both_tiers(self) -> None:
        url = "https://example.test/data"
        for tier in CacheTier:
            with self.subTest(tier=tier):
                with tempfile.TemporaryDirectory() as temporary:
                    cache = Path(temporary)
                    key = f"fixture:{tier.value}"
                    _write_cached(cache, key, url, body=b"old")
                    client = HttpClient(
                        cache,
                        user_agent="test",
                        refresh=True,
                        min_interval_seconds=0,
                    )
                    with (
                        mock.patch.object(client, "_assert_robots_allowed"),
                        mock.patch.object(
                            client,
                            "_request_once",
                            return_value=_raw_response(url, b"new"),
                        ) as requested,
                    ):
                        result = client.fetch(
                            url,
                            tier=tier,
                            cache_key=key,
                        )
                    requested.assert_called_once_with(url)
                    self.assertFalse(result.from_cache)
                    self.assertEqual(result.body, b"new")
                    self.assertEqual(client.refresh_count, 1)

    def test_robots_http_400_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, user_agent="test")
            with mock.patch.object(
                client,
                "_request_once",
                return_value=_raw_response(
                    "https://example.test/robots.txt",
                    b"",
                    status=400,
                ),
            ):
                with self.assertRaisesRegex(RobotsUnavailableError, "HTTP 400"):
                    client._robots_parser("https://example.test/data")

    def test_robots_http_404_allows_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, user_agent="test")
            with mock.patch.object(
                client,
                "_request_once",
                return_value=_raw_response(
                    "https://example.test/robots.txt",
                    b"",
                    status=404,
                ),
            ):
                client._assert_robots_allowed("https://example.test/data")

    def test_robots_unreachable_is_treated_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, user_agent="test")
            with mock.patch.object(
                client,
                "_request_once",
                side_effect=FetchError("connection closed"),
            ):
                # A robots.txt that cannot be fetched (connection close /
                # timeout) is treated as absent, so fetching is allowed.
                client._assert_robots_allowed("https://example.test/data")

    def test_robots_http_403_denies_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, user_agent="test")
            with mock.patch.object(
                client,
                "_request_once",
                return_value=_raw_response(
                    "https://example.test/robots.txt",
                    b"",
                    status=403,
                ),
            ):
                with self.assertRaises(RobotsDeniedError):
                    client._assert_robots_allowed("https://example.test/data")

    def test_robots_redirect_can_cross_authority_under_rfc_9309(self) -> None:
        responses = iter(
            [
                _raw_response(
                    "https://old.example.test/robots.txt",
                    b"",
                    status=301,
                    headers={
                        "Location": "https://new.example.test/robots.txt"
                    },
                ),
                _raw_response(
                    "https://new.example.test/robots.txt",
                    b"User-agent: *\nDisallow: /private\n",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(temporary, user_agent="test")
            with mock.patch.object(
                client,
                "_request_once",
                side_effect=lambda _: next(responses),
            ) as requested:
                parser = client._robots_parser(
                    "https://old.example.test/public"
                )
        self.assertEqual(requested.call_count, 2)
        self.assertTrue(
            parser.can_fetch("test", "https://old.example.test/public")
        )
        self.assertFalse(
            parser.can_fetch("test", "https://old.example.test/private")
        )

    def test_content_length_over_cap_raises_before_body_read(self) -> None:
        response = _FakeResponse(
            "https://example.test/large",
            b"not-read",
            content_length="11",
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                temporary,
                user_agent="test",
                min_interval_seconds=0,
                max_response_bytes=10,
            )
            with mock.patch.object(http._OPENER, "open", return_value=response):
                with self.assertRaisesRegex(
                    FetchError,
                    r"上限 10 バイト.*https://example\.test/large",
                ):
                    client._request_once("https://example.test/large")
        self.assertEqual(response.read_sizes, [])

    def test_oversized_body_raises_after_bounded_read(self) -> None:
        response = _FakeResponse(
            "https://example.test/large",
            b"x" * 12,
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                temporary,
                user_agent="test",
                min_interval_seconds=0,
                max_response_bytes=10,
            )
            with mock.patch.object(http._OPENER, "open", return_value=response):
                with self.assertRaisesRegex(FetchError, "上限 10 バイト"):
                    client._request_once("https://example.test/large")
        self.assertEqual(response.read_sizes, [11])

    def test_clients_send_their_own_user_agents(self) -> None:
        responses = [
            _FakeResponse("https://example.test/one", b"one"),
            _FakeResponse("https://example.test/two", b"two"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            first = HttpClient(
                temporary,
                user_agent="client-one",
                min_interval_seconds=0,
            )
            second = HttpClient(
                temporary,
                user_agent="client-two",
                min_interval_seconds=0,
            )
            with mock.patch.object(
                http._OPENER, "open", side_effect=responses
            ) as opened:
                first._request_once("https://example.test/one")
                second._request_once("https://example.test/two")
        sent_agents = [
            call.args[0].get_header("User-agent")
            for call in opened.call_args_list
        ]
        self.assertEqual(sent_agents, ["client-one", "client-two"])

    def test_throttle_is_shared_across_client_instances_on_one_host(self) -> None:
        responses = [
            _FakeResponse("https://example.test/one", b"one"),
            _FakeResponse("https://example.test/two", b"two"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            first = HttpClient(
                temporary,
                user_agent="one",
                min_interval_seconds=99,
            )
            second = HttpClient(
                temporary,
                user_agent="two",
                min_interval_seconds=1.5,
            )
            with (
                mock.patch.object(http._OPENER, "open", side_effect=responses),
                mock.patch.object(
                    http.time,
                    "monotonic",
                    # claim slot for /one, read clock for /two, claim it
                    side_effect=[10.0, 10.25, 11.5],
                ),
                mock.patch.object(http.time, "sleep") as slept,
            ):
                first._request_once("https://example.test/one")
                second._request_once("https://example.test/two")
        slept.assert_called_once_with(1.25)

    def test_throttle_is_not_shared_between_hosts(self) -> None:
        responses = [
            _FakeResponse("https://one.test/a", b"one"),
            _FakeResponse("https://two.test/a", b"two"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            first = HttpClient(
                temporary,
                user_agent="one",
                min_interval_seconds=99,
            )
            second = HttpClient(
                temporary,
                user_agent="two",
                min_interval_seconds=99,
            )
            with (
                mock.patch.object(http._OPENER, "open", side_effect=responses),
                mock.patch.object(
                    http.time,
                    "monotonic",
                    side_effect=[10.0, 10.25],
                ),
                mock.patch.object(http.time, "sleep") as slept,
            ):
                first._request_once("https://one.test/a")
                second._request_once("https://two.test/a")
        slept.assert_not_called()

    def test_retry_on_429_with_retry_after_header(self) -> None:
        rate_limited = _FakeResponse(
            "https://example.test/rate-limited",
            b"Too Many Requests",
            status=429,
        )
        rate_limited.headers["Retry-After"] = "5"
        success = _FakeResponse(
            "https://example.test/rate-limited",
            b"Success after retry",
            status=200,
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                temporary,
                user_agent="retry-test",
                min_interval_seconds=0,
                max_retries=2,
            )
            with (
                mock.patch.object(
                    http._OPENER,
                    "open",
                    side_effect=[rate_limited, success],
                ),
                mock.patch.object(http.time, "sleep") as slept,
            ):
                raw = client._request_once("https://example.test/rate-limited")
            self.assertEqual(200, raw.status)
            self.assertEqual(b"Success after retry", raw.body)
            self.assertEqual(1, client.retry_count)
            slept.assert_called_once_with(5.0)

    def test_retry_fails_when_exceeding_max_retries(self) -> None:
        rate_limited = _FakeResponse(
            "https://example.test/persistent-429",
            b"Rate Limited",
            status=429,
        )
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                temporary,
                user_agent="retry-fail-test",
                min_interval_seconds=0,
                max_retries=2,
                backoff_base_seconds=1.0,
            )
            with (
                mock.patch.object(
                    http._OPENER,
                    "open",
                    side_effect=[rate_limited, rate_limited, rate_limited],
                ),
                mock.patch.object(http.time, "sleep") as slept,
            ):
                raw = client._request_once("https://example.test/persistent-429")
            self.assertEqual(429, raw.status)
            self.assertEqual(2, client.retry_count)
            self.assertEqual(2, slept.call_count)


class FakeHttpClientTests(unittest.TestCase):
    def test_maps_responses_and_records_url_and_tier(self) -> None:
        url = "https://example.test/data"
        response = make_fetch_result(url, "本文")
        client = FakeHttpClient({url: response}, user_agent="test-fake")
        result = client.fetch(url, tier=CacheTier.DOCUMENT)
        self.assertIs(result, response)
        self.assertEqual(client.calls, [(url, CacheTier.DOCUMENT)])
        self.assertEqual(client.user_agent, "test-fake")

    def test_unmapped_url_raises_clear_key_error(self) -> None:
        client = FakeHttpClient({})
        url = "https://example.test/unexpected"
        with self.assertRaisesRegex(UnexpectedUrlError, url):
            client.fetch(url, tier=CacheTier.INDEX)

    def test_retrieval_report_has_real_report_keys(self) -> None:
        client = FakeHttpClient({})
        with tempfile.TemporaryDirectory() as temporary:
            real = HttpClient(temporary, user_agent="test")
            self.assertEqual(
                set(client.retrieval_report()),
                set(real.retrieval_report()),
            )


class PerHostThrottleTests(unittest.TestCase):
    """Politeness is per host: one host is paced, unrelated hosts are not."""

    def setUp(self) -> None:
        http.reset_throttle_state()

    @staticmethod
    def _opener(log: list[tuple[str, float, float]], hold: float):
        append_lock = threading.Lock()

        def _open(request, *args, **kwargs):  # type: ignore[no-untyped-def]
            url = request.full_url
            started = time.monotonic()
            time.sleep(hold)
            with append_lock:
                log.append((url, started, time.monotonic()))
            if url.endswith("/robots.txt"):
                return _FakeResponse(url, b"User-agent: *\nAllow: /\n")
            return _FakeResponse(url, b"<p>ok</p>", content_type="text/html")

        return _open

    def test_throttle_host_key_drops_leading_www(self) -> None:
        self.assertEqual(
            http._throttle_host("https://www.city.example.test/a"),
            "city.example.test",
        )
        self.assertEqual(
            http._throttle_host("https://city.example.test/a"),
            "city.example.test",
        )
        self.assertNotEqual(
            http._throttle_host("https://a.example.test/"),
            http._throttle_host("https://b.example.test/"),
        )

    def test_same_host_requests_are_serialised_and_spaced(self) -> None:
        interval = 0.3
        log: list[tuple[str, float, float]] = []
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                http._OPENER, "open", side_effect=self._opener(log, hold=0.05)
            ):

                def run(path: str) -> None:
                    HttpClient(
                        temporary,
                        user_agent="test",
                        min_interval_seconds=interval,
                    ).fetch(f"https://same.test/{path}", tier=CacheTier.DOCUMENT)

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(run, ["a", "b"]))

        self.assertGreaterEqual(len(log), 2)
        spans = sorted((start, end) for _, start, end in log)
        for (first_start, first_end), (second_start, _) in zip(
            spans, spans[1:], strict=False
        ):
            self.assertLessEqual(
                first_end, second_start + 1e-6, "same-host requests overlapped"
            )
            self.assertGreaterEqual(
                second_start - first_start,
                interval * 0.9,
                "same-host requests were not spaced by min_interval_seconds",
            )

    def test_different_hosts_do_not_wait_on_each_other(self) -> None:
        # Long enough that a single global throttle would dominate the timing.
        interval = 2.0
        hosts = ("first.test", "second.test")
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                http._OPENER, "open", side_effect=self._opener([], hold=0.0)
            ):
                # Warm the on-disk robots.txt cache so the timed section below
                # makes exactly one request per host.
                for host in hosts:
                    HttpClient(
                        temporary, user_agent="test", min_interval_seconds=0
                    ).fetch(f"https://{host}/warm", tier=CacheTier.DOCUMENT)

            http.reset_throttle_state()
            log: list[tuple[str, float, float]] = []
            with mock.patch.object(
                http._OPENER, "open", side_effect=self._opener(log, hold=0.1)
            ):

                def run(host: str) -> None:
                    HttpClient(
                        temporary,
                        user_agent="test",
                        min_interval_seconds=interval,
                    ).fetch(f"https://{host}/page", tier=CacheTier.DOCUMENT)

                started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(run, hosts))
                elapsed = time.monotonic() - started

        # Order is not asserted: with a per-host gate the two run concurrently,
        # so which one lands first is genuinely undetermined.
        self.assertEqual(
            sorted(url for url, _, _ in log),
            sorted(f"https://{host}/page" for host in hosts),
            "expected exactly one request per host; robots cache was not reused",
        )
        self.assertLess(
            elapsed,
            interval,
            "unrelated hosts waited on each other's throttle",
        )


if __name__ == "__main__":
    unittest.main()
