"""Importable HTTP test doubles shared by lcaios test modules."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from lcaios.http import CacheTier, FetchResult


class UnexpectedUrlError(KeyError):
    """Raised when a fake client receives an unmapped URL."""


class FakeHttpClient:
    """Maps URL to FetchResult. Records (url, tier). No network, no robots, no cache."""

    def __init__(
        self,
        responses: dict[str, FetchResult],
        *,
        user_agent: str = "fake",
    ) -> None:
        self.responses = responses
        self.user_agent = user_agent
        self.calls: list[tuple[str, CacheTier]] = []

    def fetch(
        self,
        url: str,
        *,
        tier: CacheTier,
        cache_key: str | None = None,
        sensitive_query_keys: set[str] | None = None,
    ) -> FetchResult:
        del cache_key, sensitive_query_keys
        self.calls.append((url, tier))
        try:
            return self.responses[url]
        except KeyError as error:
            raise UnexpectedUrlError(
                f"FakeHttpClient に未登録の URL です: {url}"
            ) from error

    def retrieval_report(self) -> dict[str, Any]:
        """Return the real client's report keys with inert fake values."""

        return {
            "cache_directory": "",
            "offline": False,
            "refresh": False,
            "live_request_count": 0,
            "cache_hit_count": 0,
            "cache_miss_count": 0,
            "refresh_count": 0,
            "latestness_rechecked_this_run": False,
            "sources": [],
            "accesses": [],
        }


def make_fetch_result(
    url: str,
    body: str,
    *,
    content_type: str = "text/html",
    encoding: str = "utf-8",
) -> FetchResult:
    """Build a deterministic FetchResult from a URL and text body."""

    body_bytes = body.encode(encoding)
    return FetchResult(
        url=url,
        final_url=url,
        body=body_bytes,
        fetched_at="2000-01-01T00:00:00Z",
        content_type=content_type,
        encoding=encoding,
        cache_path=Path("/nonexistent/fake-http-cache.body"),
        sha256=hashlib.sha256(body_bytes).hexdigest(),
        from_cache=False,
    )
