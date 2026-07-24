"""Conservative HTTP fetching with robots checks, throttling, and a local cache."""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


USER_AGENT = (
    "local-councilor-ai-os bootstrap/0.1 "
    "(official public-data research; low rate)"
)
MIN_REQUEST_INTERVAL_SECONDS = 1.5
_MAX_REDIRECTS = 5
_REQUEST_LOCK = threading.RLock()
_LAST_REQUEST_AT: float | None = None


class FetchError(RuntimeError):
    """Raised when a source cannot be fetched safely."""


class OfflineCacheMiss(FetchError):
    """Raised when offline mode cannot satisfy a request from cache."""


class RobotsDeniedError(FetchError):
    """Raised when robots.txt does not permit a requested URL."""


class RobotsUnavailableError(FetchError):
    """Raised when robots.txt cannot be checked."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep redirects visible so each destination can be checked."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


@dataclass(frozen=True)
class FetchResult:
    """A fetched or cached source plus rebuild provenance."""

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
        """Decode the response using the detected encoding."""

        return self.body.decode(self.encoding or "utf-8", errors="replace")

    def json(self) -> dict[str, Any]:
        """Decode a JSON object response."""

        payload = json.loads(self.text())
        if not isinstance(payload, dict):
            raise FetchError(f"JSON object ではない応答です: {self.url}")
        return payload


@dataclass(frozen=True)
class _RawResponse:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]
    fetched_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise FetchError(f"HTTP(S) URL が必要です: {url}")
    if parts.username or parts.password:
        raise FetchError("認証情報を含む URL は取得できません")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )


def _redact_url(url: str, sensitive_query_keys: set[str]) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe_query = [
        (key, "REDACTED" if key in sensitive_query_keys else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(safe_query), "")
    )


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _cache_files(cache_dir: Path, cache_key: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.body", cache_dir / f"{digest}.json"


def _normalize_encoding(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("\"'").lower().replace("_", "-")
    if normalized in {"shift-jis", "shiftjis", "sjis", "windows-31j", "ms932"}:
        return "cp932"
    return normalized


def _detect_encoding(body: bytes, content_type_header: str) -> str:
    charset_match = re.search(
        r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type_header, re.I
    )
    if charset_match:
        return _normalize_encoding(charset_match.group(1)) or "utf-8"
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    head = body[:4096]
    meta_match = re.search(
        br"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", head, re.I
    )
    if not meta_match:
        meta_match = re.search(
            br"<meta[^>]+content\s*=\s*[\"'][^\"']*charset=([a-zA-Z0-9._-]+)",
            head,
            re.I,
        )
    if meta_match:
        return (
            _normalize_encoding(meta_match.group(1).decode("ascii", "ignore"))
            or "utf-8"
        )
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


class HttpClient:
    """Sequential cached HTTP client used by all bootstrap sources."""

    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        offline: bool = False,
        refresh: bool = False,
        timeout: float = 90,
    ) -> None:
        if offline and refresh:
            raise ValueError("offlineとrefreshは同時に指定できません")
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.refresh = refresh
        self.timeout = timeout
        self.request_count = 0
        self.cache_hit_count = 0
        self.cache_miss_count = 0
        self.refresh_count = 0
        self.fetch_log: list[dict[str, Any]] = []
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    @staticmethod
    def _source_id(cache_key: str) -> str:
        return cache_key.split(":", 1)[0] or "url"

    def _record_fetch(
        self,
        *,
        cache_key: str,
        url: str,
        status: str,
        result: FetchResult | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "source_id": self._source_id(cache_key),
            "status": status,
            "url": result.url if result is not None else url,
        }
        if result is not None:
            item.update(
                {
                    "final_url": result.final_url,
                    "fetched_at": result.fetched_at,
                    "sha256": result.sha256,
                }
            )
        self.fetch_log.append(item)

    def retrieval_report(self) -> dict[str, Any]:
        """Return secret-free cache and network provenance for this process."""

        by_source: dict[str, dict[str, Any]] = {}
        for item in self.fetch_log:
            source = by_source.setdefault(
                item["source_id"],
                {
                    "source_id": item["source_id"],
                    "cache_hits": 0,
                    "network_fetches": 0,
                    "refreshes": 0,
                    "cache_misses": 0,
                    "latestness_rechecked_this_run": True,
                },
            )
            status = item["status"]
            if status == "cache_hit":
                source["cache_hits"] += 1
                source["latestness_rechecked_this_run"] = False
            elif status == "refreshed":
                source["refreshes"] += 1
                source["network_fetches"] += 1
            elif status == "fetched":
                source["network_fetches"] += 1
            elif status == "cache_miss":
                source["cache_misses"] += 1
                source["latestness_rechecked_this_run"] = False
        return {
            "cache_directory": str(self.cache_dir.resolve(strict=False)),
            "offline": self.offline,
            "refresh": self.refresh,
            "live_request_count": self.request_count,
            "cache_hit_count": self.cache_hit_count,
            "cache_miss_count": self.cache_miss_count,
            "refresh_count": self.refresh_count,
            "latestness_rechecked_this_run": bool(self.fetch_log)
            and all(
                item["status"] in {"fetched", "refreshed"}
                for item in self.fetch_log
            ),
            "sources": sorted(by_source.values(), key=lambda item: item["source_id"]),
            "accesses": self.fetch_log,
        }

    def _read_cached(
        self,
        cache_key: str,
        *,
        accepted_statuses: range | set[int],
    ) -> tuple[FetchResult, int] | None:
        body_path, metadata_path = _cache_files(self.cache_dir, cache_key)
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
            return (
                FetchResult(
                    url=str(metadata["requested_url"]),
                    final_url=str(metadata["final_url"]),
                    body=body,
                    fetched_at=str(metadata["fetched_at"]),
                    content_type=str(metadata["content_type"]),
                    encoding=str(metadata["encoding"]),
                    cache_path=body_path,
                    sha256=digest,
                    from_cache=True,
                ),
                status,
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _write_cache(
        self,
        cache_key: str,
        requested_url: str,
        response: _RawResponse,
        request_log: list[dict[str, Any]],
        *,
        sensitive_query_keys: set[str],
    ) -> FetchResult:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body_path, metadata_path = _cache_files(self.cache_dir, cache_key)
        media_type, raw_content_type = _content_type(response.headers)
        encoding = _detect_encoding(response.body, raw_content_type)
        digest = hashlib.sha256(response.body).hexdigest()
        safe_requested_url = _redact_url(requested_url, sensitive_query_keys)
        safe_final_url = _redact_url(response.url, sensitive_query_keys)
        metadata = {
            "schema_version": 1,
            "requested_url": safe_requested_url,
            "final_url": safe_final_url,
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
            url=safe_requested_url,
            final_url=safe_final_url,
            body=response.body,
            fetched_at=response.fetched_at,
            content_type=media_type,
            encoding=encoding,
            cache_path=body_path,
            sha256=digest,
            from_cache=False,
        )

    def _request_once(self, url: str) -> _RawResponse:
        global _LAST_REQUEST_AT
        now = time.monotonic()
        if _LAST_REQUEST_AT is not None:
            remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - _LAST_REQUEST_AT)
            if remaining > 0:
                time.sleep(remaining)
        _LAST_REQUEST_AT = time.monotonic()
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
        self.request_count += 1
        try:
            try:
                response = _OPENER.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return _RawResponse(
                    url=_canonical_url(response.geturl() or url),
                    status=int(getattr(response, "status", response.getcode())),
                    body=response.read(),
                    headers={
                        str(key): str(value) for key, value in response.headers.items()
                    },
                    fetched_at=_utc_now(),
                )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FetchError(f"取得に失敗しました: {url}: {error}") from error

    @staticmethod
    def _redirect_target(response: _RawResponse) -> str | None:
        if response.status not in {301, 302, 303, 307, 308}:
            return None
        location = response.headers.get("Location", response.headers.get("location"))
        if not location:
            raise FetchError(f"Location のないリダイレクトです: {response.url}")
        return _canonical_url(urllib.parse.urljoin(response.url, location))

    def _robots_parser(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = _origin(url)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        robots_key = "robots:" + robots_url
        # RFC 9309 section 2.3.1.3 treats HTTP 4xx as an unavailable
        # robots.txt. Keep 401/403 conservative, but allow other 4xx statuses.
        accepted = set(range(200, 300)) | set(range(400, 500))
        cached = (
            None
            if self.refresh
            else self._read_cached(robots_key, accepted_statuses=accepted)
        )
        if cached:
            result, status = cached
        else:
            if self.offline:
                raise OfflineCacheMiss(
                    f"robots.txt のキャッシュがありません: {robots_url}"
                )
            current_url = robots_url
            request_log: list[dict[str, Any]] = []
            for _ in range(_MAX_REDIRECTS + 1):
                response = self._request_once(current_url)
                request_log.append(
                    {
                        "url": current_url,
                        "resolved_url": response.url,
                        "status": response.status,
                        "fetched_at": response.fetched_at,
                    }
                )
                target = self._redirect_target(response)
                if target:
                    # RFC 9309 section 2.3.1.2 allows redirects across
                    # authorities. The resulting rules still apply to the
                    # authority of the originally requested robots.txt.
                    current_url = target
                    continue
                if response.status not in accepted:
                    raise RobotsUnavailableError(
                        f"robots.txt を確認できませんでした: HTTP {response.status}"
                    )
                result = self._write_cache(
                    robots_key,
                    robots_url,
                    response,
                    request_log,
                    sensitive_query_keys=set(),
                )
                status = response.status
                break
            else:
                raise RobotsUnavailableError(
                    "robots.txt のリダイレクト回数が上限を超えました"
                )
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        if status in {401, 403}:
            lines = ["User-agent: *", "Disallow: /"]
        elif status in range(400, 500):
            lines = ["User-agent: *", "Disallow:"]
        else:
            lines = result.text().splitlines()
        parser.parse(lines)
        self._robots[origin] = parser
        return parser

    def _assert_robots_allowed(self, url: str) -> None:
        parser = self._robots_parser(url)
        if not parser.can_fetch(USER_AGENT, url):
            raise RobotsDeniedError(f"robots.txt により取得できません: {url}")

    def fetch(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        sensitive_query_keys: set[str] | None = None,
    ) -> FetchResult:
        """Fetch a URL or return its verified cached response."""

        requested_url = _canonical_url(url)
        secret_keys = sensitive_query_keys or set()
        safe_url = _redact_url(requested_url, secret_keys)
        key = cache_key or "url:" + safe_url
        body_path, metadata_path = _cache_files(self.cache_dir, key)
        cache_available = body_path.is_file() and metadata_path.is_file()
        cached = (
            None
            if self.refresh
            else self._read_cached(key, accepted_statuses=range(200, 300))
        )
        if cached:
            self.cache_hit_count += 1
            self._record_fetch(
                cache_key=key,
                url=safe_url,
                status="cache_hit",
                result=cached[0],
            )
            return cached[0]
        if self.offline:
            self.cache_miss_count += 1
            self._record_fetch(
                cache_key=key,
                url=safe_url,
                status="cache_miss",
            )
            raise OfflineCacheMiss(f"キャッシュがありません: {safe_url}")

        with _REQUEST_LOCK:
            cached = (
                None
                if self.refresh
                else self._read_cached(key, accepted_statuses=range(200, 300))
            )
            if cached:
                self.cache_hit_count += 1
                self._record_fetch(
                    cache_key=key,
                    url=safe_url,
                    status="cache_hit",
                    result=cached[0],
                )
                return cached[0]
            current_url = requested_url
            request_log: list[dict[str, Any]] = []
            for _ in range(_MAX_REDIRECTS + 1):
                self._assert_robots_allowed(current_url)
                response = self._request_once(current_url)
                request_log.append(
                    {
                        "url": _redact_url(current_url, secret_keys),
                        "resolved_url": _redact_url(response.url, secret_keys),
                        "status": response.status,
                        "fetched_at": response.fetched_at,
                    }
                )
                target = self._redirect_target(response)
                if target:
                    current_url = target
                    continue
                if response.status < 200 or response.status >= 300:
                    raise FetchError(
                        f"取得に失敗しました: HTTP {response.status}: {safe_url}"
                    )
                result = self._write_cache(
                    key,
                    requested_url,
                    response,
                    request_log,
                    sensitive_query_keys=secret_keys,
                )
                status = "refreshed" if self.refresh and cache_available else "fetched"
                if status == "refreshed":
                    self.refresh_count += 1
                elif not cache_available:
                    self.cache_miss_count += 1
                self._record_fetch(
                    cache_key=key,
                    url=safe_url,
                    status=status,
                    result=result,
                )
                return result
            raise FetchError("リダイレクト回数が上限を超えました")
