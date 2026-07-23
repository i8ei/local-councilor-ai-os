#!/usr/bin/env python3
"""Ingest regulations from a user-supplied g-reiki tenant base URL."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import urllib.parse
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

MODULE_DIR = Path(__file__).resolve().parent
MINUTES_MODULE = MODULE_DIR.parent / "minutes-db"
if str(MINUTES_MODULE) not in sys.path:
    sys.path.insert(0, str(MINUTES_MODULE))
if str(MODULE_DIR) in sys.path:
    sys.path.remove(str(MODULE_DIR))
sys.path.insert(0, str(MODULE_DIR))

from adapters import base as fetch_base  # type: ignore  # noqa: E402
from adapters.base import (  # type: ignore  # noqa: E402
    FetchError,
    FetchResult,
    RobotsDeniedError,
    RobotsUnavailableError,
)
from ingest import (  # noqa: E402
    _infer_date,
    ensure_schema,
    segment_articles,
    stable_id,
    store_document,
)


USER_AGENT = "local-councilor-ai-os regulations ingester (research; low rate)"
MIN_REQUEST_INTERVAL_SECONDS = 1.5
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache" / "greiki"
ENTRY_FILENAME = "reiki_menu.html"
MAX_NAVIGATION_PAGES = 64

_BLOCK_TAGS = {
    "article", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4",
    "h5", "h6", "li", "main", "p", "pre", "section", "table", "td", "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
_META_CHARSET_RE = re.compile(
    br"<meta[^>]+(?:charset\s*=\s*[\"']?\s*|content\s*=\s*[\"'][^\"']*charset=)([a-zA-Z0-9._-]+)",
    re.I,
)
_KANA_INDEX_RE = re.compile(r"(?:kana_default|r_50_[a-z]+)\.html$", re.I)
_REGULATION_RE = re.compile(r"reiki_honbun/[^/]+\.html$", re.I)
_FETCH_SETTINGS_LOCK = threading.RLock()


class StructureMismatchError(RuntimeError):
    """Raised when the expected g-reiki link structure is absent."""

    status = "structure_mismatch"


def _collapse(value: str) -> str:
    return re.sub(r"[ \t\r\v　]+", " ", value).strip()


def normalize_base_url(value: str) -> str:
    """Validate a tenant base URL without deriving or guessing a tenant."""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def _normalize_encoding(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("\"'").lower().replace("_", "-")
    if normalized in {"shift-jis", "shiftjis", "sjis", "windows-31j", "ms932"}:
        return "cp932"
    return normalized


def decode_html(body: bytes, declared_encoding: str | None = None) -> tuple[str, str]:
    """Decode g-reiki HTML, including UTF-8 and Shift_JIS/Windows-31J pages."""
    candidates: list[str] = []
    if body.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    elif body.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")
    match = _META_CHARSET_RE.search(body[:4096])
    if match:
        candidates.append(
            _normalize_encoding(match.group(1).decode("ascii", "ignore")) or "utf-8"
        )
    normalized_declared = _normalize_encoding(declared_encoding)
    if normalized_declared:
        candidates.append(normalized_declared)
    candidates.extend(("utf-8", "cp932"))
    for encoding in dict.fromkeys(candidates):
        try:
            return body.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


def fetch_url(
    url: str,
    *,
    cache_dir: str | Path | None = None,
    timeout: float = 30,
) -> FetchResult:
    """Use the shared polite fetcher with this adapter's honest user agent."""
    cache_root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    with _FETCH_SETTINGS_LOCK:
        previous_user_agent = fetch_base.USER_AGENT
        previous_interval = fetch_base.MIN_REQUEST_INTERVAL_SECONDS
        fetch_base.USER_AGENT = USER_AGENT
        fetch_base.MIN_REQUEST_INTERVAL_SECONDS = max(
            MIN_REQUEST_INTERVAL_SECONDS, previous_interval
        )
        try:
            return fetch_base.polite_fetch(url, cache_dir=cache_root, timeout=timeout)
        finally:
            fetch_base.USER_AGENT = previous_user_agent
            fetch_base.MIN_REQUEST_INTERVAL_SECONDS = previous_interval


class _LinkParser(HTMLParser):
    """Collect actual HTML links and frame sources without evaluating scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "base" and attributes.get("href") and self.base_href is None:
            self.base_href = attributes["href"]
        if tag == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_text = []
        if tag in {"frame", "iframe"} and attributes.get("src"):
            self.links.append((attributes["src"], "", tag))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(
                (self._href, _collapse("".join(self._link_text)), "a")
            )
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)


class _DocumentParser(HTMLParser):
    """Extract the g-reiki primary body and small document metadata fields."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.primary_found = False
        self.date_numbers: list[str] = []
        self.categories: list[str] = []
        self._title_depth = 0
        self._ignored_depth = 0
        self._div_depth = 0
        self._primary_div_depth: int | None = None
        self._primary_parts: list[str] = []
        self._captures: list[dict[str, Any]] = []

    @property
    def _in_primary(self) -> bool:
        return self._primary_div_depth is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            self._div_depth += 1
            if attributes.get("id") == "primary" and not self._in_primary:
                self.primary_found = True
                self._primary_div_depth = self._div_depth
        if tag == "title":
            self._title_depth += 1
        if "datenumber-area" in classes:
            self._captures.append({"tag": tag, "kind": "date", "parts": []})
        if "taikei-item" in classes:
            self._captures.append({"tag": tag, "kind": "category", "parts": []})
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if self._in_primary and tag in _BLOCK_TAGS:
            self._primary_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if not self._ignored_depth:
            if self._in_primary and tag in _BLOCK_TAGS:
                self._primary_parts.append("\n")
            if tag == "title" and self._title_depth:
                self._title_depth -= 1
            for index in range(len(self._captures) - 1, -1, -1):
                capture = self._captures[index]
                if capture["tag"] != tag:
                    continue
                value = _collapse("".join(capture["parts"]))
                if value:
                    target = (
                        self.date_numbers
                        if capture["kind"] == "date"
                        else self.categories
                    )
                    target.append(value)
                del self._captures[index]
                break
        if tag == "div":
            if self._primary_div_depth == self._div_depth:
                self._primary_div_depth = None
            self._div_depth = max(0, self._div_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._title_depth:
            self.title += data
        for capture in self._captures:
            capture["parts"].append(data)
        if self._in_primary:
            self._primary_parts.append(data)

    def handle_comment(self, data: str) -> None:
        if self._in_primary and _collapse(data).lower() == "secondary":
            self._primary_parts.append("\n")
            self._primary_div_depth = None

    def primary_text(self) -> str:
        lines = [_collapse(line) for line in "".join(self._primary_parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _same_tenant(url: str, base_url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(base_url)
    return (
        parsed.scheme.lower() in {"http", "https"}
        and base.scheme.lower() in {"http", "https"}
        and parsed.netloc.lower() == base.netloc.lower()
        and parsed.path.startswith(base.path)
    )


def _resolve_link(page_url: str, base_url: str, href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    parsed_href = urllib.parse.urlsplit(href)
    if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
        return None
    resolved = urllib.parse.urljoin(page_url, href)
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    resolved = urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )
    return resolved if _same_tenant(resolved, base_url) else None


def _tenant_relative_path(url: str, base_url: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    base_path = urllib.parse.unquote(urllib.parse.urlsplit(base_url).path)
    return path[len(base_path):].lstrip("/")


def _is_kana_index(url: str, base_url: str) -> bool:
    relative = _tenant_relative_path(url, base_url)
    return relative.lower().startswith("reiki_kana/") and bool(
        _KANA_INDEX_RE.search(relative)
    )


def _is_regulation(url: str, base_url: str) -> bool:
    return bool(_REGULATION_RE.fullmatch(_tenant_relative_path(url, base_url)))


def _is_navigation_candidate(url: str, base_url: str) -> bool:
    relative = _tenant_relative_path(url, base_url)
    lowered = relative.lower()
    if _is_kana_index(url, base_url):
        return True
    if lowered.startswith("reiki_taikei/") and lowered.endswith((".html", ".htm")):
        return True
    return "/" not in relative.rstrip("/") and lowered.endswith((".html", ".htm"))


def _page_links(
    fetched: FetchResult,
    base_url: str,
) -> tuple[list[tuple[str, str, str]], str]:
    html, encoding = decode_html(fetched.body, fetched.encoding)
    parser = _LinkParser()
    parser.feed(html)
    resolution_base = fetched.final_url
    if parser.base_href:
        candidate = _resolve_link(fetched.final_url, base_url, parser.base_href)
        if candidate:
            resolution_base = candidate
    links: list[tuple[str, str, str]] = []
    for href, label, kind in parser.links:
        resolved = _resolve_link(resolution_base, base_url, href)
        if resolved:
            links.append((resolved, label, kind))
    return links, encoding


def discover_documents(
    base_url: str,
    *,
    cache_dir: str | Path | None = None,
    limit: int | None = None,
    fetcher: Callable[..., FetchResult] = fetch_url,
) -> list[dict[str, Any]]:
    """Discover regulation pages by following only links present in g-reiki indexes."""
    base_url = normalize_base_url(base_url)
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    entry_url = urllib.parse.urljoin(base_url, ENTRY_FILENAME)
    queue: deque[str] = deque([entry_url])
    queued = {entry_url}
    visited: set[str] = set()
    saw_kana_index = False
    refs: list[dict[str, Any]] = []
    seen_documents: set[str] = set()

    while queue and len(visited) < MAX_NAVIGATION_PAGES:
        page_url = queue.popleft()
        fetched = fetcher(page_url, cache_dir=cache_dir)
        if not _same_tenant(fetched.final_url, base_url):
            raise StructureMismatchError(
                "g-reiki entry/index redirected outside the supplied tenant base URL"
            )
        visited.add(page_url)
        links, _encoding = _page_links(fetched, base_url)
        current_is_kana = _is_kana_index(fetched.final_url, base_url)
        saw_kana_index = saw_kana_index or current_is_kana

        if current_is_kana:
            queue = deque(url for url in queue if _is_kana_index(url, base_url))
            for url, label, _kind in links:
                if not _is_regulation(url, base_url) or url in seen_documents:
                    continue
                seen_documents.add(url)
                refs.append(
                    {
                        "document_id": stable_id("regdoc", url),
                        "source_url": url,
                        "title": label or Path(urllib.parse.urlsplit(url).path).name,
                        "discovered_from": fetched.final_url,
                    }
                )
                if limit is not None and len(refs) >= limit:
                    return refs

        candidates = [
            url
            for url, _label, _kind in links
            if _is_navigation_candidate(url, base_url)
            and (not saw_kana_index or _is_kana_index(url, base_url))
            and url not in visited
            and url not in queued
        ]
        candidates.sort(key=lambda url: (not _is_kana_index(url, base_url), url))
        for url in reversed(candidates):
            queue.appendleft(url)
            queued.add(url)

    if queue:
        raise StructureMismatchError(
            "g-reiki index navigation exceeded the safety page bound"
        )
    if not saw_kana_index:
        raise StructureMismatchError(
            "expected g-reiki 五十音 index links were not found from reiki_menu.html"
        )
    if not refs:
        raise StructureMismatchError(
            "g-reiki 五十音 index was found, but it contained no reiki_honbun links"
        )
    return refs


def fetch_document(
    ref: dict[str, Any],
    *,
    base_url: str,
    source_name: str | None = None,
    cache_dir: str | Path | None = None,
    fetcher: Callable[..., FetchResult] = fetch_url,
) -> dict[str, Any]:
    """Fetch and normalize one g-reiki regulation into the existing schema."""
    base_url = normalize_base_url(base_url)
    fetched = fetcher(ref["source_url"], cache_dir=cache_dir)
    if not _same_tenant(fetched.final_url, base_url):
        raise StructureMismatchError(
            "regulation page redirected outside the supplied tenant base URL"
        )
    html, encoding = decode_html(fetched.body, fetched.encoding)
    parser = _DocumentParser()
    parser.feed(html)
    text = parser.primary_text()
    if not parser.primary_found or not text:
        raise StructureMismatchError(
            f"expected g-reiki primary body was not found: {ref['source_url']}"
        )
    articles = segment_articles(text)
    for article in articles:
        line_locator = str(article.get("locator") or "document:1")
        article["locator"] = f"{fetched.final_url}; primary-{line_locator}"
    title = _collapse(parser.title) or str(ref.get("title") or fetched.final_url)
    category = " / ".join(dict.fromkeys(parser.categories)) or None
    resolved_source_name = source_name or (
        f"g-reiki official regulations ({urllib.parse.urlsplit(base_url).netloc})"
    )
    issues = [] if articles else ["本文は取得できましたが条単位に分割できませんでした。"]
    return {
        "document": {
            "document_id": ref.get("document_id")
            or stable_id("regdoc", ref["source_url"]),
            "title": title,
            "category": category,
            "source_url": ref["source_url"],
            "source_name": resolved_source_name,
            "promulgated_on": _infer_date(
                "\n".join(parser.date_numbers) or text[:2000]
            ),
            "enforced_on": None,
            "fetched_at": fetched.fetched_at,
            "adapter": "g_reiki",
            "verification_state": "discovered",
        },
        "articles": articles,
        "provenance": {
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": fetched.content_type,
            "content_sha256": fetched.sha256,
            "adapter": "g_reiki",
            "transform": {
                "extractor": "g-reiki div#primary via stdlib HTMLParser",
                "encoding": encoding,
                "segmentation": "article_heading_or_document_fallback",
            },
            "status": "extracted" if articles else "text_without_articles",
            "cache_path": str(fetched.cache_path),
            "issues": issues,
        },
    }


def ingest_greiki(
    base_url: str,
    db_path: str | Path,
    *,
    source_name: str | None = None,
    cache_dir: str | Path | None = None,
    limit: int | None = None,
    fetcher: Callable[..., FetchResult] = fetch_url,
) -> dict[str, Any]:
    """Discover, fetch, and store g-reiki regulations."""
    base_url = normalize_base_url(base_url)
    refs = discover_documents(
        base_url, cache_dir=cache_dir, limit=limit, fetcher=fetcher
    )
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        tokenizer = ensure_schema(connection)
        document_count = 0
        article_count = 0
        statuses: dict[str, int] = {}
        for ref in refs:
            payload = fetch_document(
                ref,
                base_url=base_url,
                source_name=source_name,
                cache_dir=cache_dir,
                fetcher=fetcher,
            )
            article_count += store_document(connection, payload)
            status = str(payload["provenance"]["status"])
            statuses[status] = statuses.get(status, 0) + 1
            document_count += 1
        connection.commit()
    return {
        "status": "ok",
        "database": str(database),
        "base_url": base_url,
        "documents": document_count,
        "articles": article_count,
        "statuses": statuses,
        "fts_tokenizer": tokenizer,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help="User-supplied g-reiki tenant base URL ending at the tenant directory",
    )
    parser.add_argument("--db", required=True, help="Output SQLite database")
    parser.add_argument("--source-name", help="Official source label stored in the DB")
    parser.add_argument("--limit", type=int, help="Document limit for verification runs")
    parser.add_argument("--cache-dir", help="Override local cache directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print(
            json.dumps(
                {"status": "invalid_arguments", "error": "--limit must be at least 1"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        result = ingest_greiki(
            args.base_url,
            args.db,
            source_name=args.source_name,
            cache_dir=args.cache_dir,
            limit=args.limit,
        )
    except StructureMismatchError as exc:
        error = {"status": exc.status, "error": str(exc)}
    except RobotsDeniedError as exc:
        error = {"status": "robots_denied", "error": str(exc)}
    except RobotsUnavailableError as exc:
        error = {"status": "robots_unavailable", "error": str(exc)}
    except (FetchError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        error = {"status": "error", "error": str(exc)}
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
