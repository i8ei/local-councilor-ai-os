"""Shared adapter contracts and a conservative HTTP fetcher."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPMessage
from pathlib import Path
from typing import Any, Mapping

USER_AGENT = "local-councilor-ai-os minutes ingester (research; low rate)"
MIN_REQUEST_INTERVAL_SECONDS = 1.5
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    """Raised when a source cannot be fetched safely."""


class RobotsDeniedError(FetchError):
    """Raised when robots.txt does not permit a requested URL."""


class RobotsUnavailableError(FetchError):
    """Raised when robots.txt cannot be checked."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep redirects visible so their destinations can be checked first."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_REQUEST_LOCK = threading.RLock()
_LAST_REQUEST_AT: float | None = None
_ROBOTS_CACHE: dict[tuple[str, str], urllib.robotparser.RobotFileParser] = {}


@dataclass(frozen=True)
class FetchResult:
    """A fetched or cached source with enough provenance to rebuild the DB."""

    url: str
    final_url: str
    body: bytes
    fetched_at: str
    content_type: str
    encoding: str
    cache_path: Path
    sha256: str
    from_cache: bool

    def text(self) -> str:
        """Decode the body without failing an ingestion run on bad bytes."""

        return self.body.decode(self.encoding or "utf-8", errors="replace")


class MinutesAdapter(ABC):
    """Minimal interface implemented by every minutes source adapter."""

    @abstractmethod
    def detect_capabilities(self) -> dict[str, Any]:
        """Describe supported discovery and extraction features."""

    @abstractmethod
    def list_meetings(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return source meeting descriptors, optionally capped by ``limit``."""

    @abstractmethod
    def fetch_meeting(self, meeting_id: str) -> dict[str, Any]:
        """Return one normalized meeting, its speeches, and provenance."""


# Keep the shorter name convenient for third-party adapters.
Adapter = MinutesAdapter


@dataclass(frozen=True)
class _RawResponse:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]
    fetched_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise FetchError(f"HTTP(S) URL が必要です: {url}")
    if parts.username or parts.password:
        raise FetchError("認証情報を含む URL は取得できません")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _cache_files(cache_dir: Path, url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.body", cache_dir / f"{digest}.json"


def _normalize_encoding(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("\"'").lower().replace("_", "-")
    if normalized in {"shift-jis", "shiftjis", "sjis", "windows-31j", "ms932"}:
        return "cp932"
    return normalized


def _detect_encoding(body: bytes, content_type_header: str) -> str:
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type_header, re.I)
    if charset_match:
        return _normalize_encoding(charset_match.group(1)) or "utf-8"
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    head = body[:4096]
    meta_match = re.search(
        br"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
        head,
        re.I,
    )
    if not meta_match:
        meta_match = re.search(
            br"<meta[^>]+content\s*=\s*[\"'][^\"']*charset=([a-zA-Z0-9._-]+)",
            head,
            re.I,
        )
    if meta_match:
        return _normalize_encoding(meta_match.group(1).decode("ascii", "ignore")) or "utf-8"
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        try:
            body.decode("cp932")
            return "cp932"
        except UnicodeDecodeError:
            return "utf-8"


def _content_type(headers: Mapping[str, str]) -> tuple[str, str]:
    raw = headers.get("Content-Type", headers.get("content-type", ""))
    media_type = raw.split(";", 1)[0].strip().lower() or "application/octet-stream"
    return media_type, raw


def _read_cached(
    cache_dir: Path,
    url: str,
    *,
    accepted_statuses: range | set[int],
) -> tuple[FetchResult, int] | None:
    body_path, metadata_path = _cache_files(cache_dir, url)
    if not body_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        status = int(metadata["status"])
        if status not in accepted_statuses:
            return None
        body = body_path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != metadata["sha256"]:
            return None
        result = FetchResult(
            url=url,
            final_url=str(metadata["final_url"]),
            body=body,
            fetched_at=str(metadata["fetched_at"]),
            content_type=str(metadata["content_type"]),
            encoding=str(metadata["encoding"]),
            cache_path=body_path,
            sha256=digest,
            from_cache=True,
        )
        return result, status
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(
    cache_dir: Path,
    requested_url: str,
    response: _RawResponse,
    request_log: list[dict[str, Any]],
) -> FetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path, metadata_path = _cache_files(cache_dir, requested_url)
    media_type, raw_content_type = _content_type(response.headers)
    encoding = _detect_encoding(response.body, raw_content_type)
    digest = hashlib.sha256(response.body).hexdigest()
    metadata = {
        "schema_version": 1,
        "requested_url": requested_url,
        "final_url": response.url,
        "status": response.status,
        "fetched_at": response.fetched_at,
        "content_type": media_type,
        "encoding": encoding,
        "sha256": digest,
        "requests": request_log,
    }
    suffix = f".tmp-{os.getpid()}-{threading.get_ident()}"
    body_tmp = body_path.with_name(body_path.name + suffix)
    metadata_tmp = metadata_path.with_name(metadata_path.name + suffix)
    try:
        body_tmp.write_bytes(response.body)
        metadata_tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(body_tmp, body_path)
        os.replace(metadata_tmp, metadata_path)
    finally:
        body_tmp.unlink(missing_ok=True)
        metadata_tmp.unlink(missing_ok=True)
    return FetchResult(
        url=requested_url,
        final_url=response.url,
        body=response.body,
        fetched_at=response.fetched_at,
        content_type=media_type,
        encoding=encoding,
        cache_path=body_path,
        sha256=digest,
        from_cache=False,
    )


def _throttle() -> None:
    global _LAST_REQUEST_AT
    now = time.monotonic()
    if _LAST_REQUEST_AT is not None:
        remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - _LAST_REQUEST_AT)
        if remaining > 0:
            time.sleep(remaining)
    _LAST_REQUEST_AT = time.monotonic()


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return int(status)


def _request_once(url: str, timeout: float) -> _RawResponse:
    _throttle()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
        method="GET",
    )
    try:
        try:
            response = _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            headers = {str(key): str(value) for key, value in response.headers.items()}
            response_url = _canonical_url(response.geturl() or url)
            return _RawResponse(
                url=response_url,
                status=_response_status(response),
                body=body,
                headers=headers,
                fetched_at=_utc_now(),
            )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"取得に失敗しました: {url}: {error}") from error


def _redirect_target(response: _RawResponse) -> str | None:
    if response.status not in {301, 302, 303, 307, 308}:
        return None
    location = response.headers.get("Location", response.headers.get("location"))
    if not location:
        raise FetchError(f"Location のないリダイレクトです: {response.url}")
    return _canonical_url(urllib.parse.urljoin(response.url, location))


def _fetch_robots(cache_dir: Path, robots_url: str, timeout: float) -> tuple[FetchResult, int]:
    accepted = set(range(200, 300)) | {401, 403, 404, 410}
    cached = _read_cached(cache_dir, robots_url, accepted_statuses=accepted)
    if cached:
        return cached

    current_url = robots_url
    request_log: list[dict[str, Any]] = []
    for _ in range(_MAX_REDIRECTS + 1):
        response = _request_once(current_url, timeout)
        request_log.append(
            {
                "url": current_url,
                "resolved_url": response.url,
                "status": response.status,
                "fetched_at": response.fetched_at,
            }
        )
        target = _redirect_target(response)
        if target:
            if urllib.parse.urlsplit(target).hostname != urllib.parse.urlsplit(robots_url).hostname:
                raise RobotsUnavailableError("robots.txt の別ホスト転送は追跡しません")
            current_url = target
            continue
        if response.status not in accepted:
            raise RobotsUnavailableError(
                f"robots.txt を確認できませんでした: HTTP {response.status}"
            )
        return _write_cache(cache_dir, robots_url, response, request_log), response.status
    raise RobotsUnavailableError("robots.txt のリダイレクト回数が上限を超えました")


def _robots_parser(url: str, cache_dir: Path, timeout: float) -> urllib.robotparser.RobotFileParser:
    origin = _origin(url)
    cache_key = (origin, str(cache_dir.resolve()))
    cached_parser = _ROBOTS_CACHE.get(cache_key)
    if cached_parser is not None:
        return cached_parser

    robots_url = origin + "/robots.txt"
    result, status = _fetch_robots(cache_dir, robots_url, timeout)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    if status in {401, 403}:
        lines = ["User-agent: *", "Disallow: /"]
    elif status in {404, 410}:
        lines = ["User-agent: *", "Disallow:"]
    else:
        lines = result.text().splitlines()
    parser.parse(lines)
    _ROBOTS_CACHE[cache_key] = parser
    return parser


def _assert_robots_allowed(url: str, cache_dir: Path, timeout: float) -> None:
    parser = _robots_parser(url, cache_dir, timeout)
    if not parser.can_fetch(USER_AGENT, url):
        raise RobotsDeniedError(f"robots.txt により取得できません: {url}")


def polite_fetch(
    url: str,
    cache_dir: str | os.PathLike[str] | None = None,
    timeout: float = 30,
) -> FetchResult:
    """Fetch one URL sequentially, respecting robots.txt, throttle, and cache.

    A valid cache hit never performs a network request. Network activity,
    including robots.txt and redirects, is serialized process-wide.
    """

    requested_url = _canonical_url(url)
    cache_root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    cached = _read_cached(cache_root, requested_url, accepted_statuses=range(200, 300))
    if cached:
        return cached[0]

    with _REQUEST_LOCK:
        cached = _read_cached(cache_root, requested_url, accepted_statuses=range(200, 300))
        if cached:
            return cached[0]
        current_url = requested_url
        request_log: list[dict[str, Any]] = []
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_robots_allowed(current_url, cache_root, timeout)
            response = _request_once(current_url, timeout)
            request_log.append(
                {
                    "url": current_url,
                    "resolved_url": response.url,
                    "status": response.status,
                    "fetched_at": response.fetched_at,
                }
            )
            target = _redirect_target(response)
            if target:
                current_url = target
                continue
            if response.status < 200 or response.status >= 300:
                raise FetchError(f"取得に失敗しました: HTTP {response.status}: {current_url}")
            return _write_cache(cache_root, requested_url, response, request_log)
        raise FetchError("リダイレクト回数が上限を超えました")
