#!/usr/bin/env python3
"""Ingest regulations from a user-supplied D1-Law (d1w_reiki) frame-type index URL.

Kohoku town pattern (live-scouted + live-verified 2026-08-21):

* Entry ``reiki.html`` is a ``FRAMESET``. Frames point to
  ``mokuji_bunya.html`` -> ``mokuji_bunya_chiled.html`` ->
  ``mokuji_bunya_index.html`` (left tree with real ``<a href=bunya_*.html>``
  links, ~72) and ``bunya_0010000.html`` (right list).
* ``bunya_*`` pages contain ``<A HREF="javascript:OpenResDataWin('<id>')">``
  entries (``title`` holds date/number, visible text holds regulation name).
* Verified live: ``OpenResDataWin(id)`` maps to the observed fixed spec
  ``./<id>/<id>.html`` which is itself a frameset pointing to
  ``<id>_m.html`` (toc) and ``<id>_j.html`` (main text containing ``第N条``).
  Some tenants (e.g. Kiyama) use a landing page with a real
  ``<a href=mokuji_bunya.html>`` instead of a FRAMESET entry; the BFS follows
  either, so both entry shapes work with the same ``--index-url``.
  The adapter derives ``<id>/<id>_j.html`` directly relative to the
  ``index_url`` directory and fetches only that ``_j.html``; the intermediate
  ``<id>.html`` frameset is never fetched. Any fetch failure on a derived
  ``_j.html`` URL is a per-document safe stop (recorded, not retried via a
  guessed alternative).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.parse
from collections import deque
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lcaios.http import (
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchError,
    FetchResult,
    HttpClient,
    RobotsDeniedError,
    RobotsUnavailableError,
)
from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_database_run,
)
from modules.regulations.ingest import (
    _infer_date,
    ensure_schema,
    segment_articles,
    stable_id,
    store_document,
)

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache" / "d1law"
MAX_NAVIGATION_PAGES = 128

_BLOCK_TAGS = {
    "article",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+(?:charset\\s*=\\s*[\"']?\\s*|content\\s*=\\s*[\"'][^\"']*charset=)([a-zA-Z0-9._-]+)",
    re.I,
)
_OPEN_RE = re.compile(r"OpenResDataWin\s*\(\s*['\"]([^'\"\\)]+)['\"]\s*\)", re.I)


class StructureMismatchError(RuntimeError):
    """Raised when the expected D1-Law frame structure is absent."""

    status = "structure_mismatch"


def _collapse(value: str) -> str:
    return re.sub(r"[ \\t\\r\\v　]+", " ", value).strip()


def normalize_index_url(value: str) -> str:
    """Validate a D1-Law index URL without deriving or guessing a tenant."""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("index URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("index URL must not contain credentials")
    if not parsed.path:
        raise ValueError("index URL must contain a path")
    # Keep query (D1-Law opensearch uses ?jctcd=), drop fragment.
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )


def _normalize_encoding(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("\"'").lower().replace("_", "-")
    if normalized in {"shift-jis", "shiftjis", "sjis", "windows-31j", "ms932"}:
        return "cp932"
    return normalized


def decode_html(body: bytes, declared_encoding: str | None = None) -> tuple[str, str]:
    """Decode D1-Law HTML, including UTF-8 and Shift_JIS/Windows-31J pages."""
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


class _LinkParser(HTMLParser):
    """Collect frame srcs and anchor hrefs without evaluating scripts."""

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
        src = attributes.get("src")
        if tag in {"frame", "iframe"} and src:
            self.links.append((src, "", tag))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, _collapse("".join(self._link_text)), "a"))
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)


class _D1TextParser(HTMLParser):
    """Extract visible text and title from a D1-Law _j.html page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title_depth = 0
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._title_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._title_depth:
            self.title += data
        self._parts.append(data)

    def visible_text(self) -> str:
        lines = [_collapse(line) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _same_host(url: str, index_url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(index_url)
    return (
        parsed.scheme.lower() in {"http", "https"}
        and base.scheme.lower() in {"http", "https"}
        and parsed.netloc.lower() == base.netloc.lower()
    )


def _resolve_link(page_url: str, index_url: str, href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    parsed_href = urllib.parse.urlsplit(href)
    if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
        return None
    # javascript: pseudo-links are handled via OpenResDataWin extraction, not navigation.
    if parsed_href.scheme and parsed_href.scheme.lower() == "javascript":
        return None
    if href.strip().lower().startswith("javascript:"):
        return None
    resolved = urllib.parse.urljoin(page_url, href)
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    resolved = urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )
    return resolved if _same_host(resolved, index_url) else None


def _derived_j_url(index_url: str, doc_id: str) -> str:
    """Derive the _j.html URL for a D1-Law id using the observed fixed spec.

    Observed fixed spec (documented): ``OpenResDataWin('<id>')`` maps to
    ``./<id>/<id>.html`` (a frameset of ``<id>_m.html`` + ``<id>_j.html``).
    The adapter fetches ``<id>/<id>_j.html`` directly relative to the
    ``index_url`` directory (``urljoin(index_url, "./")``) and treats any
    fetch failure on that derived URL as a per-document safe stop with no
    guess-retry.
    """
    base_dir = urllib.parse.urljoin(index_url, "./")
    # doc_id is expected to be a simple identifier without path separators.
    if "/" in doc_id or "\\" in doc_id or not doc_id.strip():
        raise ValueError(f"invalid D1-Law id: {doc_id!r}")
    return urllib.parse.urljoin(base_dir, f"{doc_id}/{doc_id}_j.html")


def _page_links(
    fetched: FetchResult,
    index_url: str,
) -> tuple[list[tuple[str, str, str]], str]:
    html, encoding = decode_html(fetched.body, fetched.encoding)
    parser = _LinkParser()
    parser.feed(html)
    resolution_base = fetched.final_url
    if parser.base_href:
        candidate = _resolve_link(fetched.final_url, index_url, parser.base_href)
        if candidate:
            resolution_base = candidate
    links: list[tuple[str, str, str]] = []
    for href, label, kind in parser.links:
        # Keep javascript: hrefs for id extraction; resolve others.
        if href.strip().lower().startswith("javascript:"):
            links.append((href, label, kind))
            continue
        resolved = _resolve_link(resolution_base, index_url, href)
        if resolved:
            links.append((resolved, label, kind))
        elif kind in {"frame", "iframe"}:
            # frame src that failed host check is simply ignored (host drift safe stop)
            continue
    return links, encoding


def discover_documents(
    index_url: str,
    *,
    client: HttpClient,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Discover D1-Law regulations via frameset traversal and OpenResDataWin ids.

    From the user-supplied ``index_url``, follow ``FRAMESET`` ``src`` attributes
    and real ``<a href>`` links (same-host only) through the left-tree
    ``mokuji``/``bunya`` pages, collect ``OpenResDataWin('<id>')`` ids from
    ``bunya_*`` pages, and return refs whose ``source_url`` is the derived
    ``<id>/<id>_j.html`` URL.
    """
    index_url = normalize_index_url(index_url)
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    queue: deque[str] = deque([index_url])
    queued: set[str] = {index_url}
    visited: set[str] = set()
    seen_ids: set[str] = set()
    refs: list[dict[str, Any]] = []

    while queue and len(visited) < MAX_NAVIGATION_PAGES:
        page_url = queue.popleft()
        fetched = client.fetch(page_url, tier=CacheTier.INDEX)
        if not _same_host(fetched.final_url, index_url):
            raise StructureMismatchError(
                "D1-Law index redirected outside the supplied index host"
            )
        visited.add(page_url)
        links, _encoding = _page_links(fetched, index_url)

        for href, label, _kind in links:
            match = _OPEN_RE.search(href)
            if not match:
                continue
            doc_id = match.group(1).strip()
            if not doc_id or doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            try:
                j_url = _derived_j_url(index_url, doc_id)
            except ValueError:
                continue
            if not _same_host(j_url, index_url):
                continue
            refs.append(
                {
                    "document_id": stable_id("regdoc", j_url),
                    "source_url": j_url,
                    "title": label or doc_id,
                    "discovered_from": fetched.final_url,
                    "d1law_id": doc_id,
                }
            )
            if limit is not None and len(refs) >= limit:
                return refs

        for href, _label, _kind in links:
            if _OPEN_RE.search(href):
                continue
            if href.strip().lower().startswith("javascript:"):
                continue
            # href at this point is either already resolved (same-host) or raw frame src.
            # For frame src we already resolved; for <a> we resolved to absolute.
            # _page_links returns resolved absolute for navigable links, raw for javascript.
            # So here href is either javascript (skipped) or absolute.
            # Detect absolute by scheme.
            parsed = urllib.parse.urlsplit(href)
            resolved: str | None
            if parsed.scheme.lower() in {"http", "https"}:
                resolved = href
            else:
                resolved = _resolve_link(fetched.final_url, index_url, href)
                if not resolved:
                    continue
            if resolved in visited or resolved in queued:
                continue
            # Only enqueue http(s) same-host links; _resolve already enforced.
            if not _same_host(resolved, index_url):
                continue
            queue.append(resolved)
            queued.add(resolved)

    if queue:
        raise StructureMismatchError(
            "D1-Law index navigation exceeded the safety page bound"
        )
    if not refs:
        raise StructureMismatchError(
            "expected D1-Law OpenResDataWin links were not found from the index"
        )
    return refs


def fetch_document(
    ref: dict[str, Any],
    *,
    index_url: str,
    client: HttpClient,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Fetch and normalize one D1-Law regulation from its derived _j.html."""
    index_url = normalize_index_url(index_url)
    fetched = client.fetch(ref["source_url"], tier=CacheTier.DOCUMENT)
    if not _same_host(fetched.final_url, index_url):
        raise StructureMismatchError(
            "D1-Law regulation page redirected outside the supplied index host"
        )
    html, encoding = decode_html(fetched.body, fetched.encoding)
    parser = _D1TextParser()
    parser.feed(html)
    text = parser.visible_text()
    if not text:
        raise StructureMismatchError(
            f"expected D1-Law body was not found: {ref['source_url']}"
        )
    articles = segment_articles(text)
    for article in articles:
        line_locator = str(article.get("locator") or "document:1")
        article["locator"] = f"{fetched.final_url}; {line_locator}"
    title = _collapse(parser.title) or str(ref.get("title") or fetched.final_url)
    resolved_source_name = source_name or (
        f"d1-law official regulations ({urllib.parse.urlsplit(index_url).netloc})"
    )
    issues = (
        [] if articles else ["本文は取得できましたが条単位に分割できませんでした。"]
    )
    return {
        "document": {
            "document_id": ref.get("document_id")
            or stable_id("regdoc", ref["source_url"]),
            "title": title,
            "category": None,
            "source_url": ref["source_url"],
            "source_name": resolved_source_name,
            "promulgated_on": _infer_date(text[:2000]),
            "enforced_on": None,
            "fetched_at": fetched.fetched_at,
            "adapter": "d1_law",
            "verification_state": "discovered",
        },
        "articles": articles,
        "provenance": {
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": fetched.content_type,
            "content_sha256": fetched.sha256,
            "adapter": "d1_law",
            "transform": {
                "extractor": "d1-law _j.html via stdlib HTMLParser",
                "encoding": encoding,
                "segmentation": "article_heading_or_document_fallback",
            },
            "status": "extracted" if articles else "text_without_articles",
            "cache_path": str(fetched.cache_path),
            "issues": issues,
        },
    }


def ingest_d1law(
    index_url: str,
    db_path: str | Path,
    *,
    source_name: str | None = None,
    cache_dir: str | Path | None = None,
    limit: int | None = None,
    offline: bool = False,
    refresh: bool = False,
    timeout: float = 90,
) -> dict[str, Any]:
    """Discover, fetch, and store D1-Law regulations.

    Derived ``_j.html`` fetch failures are per-document safe stops: the
    document is counted in ``statuses["fetch_failed"]`` and the ingest
    continues without retrying a guessed alternative URL.
    """
    index_url = normalize_index_url(index_url)
    client = HttpClient(
        cache_dir or DEFAULT_CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        offline=offline,
        refresh=refresh,
        timeout=timeout,
        min_interval_seconds=1.5,
    )
    refs = discover_documents(index_url, client=client, limit=limit)
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        fts_schema = ensure_schema(connection)
        document_count = 0
        article_count = 0
        statuses: dict[str, int] = {}
        failures: list[dict[str, str]] = []
        for ref in refs:
            try:
                payload = fetch_document(
                    ref,
                    index_url=index_url,
                    client=client,
                    source_name=source_name,
                )
            except (
                FetchError,
                RobotsDeniedError,
                RobotsUnavailableError,
                StructureMismatchError,
                ValueError,
            ) as exc:
                statuses["fetch_failed"] = statuses.get("fetch_failed", 0) + 1
                failures.append(
                    {"source_url": str(ref.get("source_url")), "error": str(exc)}
                )
                continue
            except (OSError, sqlite3.Error) as exc:
                statuses["fetch_failed"] = statuses.get("fetch_failed", 0) + 1
                failures.append(
                    {"source_url": str(ref.get("source_url")), "error": str(exc)}
                )
                continue
            article_count += store_document(connection, payload)
            status = str(payload["provenance"]["status"])
            statuses[status] = statuses.get(status, 0) + 1
            document_count += 1
        connection.commit()
    result: dict[str, Any] = {
        "status": "ok",
        "database": str(database),
        "index_url": index_url,
        "documents": document_count,
        "articles": article_count,
        "statuses": statuses,
        "fts_tokenizer": fts_schema["tokenizer"],
        "fts_schema": fts_schema,
        "retrieval": client.retrieval_report(),
    }
    if failures:
        result["failures"] = failures
    return result


# Backwards-compatible alias for the module name.
ingest_d1law_reiki = ingest_d1law


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-url",
        required=True,
        help="User-supplied D1-Law index URL (frame entry, e.g. reiki.html)",
    )
    parser.add_argument("--db", required=True, help="Output SQLite database")
    parser.add_argument("--source-name", help="Official source label stored in the DB")
    parser.add_argument(
        "--limit", type=int, help="Document limit for verification runs"
    )
    parser.add_argument("--cache-dir", help="Override local cache directory")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ネットワークを使わず検証済みローカルキャッシュだけを使う",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="検証済みcacheがあっても公式参照先を再取得する",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90,
        help="取得タイムアウト秒",
    )
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
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
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    try:
        manifest_path, manifest = begin_module_run(
            args.manifest_dir,
            run_type="regulations",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={
                "adapter": "d1_law",
                "index_url": args.index_url,
                "database": args.db,
                "source_name": args.source_name,
                "limit": args.limit,
                "cache_directory": args.cache_dir or str(DEFAULT_CACHE_DIR),
                "offline": args.offline,
                "refresh": args.refresh,
                "timeout": args.timeout,
            },
        )
        result = ingest_d1law(
            args.index_url,
            args.db,
            source_name=args.source_name,
            cache_dir=args.cache_dir,
            limit=args.limit,
            offline=args.offline,
            refresh=args.refresh,
            timeout=args.timeout,
        )
    except StructureMismatchError as exc:
        fail_module_run(manifest_path, manifest, exc)
        error = {"status": exc.status, "error": str(exc)}
    except RobotsDeniedError as exc:
        fail_module_run(manifest_path, manifest, exc)
        error = {"status": "robots_denied", "error": str(exc)}
    except RobotsUnavailableError as exc:
        fail_module_run(manifest_path, manifest, exc)
        error = {"status": "robots_unavailable", "error": str(exc)}
    except (FetchError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        fail_module_run(manifest_path, manifest, exc)
        error = {"status": "error", "error": str(exc)}
    else:
        if manifest is not None:
            manifest["retrieval"] = result["retrieval"]
        finish_database_run(
            manifest_path,
            manifest,
            database=result["database"],
            artifact_kind="regulations_database",
            scope={"adapter": "d1_law", "action": "ingest"},
            coverage={
                "documents": result["documents"],
                "articles": result["articles"],
                "statuses": result["statuses"],
                "fts_tokenizer": result["fts_tokenizer"],
                "fts_schema": result["fts_schema"],
            },
            checks=[
                {
                    "name": "document_rows",
                    "status": "passed" if result["documents"] > 0 else "failed",
                    "detail": result["documents"],
                }
            ],
        )
        if manifest_path is not None:
            result["manifest"] = str(manifest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
