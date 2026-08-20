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

from .adapters.base import (
    MINUTES_USER_AGENT,
    CacheTier,
    FetchError,
    HttpClient,
)

MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = MODULE_DIR / ".cache"
_STATIC_STRONG_RE = re.compile(r"(?:会議録|議事録|minutes?)", re.I)
_MEETING_RE = re.compile(r"(?:定例会|臨時会|本会議|委員会)")
_DOCUMENT_RE = re.compile(r"\.(?:pdf|html?|txt)(?:$|[?#])", re.I)
_DISCUSS_HOST_SUFFIXES = ("discussvision.net",)

# Council scope for static minutes (consistent with bootstrap/cli/preflight.py)
_COUNCIL_TEXT_TOKENS = (
    "市議会",
    "町議会",
    "村議会",
    "区議会",
    "議会事務局",
    "本会議",
    "定例会",
    "臨時会",
)
_COUNCIL_URL_TOKENS = (
    "/gikai",
    "/shigikai",
    "/council",
    "/assembly",
    "kaigiroku",
    "gijiroku",
)
_NON_COUNCIL_TOKENS = (
    "審議会",
    "懇話会",
    "審査会",
    "教育委員会",
    "農業委員会",
    "選挙管理委員会",
    "総合教育会議",
    "監査委員",
)


def _has_council_scope(text: str) -> bool:
    """Check if text indicates council scope and not non-council committee."""
    if any(tok in text for tok in _NON_COUNCIL_TOKENS):
        return False
    lower = text.lower()
    if any(tok.lower() in lower for tok in _COUNCIL_TEXT_TOKENS):
        return True
    if "議会" in text:
        return True
    if any(tok in lower for tok in _COUNCIL_URL_TOKENS):
        return True
    return False


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
    lower_path = path.lower()
    if host == "ssp.kaigiroku.net":
        if re.match(r"^/tenant/[^/]+(?:/|$)", path, re.I):
            return "kaigiroku_net", "ssp.kaigiroku.net の /tenant/<name>/ URL"
        return "kaigiroku_net", "kaigiroku.net配信ホスト ssp.kaigiroku.net"
    if _host_matches(host, "gijiroku.com"):
        return "voices", "*.gijiroku.com の voices 系 URL"
    if host == "dbsr.jp" or host.endswith(".dbsr.jp"):
        if "/index.php" in lower_path:
            return "dbsr", "dbsr.jp の /index.php URL"
    if any(_host_matches(host, suffix) for suffix in _DISCUSS_HOST_SUFFIXES):
        return "discuss", "DiscussVision の既知ホスト"
    return None


def _is_static_candidate(url: str, label: str = "") -> bool:
    decoded_url = urllib.parse.unquote(url)
    combined = f"{decoded_url} {label}"
    if not _has_council_scope(combined):
        return False
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
    client: HttpClient,
    fetch_page: bool = True,
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
            result = client.fetch(url, tier=CacheTier.INDEX)
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
            if any(tok in page_text for tok in _NON_COUNCIL_TOKENS):
                lower_cand = candidate_url.lower()
                lower_label = label.lower()
                if not any(
                    tok in lower_cand or tok in lower_label
                    for tok in ("/gikai", "/shigikai", "/council", "/assembly")
                ):
                    continue
                if any(
                    t in f"{label} {page_text}"
                    for t in ("教育委員会", "農業委員会", "審議会")
                ):
                    continue
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
        page_text
        and _STATIC_STRONG_RE.search(page_text)
        and _has_council_scope(page_text)
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
    parser.add_argument("--cache-dir", type=Path, help="取得キャッシュの保存先")
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
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="入力 URL のパターンだけを判定し、ページを取得しない",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        client = HttpClient(
            args.cache_dir or DEFAULT_CACHE_DIR,
            user_agent=MINUTES_USER_AGENT,
            offline=args.offline,
            refresh=args.refresh,
            timeout=args.timeout,
        )
    except ValueError as error:
        parser.error(str(error))
    verdict = detect_url(
        args.url,
        client=client,
        fetch_page=not args.no_fetch,
    )
    json.dump(verdict, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
