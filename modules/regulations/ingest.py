#!/usr/bin/env python3
"""Ingest officially published regulations from configured static HTML/TXT links."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.parse
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lcaios.module_manifest import (
    begin_module_run,
    fail_module_run,
    finish_database_run,
    input_file_record,
)
from modules.minutes_db.adapters.base import FetchError, polite_fetch

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
SCHEMA_PATH = MODULE_DIR / "schema.sql"
_BLOCK_TAGS = {
    "article", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4",
    "h5", "h6", "li", "main", "p", "pre", "section", "table", "td", "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
_ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千〇零壱弐参0-9]+条(?:の[一二三四五六七八九十百千〇零壱弐参0-9]+)?)(?:[ 　]*(.*))?$")
_DATE_RE = re.compile(r"(令和|平成|昭和)(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日|(?<!\d)(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?")


def stable_id(prefix: str, value: str) -> str:
    """Build a deterministic identifier from a stable source value."""
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


class _HtmlTextParser(HTMLParser):
    """Collect visible text, title, and links from a small HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[tuple[str, str]] = []
        self._title_depth = 0
        self._ignored_depth = 0
        self._href: str | None = None
        self._link_text: list[str] = []
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self._text.append("\n")
        if tag == "title":
            self._title_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._link_text = []

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
        if tag == "a" and self._href is not None:
            self.links.append((self._href, _collapse("".join(self._link_text))))
            self._href = None
            self._link_text = []
        if tag in _BLOCK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._text.append(data)
        if self._title_depth:
            self.title += data
        if self._href is not None:
            self._link_text.append(data)

    def visible_text(self) -> str:
        lines = [_collapse(line) for line in "".join(self._text).splitlines()]
        return "\n".join(line for line in lines if line)


def _collapse(value: str) -> str:
    return re.sub(r"[ \t\r\v]+", " ", value).strip()


def _infer_date(text: str) -> str | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    if match.group(1):
        bases = {"令和": 2018, "平成": 1988, "昭和": 1925}
        era_year = 1 if match.group(2) == "元" else int(match.group(2))
        return f"{bases[match.group(1)] + era_year:04d}-{int(match.group(3)):02d}-{int(match.group(4)):02d}"
    return f"{int(match.group(5)):04d}-{int(match.group(6)):02d}-{int(match.group(7)):02d}"


def segment_articles(text: str) -> list[dict[str, Any]]:
    """Split regulation text by article headings, preserving fallback chunks."""
    lines = [_collapse(line) for line in text.splitlines()]
    records = [(idx, line) for idx, line in enumerate(lines, start=1) if line]
    articles: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        pending["text"] = "\n".join(pending.pop("_parts")).strip()
        if pending["text"]:
            pending["seq"] = len(articles) + 1
            articles.append(pending)
        pending = None

    for line_no, line in records:
        match = _ARTICLE_RE.match(line)
        if match:
            flush()
            article_no = match.group(1)
            heading = (match.group(2) or "").strip() or None
            pending = {
                "article_no": article_no,
                "heading": heading,
                "locator": f"line:{line_no}",
                "_parts": [line],
            }
            continue
        if pending is None:
            pending = {
                "article_no": None,
                "heading": None,
                "locator": f"line:{line_no}",
                "_parts": [line],
            }
        else:
            pending["_parts"].append(line)
    flush()
    if articles:
        return articles
    collapsed = _collapse(text.replace("\n", " "))
    return [
        {
            "seq": 1,
            "article_no": None,
            "heading": None,
            "text": collapsed,
            "locator": "document:1",
        }
    ] if collapsed else []


def ensure_schema(connection: sqlite3.Connection) -> str:
    """Create tables; keep relational layer usable when FTS5 is unavailable."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    try:
        connection.executescript(schema)
        return "unicode61"
    except sqlite3.OperationalError as exc:
        if "fts5" not in str(exc).lower():
            raise
        relational = schema.split("CREATE VIRTUAL TABLE IF NOT EXISTS regulation_articles_fts", 1)[0]
        connection.executescript(relational)
        return "unavailable"


def _load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    index_urls = data.get("index_url")
    if isinstance(index_urls, str):
        index_urls = [index_urls]
    if not isinstance(index_urls, list) or not index_urls:
        raise ValueError("config.index_url must be a URL or URL list")
    data["index_url"] = index_urls
    allow_hosts = data.get("allow_hosts", [])
    if (
        not isinstance(allow_hosts, list)
        or not all(isinstance(host, str) and host.strip() for host in allow_hosts)
    ):
        raise ValueError("config.allow_hosts must be a list of hostnames")
    for key in ("link_include_regex", "link_exclude_regex"):
        if data.get(key):
            re.compile(str(data[key]))
    return data


class DiscoveryResults(list[dict[str, Any]]):
    """List-compatible discovery result with visible rejected candidates."""

    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
        *,
        skipped_urls: list[str] | None = None,
    ) -> None:
        super().__init__(documents or [])
        self.skipped_urls = skipped_urls or []


def _allowed_hosts(config: dict[str, Any]) -> set[str]:
    values = config.get("allow_hosts", [])
    if (
        not isinstance(values, list)
        or not all(isinstance(host, str) and host.strip() for host in values)
    ):
        raise ValueError("config.allow_hosts must be a list of hostnames")
    return {host.strip().casefold() for host in values}


def discover_documents(
    config: dict[str, Any],
    *,
    cache_dir: str | Path | None = None,
    limit: int | None = None,
) -> DiscoveryResults:
    """Discover candidate regulation documents from configured official index pages."""
    include = re.compile(str(config["link_include_regex"])) if config.get("link_include_regex") else None
    exclude = re.compile(str(config["link_exclude_regex"])) if config.get("link_exclude_regex") else None
    results = DiscoveryResults()
    seen: set[str] = set()
    skipped: set[str] = set()
    allow_hosts = _allowed_hosts(config)
    for index_url in config["index_url"]:
        fetched = polite_fetch(index_url, cache_dir=cache_dir)
        # Compare hostnames, not netlocs: an explicit :443/:80 on an otherwise
        # same-site link must not be mistaken for a different host.
        index_hostname = (
            urllib.parse.urlsplit(fetched.final_url).hostname or ""
        ).casefold()
        parser = _HtmlTextParser()
        parser.feed(fetched.text())
        for href, label in parser.links:
            source_url = urllib.parse.urljoin(fetched.final_url, href)
            parsed = urllib.parse.urlsplit(source_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            source_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
            combined = f"{source_url} {label}"
            if include and not include.search(combined):
                continue
            if exclude and exclude.search(combined):
                continue
            if not include and not re.search(r"(条例|規則|要綱|例規|reiki|regulation|rule)", combined, re.I):
                continue
            hostname = parsed.hostname.casefold() if parsed.hostname else ""
            if hostname != index_hostname and hostname not in allow_hosts:
                if source_url not in skipped:
                    results.skipped_urls.append(source_url)
                    skipped.add(source_url)
                continue
            if source_url in seen:
                continue
            seen.add(source_url)
            results.append({
                "document_id": stable_id("regdoc", source_url),
                "source_url": source_url,
                "title": label or Path(parsed.path).name or source_url,
                "discovered_from": fetched.final_url,
            })
            if limit is not None and len(results) >= limit:
                return results
    return results


def fetch_document(ref: dict[str, Any], config: dict[str, Any], *, cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Fetch one regulation document and normalize it into articles."""
    fetched = polite_fetch(ref["source_url"], cache_dir=cache_dir)
    parser = _HtmlTextParser()
    if fetched.content_type in {"text/html", "application/xhtml+xml"} or fetched.text().lstrip().startswith("<"):
        parser.feed(fetched.text())
        text = parser.visible_text()
        title = _collapse(parser.title) or ref.get("title") or fetched.final_url
        extractor = "stdlib.html.parser.HTMLParser"
    else:
        text = fetched.text()
        title = ref.get("title") or Path(urllib.parse.urlsplit(fetched.final_url).path).name
        extractor = "plain-text"
    articles = segment_articles(text)
    source_host = urllib.parse.urlsplit(fetched.final_url).hostname
    index_urls = config.get("index_url") or []
    if isinstance(index_urls, str):
        index_urls = [index_urls]
    configured_hosts = {
        host.casefold()
        for url in [
            ref.get("discovered_from"),
            *index_urls,
        ]
        if url and (host := urllib.parse.urlsplit(str(url)).hostname)
    }
    same_host = bool(
        source_host and source_host.casefold() in configured_hosts
    )
    source_name = str(
        config.get("source_name")
        or (config.get("municipality") if same_host else source_host)
        or source_host
        or "official source"
    )
    document = {
        "document": {
            "document_id": ref.get("document_id") or stable_id("regdoc", ref["source_url"]),
            "title": title,
            "category": config.get("category"),
            "source_url": ref["source_url"],
            "source_name": source_name,
            "promulgated_on": _infer_date(text[:2000]) if config.get("infer_dates", True) else None,
            "enforced_on": None,
            "fetched_at": fetched.fetched_at,
            "adapter": "static_regulations",
            "verification_state": "discovered",
        },
        "articles": articles,
        "provenance": {
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": fetched.content_type,
            "content_sha256": fetched.sha256,
            "adapter": "static_regulations",
            "transform": {"extractor": extractor, "segmentation": "article_heading_or_document_fallback"},
            "status": "extracted" if articles else "text_without_articles",
            "cache_path": str(fetched.cache_path),
            "issues": [] if articles else ["本文は取得できましたが条単位に分割できませんでした。"],
        },
    }
    return document


def store_document(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Upsert one normalized regulation document and rebuild its FTS rows."""
    doc = payload["document"]
    document_id = str(doc["document_id"])
    connection.execute(
        """
        INSERT INTO regulation_documents (
            document_id, title, category, source_url, source_name,
            promulgated_on, enforced_on, fetched_at, adapter, verification_state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            title=excluded.title,
            category=excluded.category,
            source_name=excluded.source_name,
            promulgated_on=excluded.promulgated_on,
            enforced_on=excluded.enforced_on,
            fetched_at=excluded.fetched_at,
            adapter=excluded.adapter,
            verification_state=excluded.verification_state
        """,
        (
            document_id, doc["title"], doc.get("category"), doc["source_url"],
            doc["source_name"], doc.get("promulgated_on"), doc.get("enforced_on"),
            doc["fetched_at"], doc["adapter"], doc["verification_state"],
        ),
    )
    document_id = str(connection.execute(
        "SELECT document_id FROM regulation_documents WHERE source_url = ?",
        (doc["source_url"],),
    ).fetchone()[0])
    connection.execute("DELETE FROM regulation_articles WHERE document_id = ?", (document_id,))
    try:
        connection.execute("DELETE FROM regulation_articles_fts WHERE document_id = ?", (document_id,))
    except sqlite3.OperationalError as exc:
        if "regulation_articles_fts" not in str(exc):
            raise
    stored = 0
    for position, article in enumerate(payload.get("articles") or [], start=1):
        seq = int(article.get("seq") or position)
        article_id = str(article.get("article_id") or stable_id("regart", f"{doc['source_url']}#{seq}"))
        connection.execute(
            """
            INSERT INTO regulation_articles (
                article_id, document_id, seq, article_no, heading, text, locator
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id, document_id, seq, article.get("article_no"),
                article.get("heading"), str(article.get("text") or ""),
                str(article.get("locator") or f"article:{seq}"),
            ),
        )
        try:
            connection.execute(
                """
                INSERT INTO regulation_articles_fts (
                    text, heading, article_no, title, document_id, article_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(article.get("text") or ""), article.get("heading"),
                    article.get("article_no"), doc["title"], document_id, article_id,
                ),
            )
        except sqlite3.OperationalError as exc:
            if "regulation_articles_fts" not in str(exc):
                raise
        stored += 1
    provenance = payload.get("provenance") or {}
    resolved_url = str(provenance.get("resolved_url") or doc["source_url"])
    connection.execute(
        """
        INSERT INTO regulation_provenance (
            provenance_id, document_id, discovered_from, resolved_url,
            fetched_at, media_type, content_sha256, adapter, transform,
            status, cache_path, issues_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id, resolved_url) DO UPDATE SET
            discovered_from=excluded.discovered_from,
            fetched_at=excluded.fetched_at,
            media_type=excluded.media_type,
            content_sha256=excluded.content_sha256,
            adapter=excluded.adapter,
            transform=excluded.transform,
            status=excluded.status,
            cache_path=excluded.cache_path,
            issues_json=excluded.issues_json
        """,
        (
            stable_id("regprov", f"{document_id}:{resolved_url}"), document_id,
            provenance.get("discovered_from"), resolved_url,
            provenance.get("fetched_at") or doc["fetched_at"], provenance.get("media_type"),
            provenance.get("content_sha256"), doc["adapter"],
            json.dumps(provenance.get("transform") or {}, ensure_ascii=False, sort_keys=True),
            provenance.get("status") or "discovered", provenance.get("cache_path"),
            json.dumps(provenance.get("issues") or [], ensure_ascii=False),
        ),
    )
    return stored


def ingest(config_path: str | Path, db_path: str | Path, *, cache_dir: str | Path | None = None, limit: int | None = None) -> dict[str, Any]:
    config = _load_config(config_path)
    refs = discover_documents(config, cache_dir=cache_dir, limit=limit)
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        tokenizer = ensure_schema(connection)
        documents = 0
        articles = 0
        statuses: dict[str, int] = {}
        for ref in refs:
            payload = fetch_document(ref, config, cache_dir=cache_dir)
            articles += store_document(connection, payload)
            status = str((payload.get("provenance") or {}).get("status", "discovered"))
            statuses[status] = statuses.get(status, 0) + 1
            documents += 1
        connection.commit()
    return {
        "database": str(database),
        "documents": documents,
        "articles": articles,
        "skipped_urls": refs.skipped_urls,
        "statuses": statuses,
        "fts_tokenizer": tokenizer,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Static regulations JSON config")
    parser.add_argument("--db", required=True, help="Output SQLite database")
    parser.add_argument("--limit", type=int, default=None, help="Document limit")
    parser.add_argument("--cache-dir", help="Override cache directory")
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    try:
        manifest_path, manifest = begin_module_run(
            args.manifest_dir,
            run_type="regulations",
            repo_root=REPO_ROOT,
            run_id=args.run_id,
            requested={
                "adapter": "static",
                "config": args.config,
                "database": args.db,
                "limit": args.limit,
                "cache_directory": args.cache_dir,
            },
        )
        result = ingest(args.config, args.db, cache_dir=args.cache_dir, limit=args.limit)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, FetchError, json.JSONDecodeError) as exc:
        fail_module_run(manifest_path, manifest, exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finish_database_run(
        manifest_path,
        manifest,
        database=result["database"],
        artifact_kind="regulations_database",
        scope={"adapter": "static", "action": "ingest"},
        coverage={
            "documents": result["documents"],
            "articles": result["articles"],
            "skipped_urls": result["skipped_urls"],
            "statuses": result["statuses"],
            "fts_tokenizer": result["fts_tokenizer"],
        },
        inputs=[input_file_record(args.config, kind="regulations_adapter_config")],
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


if __name__ == "__main__":
    raise SystemExit(main())
