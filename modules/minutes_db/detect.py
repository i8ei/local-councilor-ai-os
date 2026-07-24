#!/usr/bin/env python3
"""Detect supported council-minutes publication families from observed URLs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .adapters.base import FetchError, polite_fetch

_STATIC_STRONG_RE = re.compile(r"(?:会議録|議事録|minutes?)", re.I)
_MEETING_RE = re.compile(r"(?:定例会|臨時会|本会議|委員会)")
_DOCUMENT_RE = re.compile(r"\.(?:pdf|html?|txt)(?:$|[?#])", re.I)
_DISCUSS_HOST_SUFFIXES = ("discussvision.net",)


class _PageLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.page_text: list[str] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attributes = {key.lower(): value for key, value in attrs}
        self._href = attributes.get("href")
        self._anchor_text = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        self.page_text.append(cleaned)
        if self._href is not None:
            self._anchor_text.append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor_text)))
            self._href = None
            self._anchor_text = []


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _known_family(url: str) -> tuple[str, str] | None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    path = urllib.parse.unquote(parts.path)
    if host == "ssp.kaigiroku.net":
        if re.match(r"^/tenant/[^/]+(?:/|$)", path, re.I):
            return "kaigiroku_net", "ssp.kaigiroku.net の /tenant/<name>/ URL"
        return "kaigiroku_net", "kaigiroku.net配信ホスト ssp.kaigiroku.net"
    if _host_matches(host, "gijiroku.com") and re.search(r"/voices(?:/|$)", path, re.I):
        return "voices", "*.gijiroku.com の /voices/ URL"
    if any(_host_matches(host, suffix) for suffix in _DISCUSS_HOST_SUFFIXES):
        return "discuss", "DiscussVision の既知ホスト"
    return None


def _is_static_candidate(url: str, label: str = "") -> bool:
    decoded_url = urllib.parse.unquote(url)
    combined = f"{decoded_url} {label}"
    if decoded_url.lower().split("?", 1)[0].endswith(".pdf"):
        return True
    if _STATIC_STRONG_RE.search(combined):
        return True
    return bool(_MEETING_RE.search(combined) and _DOCUMENT_RE.search(decoded_url))


def _evidence(matched_url: str, reason: str, evidence_type: str) -> dict[str, str]:
    return {
        "type": evidence_type,
        "matched_url": matched_url,
        "reason": reason,
    }


def detect_url(
    url: str,
    *,
    fetch_page: bool = True,
    cache_dir: str | Path | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    """Return a conservative JSON-serializable detection verdict."""

    direct = _known_family(url)
    if direct:
        verdict, reason = direct
        return {
            "input_url": url,
            "verdict": verdict,
            "evidence": [_evidence(url, reason, "input_url")],
        }

    fetched_at: str | None = None
    final_url = url
    links: list[tuple[str, str]] = []
    page_text = ""
    fetch_error: str | None = None
    if fetch_page and not urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
        try:
            result = polite_fetch(url, cache_dir=cache_dir, timeout=timeout)
            fetched_at = result.fetched_at
            final_url = result.final_url
            parser = _PageLinks()
            parser.feed(result.text())
            links = parser.links
            page_text = " ".join(parser.page_text)
        except (FetchError, ValueError) as error:
            fetch_error = str(error)

    final_family = _known_family(final_url)
    if final_family:
        verdict, reason = final_family
        response: dict[str, Any] = {
            "input_url": url,
            "verdict": verdict,
            "evidence": [_evidence(final_url, reason, "redirect_target")],
        }
        if fetched_at:
            response["fetched_at"] = fetched_at
        return response

    resolved_links: list[tuple[str, str]] = []
    for href, label in links:
        resolved = urllib.parse.urljoin(final_url, href)
        if urllib.parse.urlsplit(resolved).scheme.lower() not in {"http", "https"}:
            continue
        resolved_links.append((resolved, label))

    for candidate_url, _ in resolved_links:
        family = _known_family(candidate_url)
        if family:
            verdict, reason = family
            response = {
                "input_url": url,
                "verdict": verdict,
                "evidence": [_evidence(candidate_url, reason, "page_link")],
            }
            if fetched_at:
                response["fetched_at"] = fetched_at
            return response

    for candidate_url, label in resolved_links:
        if _is_static_candidate(candidate_url, label):
            response = {
                "input_url": url,
                "verdict": "static_candidate",
                "evidence": [
                    _evidence(
                        candidate_url,
                        "会議録・議事録らしい文書リンク",
                        "page_link",
                    )
                ],
            }
            if fetched_at:
                response["fetched_at"] = fetched_at
            return response

    if _is_static_candidate(final_url) or (
        page_text and _STATIC_STRONG_RE.search(page_text)
    ):
        response = {
            "input_url": url,
            "verdict": "static_candidate",
            "evidence": [
                _evidence(final_url, "入力ページ自体が会議録・議事録候補", "input_page")
            ],
        }
        if fetched_at:
            response["fetched_at"] = fetched_at
        return response

    response = {
        "input_url": url,
        "verdict": "unknown",
        "evidence": [],
    }
    if fetched_at:
        response["fetched_at"] = fetched_at
    if fetch_error:
        response["warning"] = fetch_error
    return response


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="議事録公開方式を、観測できた URL とリンクから判定します。"
    )
    parser.add_argument("url", help="自治体公式サイトまたは議事録ページの URL")
    parser.add_argument("--cache-dir", help="取得キャッシュの保存先")
    parser.add_argument("--timeout", type=float, default=30, help="取得タイムアウト秒")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="入力 URL のパターンだけを判定し、ページを取得しない",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    verdict = detect_url(
        args.url,
        fetch_page=not args.no_fetch,
        cache_dir=args.cache_dir,
        timeout=args.timeout,
    )
    json.dump(verdict, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
