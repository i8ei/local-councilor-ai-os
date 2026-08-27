#!/usr/bin/env python3
"""Ingest regulations from a user-supplied D1-Law OpenSearch index URL.

OpenSearch pattern (live-scouted across 93 municipalities in Japan):

* Modern D1-Law portal (e.g. ``https://ops-jg.d1-law.com/opensearch/?jctcd=<CODE>``
  or ``https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=<CODE>``).
* Entry URL loads the table of contents tree (``id="mokujiSearch"``) with
  categories (``001:00:00``, ``002:00:00``, ...).
* Category search URL ``/opensearch/SrMjF01/search?typeSearch=SrMj_Genko&mokujicd=<CD>``
  returns table rows containing ``doViewJobunFromJsp('<jctcd>', '<houcd>', ...)``.
* Document view URL ``/opensearch/SrJbF01/init?jctcd=<jctcd>&houcd=<houcd>&fromJsp=SrMj``
  returns the complete HTML regulation text with articles in ``<div class="contents-lineheight-2">``.
"""

from __future__ import annotations

import argparse
import html as html_lib
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
    _OPENER,
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchError,
    HttpClient,
    RobotsDeniedError,
    RobotsUnavailableError,
)
from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_database_run,
)
from lcaios.text import collapse_ascii as _collapse
from modules.regulations.ingest import (
    _infer_date,
    ensure_schema,
    segment_articles,
    stable_id,
    store_document,
)

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache" / "d1law_opensearch"
MAX_NAVIGATION_CATEGORIES = 32

_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
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

_HOUCD_RE = re.compile(
    r"doViewJobun(?:FromJsp)?\s*\(\s*['\"]([^'\"\\)]+)['\"]\s*,\s*['\"]([^'\"\\)]+)['\"]",
    re.I,
)
_MKJG_RE = re.compile(r"mkjG\s*\(\s*['\"]([^'\"\\)]+)['\"]\s*\)", re.I)


class StructureMismatchError(RuntimeError):
    """Raised when the expected D1-Law OpenSearch structure is absent."""

    status = "structure_mismatch"


def extract_jctcd(url: str) -> str | None:
    """Extract the jctcd query parameter from an OpenSearch URL."""
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)
    # Search for jctcd (case-insensitive)
    for key, values in params.items():
        if key.lower() == "jctcd" and values:
            val = values[0].strip()
            if val:
                return val
    return None


def normalize_index_url(value: str) -> str:
    """Validate and canonicalize a D1-Law OpenSearch index URL."""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("index URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("index URL must not contain credentials")
    jctcd = extract_jctcd(value)
    if not jctcd:
        raise ValueError("index URL must contain a valid jctcd parameter")
    host = parsed.netloc
    return f"https://{host}/opensearch/SrMjF01/init?jctcd={jctcd}"


def _same_host(url: str, index_url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(index_url)
    parsed_host = (parsed.hostname or "").lower()
    base_host = (base.hostname or "").lower()
    return (
        parsed.scheme.lower() in {"http", "https"}
        and base.scheme.lower() in {"http", "https"}
        and parsed_host == base_host
    )


class _D1OpenSearchTextParser(HTMLParser):
    """HTML parser to extract regulation text from OpenSearch jobun HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.in_ignored = False
        self.lines: list[str] = []
        self.current_line: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in _IGNORED_TAGS:
            self.in_ignored = True
        elif tag_lower == "title":
            self.in_title = True
        if tag_lower in _BLOCK_TAGS:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in _IGNORED_TAGS:
            self.in_ignored = False
        elif tag_lower == "title":
            self.in_title = False
        if tag_lower in _BLOCK_TAGS:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self.in_ignored:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        txt = data.strip()
        if txt:
            self.current_line.append(data)

    def _flush_line(self) -> None:
        if self.current_line:
            combined = "".join(self.current_line)
            collapsed = _collapse(combined)
            if collapsed:
                self.lines.append(collapsed)
            self.current_line = []

    @property
    def title(self) -> str:
        raw = "".join(self.title_parts)
        cleaned = html_lib.unescape(raw)
        # Often title is "条例名 市町村名例規集（都道府県名）"
        # Extract before the city reiki suffix if present
        parts = cleaned.split(" ")
        return parts[0].strip() if parts else cleaned.strip()

    def visible_text(self) -> str:
        self._flush_line()
        return "\n".join(self.lines)


def _extract_mokujicds(html_text: str) -> list[str]:
    """Extract category codes from mokuji tree HTML."""
    cds: list[str] = []
    seen: set[str] = set()

    # Find all mkjG('...') calls
    for match in _MKJG_RE.finditer(html_text):
        cd = match.group(1).strip()
        if cd and cd not in seen:
            seen.add(cd)
            cds.append(cd)

    # Find all li id="001:00:00..."
    for match in re.finditer(r'id=["\'](\d{3}:\d{2}:\d{2}[^"\']*)["\']', html_text):
        cd = match.group(1).strip()
        if cd and cd not in seen:
            seen.add(cd)
            cds.append(cd)

    # Fallback to standard top-level categories if none found in HTML
    if not cds:
        for i in range(1, 16):
            cds.append(f"{i:03d}:00:00")

    return cds[:MAX_NAVIGATION_CATEGORIES]


def _extract_regulations_from_search(
    search_html: str, *, jctcd: str, host: str, search_url: str
) -> list[dict[str, Any]]:
    """Parse regulation rows from /opensearch/SrMjF01/search response HTML."""
    refs: list[dict[str, Any]] = []
    seen_houcds: set[str] = set()

    # Pattern: doViewJobunFromJsp('jctcd', 'houcd', ...) with link label
    for match in _HOUCD_RE.finditer(search_html):
        m_jctcd = match.group(1).strip() or jctcd
        houcd = match.group(2).strip()
        if not houcd or houcd in seen_houcds:
            continue
        seen_houcds.add(houcd)

        # Find title near the match
        pos = match.start()
        after = search_html[pos : pos + 500]
        title_match = re.search(r">([^<]+)<", after)
        title = _collapse(title_match.group(1)) if title_match else houcd
        if not title or title.startswith("javascript:"):
            title = houcd

        doc_url = f"https://{host}/opensearch/SrJbF01/init?jctcd={m_jctcd}&houcd={houcd}&fromJsp=SrMj"
        refs.append(
            {
                "document_id": stable_id("regdoc", doc_url),
                "source_url": doc_url,
                "title": title,
                "discovered_from": search_url,
                "houcd": houcd,
                "jctcd": m_jctcd,
            }
        )

    return refs


def discover_documents(
    index_url: str,
    *,
    client: HttpClient,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Discover regulations from a D1-Law OpenSearch index URL."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")

    canonical_index = normalize_index_url(index_url)
    jctcd = extract_jctcd(canonical_index)
    if not jctcd:
        raise StructureMismatchError("missing jctcd in D1-Law OpenSearch index URL")

    parsed = urllib.parse.urlsplit(canonical_index)
    host = parsed.netloc

    # 1. Fetch init page (loads session and mokuji tree)
    init_res = client.fetch(canonical_index, tier=CacheTier.INDEX)
    if not _same_host(init_res.final_url, canonical_index):
        raise StructureMismatchError(
            "D1-Law OpenSearch index redirected outside the supplied index host"
        )

    # If init was served from disk cache and client is online, ping init once to establish live session cookies
    if getattr(init_res, "from_cache", False) and not getattr(client, "offline", False):
        try:
            req = urllib.request.Request(
                canonical_index,
                headers={"User-Agent": getattr(client, "user_agent", REGULATIONS_USER_AGENT), "Accept": "*/*"},
            )
            with _OPENER.open(req, timeout=getattr(client, "timeout", 30)):
                pass
        except Exception:
            pass

    init_html = init_res.text()
    mokujicds = _extract_mokujicds(init_html)

    refs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # 2. Iterate through categories and fetch search results
    for cd in mokujicds:
        search_url = f"https://{host}/opensearch/SrMjF01/search?typeSearch=SrMj_Genko&mokujicd={cd}"
        try:
            search_res = client.fetch(search_url, tier=CacheTier.INDEX)
        except Exception:
            continue

        if not _same_host(search_res.final_url, canonical_index):
            continue

        cat_refs = _extract_regulations_from_search(
            search_res.text(), jctcd=jctcd, host=host, search_url=search_url
        )
        for r in cat_refs:
            if r["source_url"] not in seen_urls:
                seen_urls.add(r["source_url"])
                refs.append(r)
                if limit is not None and len(refs) >= limit:
                    return refs

    if not refs:
        raise StructureMismatchError(
            "expected D1-Law OpenSearch regulation links were not found from the index"
        )

    return refs


def fetch_document(
    ref: dict[str, Any],
    *,
    index_url: str,
    client: HttpClient,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Fetch and normalize one D1-Law regulation from its OpenSearch jobun URL."""
    canonical_index = normalize_index_url(index_url)
    fetched = client.fetch(ref["source_url"], tier=CacheTier.DOCUMENT)
    if not _same_host(fetched.final_url, canonical_index):
        raise StructureMismatchError(
            "D1-Law regulation page redirected outside the supplied index host"
        )

    html_text = fetched.text()
    parser = _D1OpenSearchTextParser()
    parser.feed(html_text)
    text = parser.visible_text()
    if not text:
        raise StructureMismatchError(
            f"expected D1-Law OpenSearch body was not found: {ref['source_url']}"
        )

    articles = segment_articles(text)
    for article in articles:
        line_locator = str(article.get("locator") or "document:1")
        article["locator"] = f"{fetched.final_url}; {line_locator}"

    title = _collapse(parser.title) or str(ref.get("title") or fetched.final_url)
    resolved_source_name = source_name or (
        f"d1-law opensearch official regulations ({urllib.parse.urlsplit(canonical_index).netloc})"
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
                "extractor": "d1-law opensearch via stdlib HTMLParser",
                "encoding": fetched.encoding,
                "segmentation": "article_heading_or_document_fallback",
            },
            "status": "extracted" if articles else "text_without_articles",
            "cache_path": str(fetched.cache_path),
            "issues": issues,
        },
    }


def ingest_d1law_opensearch(
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
    """Discover, fetch, and store D1-Law OpenSearch regulations."""
    canonical_index = normalize_index_url(index_url)
    client = HttpClient(
        cache_dir or DEFAULT_CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        offline=offline,
        refresh=refresh,
        timeout=timeout,
        min_interval_seconds=1.5,
    )
    refs = discover_documents(canonical_index, client=client, limit=limit)
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
                    index_url=canonical_index,
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
        "index_url": canonical_index,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-url",
        required=True,
        help="User-supplied D1-Law OpenSearch index URL (e.g. ?jctcd=...)",
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
                "adapter": "d1law_opensearch",
                "index_url": args.index_url,
                "db": str(args.db),
                "limit": args.limit,
            },
        )
    except Exception:
        manifest_path = None
        manifest = None

    try:
        report = ingest_d1law_opensearch(
            args.index_url,
            args.db,
            source_name=args.source_name,
            cache_dir=args.cache_dir,
            limit=args.limit,
            offline=args.offline,
            refresh=args.refresh,
            timeout=args.timeout,
        )
        if manifest_path and manifest:
            finish_database_run(
                manifest_path,
                manifest,
                database=args.db,
                artifact_kind="d1law_database",
                scope={"adapter": "d1law_opensearch", "action": "ingest"},
                coverage={
                    "documents": report.get("documents", 0),
                    "articles": report.get("articles", 0),
                    "statuses": report.get("statuses", {}),
                },
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        if manifest_path and manifest:
            fail_module_run(manifest_path, manifest, error=str(exc))
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
