"""Diagnose municipal budget and settlement document indexes without downloads."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from .http import FetchError, HttpClient


KEYWORDS = {
    "budget": ("予算書", "当初予算", "補正予算", "予算概要", "予算説明"),
    "settlement": ("決算書", "決算概要", "決算状況", "決算説明", "主要な施策"),
}
DOCUMENT_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".csv", ".zip")
OPTIONS = {
    "later": "候補だけ記録し、文書取得とSQLite化は後日に回す",
    "sample": "候補から1〜3件を選び、PDF品質と抽出・検算経路だけ試す",
    "targeted": "年度・会計・資料種別を限定して取り込む",
    "full": "必要範囲を確認後に全件取り込む",
}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(
                {
                    "href": self._href,
                    "text": re.sub(r"\s+", " ", "".join(self._text)).strip(),
                }
            )
            self._href = None
            self._text = []


def diagnose_index(
    *,
    index_url: str,
    final_url: str,
    html: str,
    fetched_at: str,
    content_hash: str,
) -> dict[str, Any]:
    """Return candidate documents and confirmation options from one HTML page."""

    parser = _LinkParser()
    parser.feed(html)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in parser.links:
        resolved_url = urllib.parse.urljoin(final_url, link["href"])
        parts = urllib.parse.urlsplit(resolved_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue
        evidence = f"{link['text']} {urllib.parse.unquote(parts.path)}"
        kinds = [
            kind
            for kind, keywords in KEYWORDS.items()
            if any(keyword in evidence for keyword in keywords)
        ]
        extension = Path(parts.path).suffix.lower()
        if not kinds or extension not in DOCUMENT_EXTENSIONS:
            continue
        canonical = urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc, parts.path, parts.query, "")
        )
        if canonical in seen:
            continue
        seen.add(canonical)
        candidates.append(
            {
                "title": link["text"] or Path(parts.path).name,
                "url": canonical,
                "kinds": kinds,
                "format": extension.removeprefix("."),
                "reason": "official_index_keyword_and_document_extension",
            }
        )

    return {
        "schema_version": 1,
        "status": "needs_confirmation",
        "index": {
            "requested_url": index_url,
            "final_url": final_url,
            "fetched_at": fetched_at,
            "sha256": content_hash,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "documents_downloaded": 0,
        "database_created": False,
        "next_options": [
            {"id": option_id, "description": description}
            for option_id, description in OPTIONS.items()
        ],
        "advice": [
            "候補の年度、会計、資料種別、PDF品質を確認してから取得範囲を決める",
            "反復検索・年度比較・合計検算をする資料は原本保存後にSQLite化する",
            "一度だけ読む資料は都度参照でもよいが、対外利用した原典はsnapshotを残す",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m bootstrap.cli.local_documents",
        description="自治体公式索引だけを取得し、予算・決算候補と取込選択肢を表示",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    diagnose = subparsers.add_parser(
        "diagnose",
        help="索引HTMLだけを読み、文書は取得せず確認待ちで止める",
    )
    diagnose.add_argument("--index-url", required=True)
    diagnose.add_argument("--cache-dir", default="bootstrap/.cache/local-documents")
    diagnose.add_argument("--offline", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = HttpClient(args.cache_dir, offline=args.offline)
        fetched = client.fetch(args.index_url)
        if "html" not in fetched.content_type:
            raise FetchError(
                f"索引がHTMLではありません: {fetched.content_type}"
            )
        result = diagnose_index(
            index_url=args.index_url,
            final_url=fetched.final_url,
            html=fetched.text(),
            fetched_at=fetched.fetched_at,
            content_hash=f"sha256:{fetched.sha256}",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FetchError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
