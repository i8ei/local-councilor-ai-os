#!/usr/bin/env python3
"""Ingest regulations from a user-supplied joureikun catalog index URL."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.parse
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

from ._parsers import LinkParser

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache" / "joureikun"

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
_ACT_RE = re.compile(r"/joureikun/act/\d+(?:_\d+)?\.html$", re.I)


class StructureMismatchError(RuntimeError):
    """Raised when the expected joureikun link structure is absent."""

    status = "structure_mismatch"


def _collapse(value: str) -> str:
    return re.sub(r"[ \t\r\v　]+", " ", value).strip()


def normalize_index_url(value: str) -> str:
    """Validate a joureikun catalog index URL."""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("index URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("index URL must not contain credentials")
    if not parsed.path:
        raise ValueError("index URL must contain a path")
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )


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
    resolved = urllib.parse.urljoin(page_url, href)
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    resolved = urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )
    return resolved if _same_host(resolved, index_url) else None


def _is_act_url(url: str, index_url: str) -> bool:
    if not _same_host(url, index_url):
        return False
    path = urllib.parse.urlsplit(url).path
    return bool(_ACT_RE.search(path))



class _TextParser(HTMLParser):
    """Extract visible text and title from a joureikun act page."""

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


def _page_links(
    fetched: FetchResult,
    index_url: str,
) -> list[tuple[str, str]]:
    html = fetched.text()
    parser = LinkParser()
    parser.feed(html)
    resolution_base = fetched.final_url
    if parser.base_href:
        candidate = _resolve_link(fetched.final_url, index_url, parser.base_href)
        if candidate:
            resolution_base = candidate
    links: list[tuple[str, str]] = []
    for href, label in parser.links:
        resolved = _resolve_link(resolution_base, index_url, href)
        if resolved:
            links.append((resolved, label))
    return links


def discover_documents(
    index_url: str,
    *,
    client: HttpClient,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Discover act pages by following only links present in the catalog."""
    index_url = normalize_index_url(index_url)
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    fetched = client.fetch(index_url, tier=CacheTier.INDEX)
    if not _same_host(fetched.final_url, index_url):
        raise StructureMismatchError(
            "joureikun catalog redirected outside the supplied index host"
        )
    links = _page_links(fetched, index_url)
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for url, label in links:
        if not _is_act_url(url, index_url):
            continue
        if url in seen:
            continue
        seen.add(url)
        refs.append(
            {
                "document_id": stable_id("regdoc", url),
                "source_url": url,
                "title": label or Path(urllib.parse.urlsplit(url).path).name,
                "discovered_from": fetched.final_url,
            }
        )
        if limit is not None and len(refs) >= limit:
            break
    if not refs:
        raise StructureMismatchError(
            "expected joureikun act links were not found in the catalog"
        )
    return refs


def fetch_document(
    ref: dict[str, Any],
    *,
    index_url: str,
    client: HttpClient,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Fetch and normalize one joureikun act into the existing schema."""
    index_url = normalize_index_url(index_url)
    fetched = client.fetch(ref["source_url"], tier=CacheTier.DOCUMENT)
    if not _same_host(fetched.final_url, index_url):
        raise StructureMismatchError(
            "act page redirected outside the supplied index host"
        )
    # Use text detection via HttpClient encoding; visible text via parser.
    parser = _TextParser()
    # Detect raw HTML via content type or leading markup.
    is_html = fetched.content_type in {
        "text/html",
        "application/xhtml+xml",
    } or fetched.text().lstrip().startswith("<")
    if is_html:
        parser.feed(fetched.text())
        text = parser.visible_text()
        title = _collapse(parser.title) or str(ref.get("title") or fetched.final_url)
        extractor = "joureikun visible text via stdlib HTMLParser"
    else:
        text = fetched.text()
        title = str(ref.get("title") or fetched.final_url)
        extractor = "plain-text"
    if not text:
        raise StructureMismatchError(
            f"expected joureikun act body was not found: {ref['source_url']}"
        )
    articles = segment_articles(text)
    for article in articles:
        line_locator = str(article.get("locator") or "document:1")
        article["locator"] = f"{fetched.final_url}; {line_locator}"
    resolved_source_name = source_name or (
        f"joureikun official regulations ({urllib.parse.urlsplit(index_url).netloc})"
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
            "adapter": "joureikun",
            "verification_state": "discovered",
        },
        "articles": articles,
        "provenance": {
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": fetched.content_type,
            "content_sha256": fetched.sha256,
            "adapter": "joureikun",
            "transform": {
                "extractor": extractor,
                "encoding": fetched.encoding,
                "segmentation": "article_heading_or_document_fallback",
            },
            "status": "extracted" if articles else "text_without_articles",
            "cache_path": str(fetched.cache_path),
            "issues": issues,
        },
    }


def ingest_joureikun(
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
    """Discover, fetch, and store joureikun regulations."""
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
        for ref in refs:
            payload = fetch_document(
                ref,
                index_url=index_url,
                client=client,
                source_name=source_name,
            )
            article_count += store_document(connection, payload)
            status = str(payload["provenance"]["status"])
            statuses[status] = statuses.get(status, 0) + 1
            document_count += 1
        connection.commit()
    return {
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-url",
        required=True,
        help="User-supplied joureikun catalog index URL",
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
                "adapter": "joureikun",
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
        result = ingest_joureikun(
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
            scope={"adapter": "joureikun", "action": "ingest"},
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
