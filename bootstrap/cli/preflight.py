#!/usr/bin/env python3
"""Discover municipality-specific source entrances from official home pages."""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import re
import sys
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from bootstrap.cli.http import (
    FetchError,
    HttpClient,
    OfflineCacheMiss,
    RobotsDeniedError,
    RobotsUnavailableError,
)
from bootstrap.municipalities import load_metadata, load_registry


SOURCE_KINDS = ("minutes", "regulations", "budget", "settlement")
DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".xlsx", ".xls", ".csv", ".zip"}
NAVIGATION_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx"}
STRONG_KEYWORDS = {
    "minutes": ("会議録", "議事録", "会議録検索", "本会議録"),
    "regulations": ("例規集", "例規", "条例・規則", "条例規則"),
    "budget": ("予算書", "当初予算", "補正予算", "予算概要"),
    "settlement": (
        "決算書",
        "決算概要",
        "決算状況",
        "主要な施策",
        "主要施策",
    ),
}
NAVIGATION_KEYWORDS = {
    "minutes": (*STRONG_KEYWORDS["minutes"], "議会", "市議会", "町議会", "議会事務局"),
    "regulations": (*STRONG_KEYWORDS["regulations"], "条例", "規則"),
    "budget": (*STRONG_KEYWORDS["budget"], "予算", "財政", "財務"),
    "settlement": (*STRONG_KEYWORDS["settlement"], "決算", "財政", "財務"),
}
NEGATIVE_NAVIGATION_WORDS = (
    "お問い合わせ",
    "アクセシビリティ",
    "個人情報",
    "広告",
)


class PreflightError(RuntimeError):
    """Raised when the bounded preflight cannot run safely."""


@dataclass(frozen=True)
class Link:
    """One observed HTML link."""

    href: str
    label: str


class PageParser(HTMLParser):
    """Extract page context and ordinary anchors without executing scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._capture_context = False
        self._context: list[str] = []
        self.script_count = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag_name = tag.lower()
        if tag_name == "script":
            self.script_count += 1
        if tag_name in {"title", "h1"}:
            self._capture_context = True
        if tag_name == "a" and self._href is None:
            href = dict(attrs).get("href")
            if href:
                self._href = str(href)
                self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._capture_context:
            self._context.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"title", "h1"}:
            self._capture_context = False
        if tag_name == "a" and self._href is not None:
            self.links.append(
                Link(
                    href=self._href,
                    label=_collapse("".join(self._link_text)),
                )
            )
            self._href = None
            self._link_text = []

    def context(self) -> str:
        """Return normalized title and H1 evidence."""

        return _collapse(" ".join(self._context))


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _canonical_url(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    if parts.username or parts.password:
        return None
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )


def _domain_key(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def _official_host(url: str, official_domains: set[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    normalized = host.removeprefix("www.")
    return any(
        normalized == domain or normalized.endswith("." + domain)
        for domain in official_domains
    )


def _evidence_text(url: str, label: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return _collapse(
        f"{label} {urllib.parse.unquote(parts.path)} "
        f"{urllib.parse.unquote(parts.query)}"
    )


def _matching_kinds(
    text: str,
    *,
    keywords: dict[str, tuple[str, ...]],
) -> list[str]:
    return [
        kind
        for kind in SOURCE_KINDS
        if any(keyword in text for keyword in keywords[kind])
    ]


def _document_extension(url: str) -> str:
    return Path(urllib.parse.urlsplit(url).path).suffix.lower()


def _vendor(url: str) -> tuple[str, str] | None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    path = urllib.parse.unquote(parts.path)
    if host == "ssp.kaigiroku.net":
        if re.match(r"^/tenant/[^/]+(?:/|$)", path, re.I):
            return "minutes", "kaigiroku_net"
        return "minutes", "kaigiroku_net_unconfirmed_tenant"
    if (host == "gijiroku.com" or host.endswith(".gijiroku.com")) and re.search(
        r"/voices(?:/|$)", path,
        re.I,
    ):
        return "minutes", "voices"
    if host == "discussvision.net" or host.endswith(".discussvision.net"):
        return "minutes", "discuss"
    if host == "g-reiki.net" or host.endswith(".g-reiki.net"):
        return "regulations", "g_reiki"
    if path.lower().endswith("/reiki_menu.html"):
        return "regulations", "g_reiki"
    return None


def _navigation_score(text: str) -> int:
    score = 0
    for kind in SOURCE_KINDS:
        if any(keyword in text for keyword in STRONG_KEYWORDS[kind]):
            score += 10
        elif any(keyword in text for keyword in NAVIGATION_KEYWORDS[kind]):
            score += 3
    return score


def _result(
    *,
    status: str,
    adapter: str | None = None,
    index_url: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "adapter": adapter,
        "index_url": index_url,
        "reason": reason,
        "evidence": evidence or [],
        "documents_downloaded": 0,
        "database_created": False,
    }


def _classify_kind(
    kind: str,
    evidence: list[dict[str, Any]],
    *,
    blocked_kinds: set[str],
    root_blocked: bool,
    root_unavailable: bool,
    dynamic_navigation: bool,
) -> dict[str, Any]:
    if root_blocked:
        return _result(
            status="robots_blocked",
            reason="official_home_robots_blocked",
        )
    if root_unavailable:
        return _result(
            status="human_confirmation_required",
            reason="official_home_not_available_in_offline_cache",
        )

    matching = [item for item in evidence if kind in item["kinds"]]
    vendors = [item for item in matching if item.get("vendor")]
    if kind == "minutes":
        supported = next(
            (
                item
                for item in vendors
                if item["vendor"] == "kaigiroku_net"
            ),
            None,
        )
        if supported:
            return _result(
                status="ready",
                adapter="kaigiroku_net",
                index_url=supported["url"],
                evidence=[supported],
                reason="supported_vendor_linked_from_official_page",
            )
        unsupported = next(
            (
                item
                for item in vendors
                if item["vendor"]
                in {"voices", "discuss", "kaigiroku_net_unconfirmed_tenant"}
            ),
            None,
        )
        if unsupported:
            return _result(
                status="unsupported_vendor",
                adapter=unsupported["vendor"],
                index_url=unsupported["url"],
                evidence=[unsupported],
                reason="detected_vendor_has_no_verified_ingest_adapter",
            )
        static = next(
            (
                item
                for item in matching
                if item["evidence_type"] == "document_link"
                and item["official_host"]
            ),
            None,
        )
        if static:
            return _result(
                status="ready",
                adapter="static_html_pdf",
                index_url=static["observed_on"],
                evidence=[static],
                reason="official_page_links_minutes_document",
            )

    if kind == "regulations":
        supported = next(
            (
                item
                for item in vendors
                if item["vendor"] == "g_reiki"
            ),
            None,
        )
        if supported:
            return _result(
                status="ready",
                adapter="g_reiki",
                index_url=supported["url"],
                evidence=[supported],
                reason="supported_vendor_linked_from_official_page",
            )
        unsupported = next(
            (
                item
                for item in matching
                if not item["official_host"]
                and item["evidence_type"] == "page_link"
            ),
            None,
        )
        if unsupported:
            return _result(
                status="unsupported_vendor",
                adapter="unknown",
                index_url=unsupported["url"],
                evidence=[unsupported],
                reason="external_regulations_vendor_is_not_supported",
            )

    if kind in {"budget", "settlement"}:
        document = next(
            (
                item
                for item in matching
                if item["evidence_type"] == "document_link"
            ),
            None,
        )
        if document:
            return _result(
                status="ready",
                adapter="official_document_index",
                index_url=document["observed_on"],
                evidence=[document],
                reason="official_page_links_matching_document",
            )
        context = next(
            (
                item
                for item in matching
                if item["evidence_type"] == "page_context"
                and item["official_host"]
            ),
            None,
        )
        if context:
            return _result(
                status="ready",
                adapter="official_index",
                index_url=context["url"],
                evidence=[context],
                reason="fetched_official_page_has_matching_title_or_heading",
            )

    context = next(
        (
            item
            for item in matching
            if item["evidence_type"] == "page_context"
        ),
        None,
    )
    if context:
        return _result(
            status="unknown_structure",
            index_url=context["url"],
            evidence=[context],
            reason="related_official_page_found_but_structure_is_not_supported",
        )

    external = next(
        (item for item in matching if not item["official_host"]),
        None,
    )
    if external:
        return _result(
            status="human_confirmation_required",
            index_url=external["url"],
            evidence=[external],
            reason="external_candidate_requires_human_confirmation",
        )
    if kind in blocked_kinds:
        return _result(
            status="robots_blocked",
            reason="related_candidate_was_blocked_by_robots",
        )
    if matching:
        return _result(
            status="human_confirmation_required",
            index_url=matching[0]["url"],
            evidence=[matching[0]],
            reason="related_link_found_but_not_confirmed_as_an_index",
        )
    if dynamic_navigation:
        return _result(
            status="unknown_structure",
            reason="javascript_navigation_not_visible_in_static_html",
        )
    return _result(
        status="source_not_found",
        reason="no_matching_link_observed_within_page_bound",
    )


def preflight_municipality(
    municipality: dict[str, str],
    client: HttpClient,
    *,
    max_pages: int,
) -> dict[str, Any]:
    """Fetch only bounded official HTML pages and classify four source types."""

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    root_url = municipality["official_home_url"]
    official_domains = {_domain_key(root_url)}
    queue: list[tuple[int, int, int, str, str, str]] = []
    sequence = itertools.count()
    heapq.heappush(
        queue,
        (0, 0, next(sequence), root_url, "", "official_home"),
    )
    queued = {root_url}
    visited: set[str] = set()
    pages: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    blocked_kinds: set[str] = set()
    warnings: list[dict[str, str]] = []
    root_blocked = False
    root_unavailable = False
    dynamic_navigation = False

    while queue and len(pages) < max_pages:
        _negative_score, depth, _order, url, discovered_from, label = heapq.heappop(
            queue
        )
        queued.discard(url)
        if url in visited:
            continue
        candidate_kinds = _matching_kinds(
            _evidence_text(url, label),
            keywords=NAVIGATION_KEYWORDS,
        )
        actionable_kinds = {
            item_kind
            for item in evidence
            for item_kind in item["kinds"]
            if (
                item.get("vendor")
                or item["evidence_type"] in {"document_link", "page_context"}
            )
        }
        if candidate_kinds and all(
            kind in actionable_kinds for kind in candidate_kinds
        ):
            continue
        visited.add(url)
        try:
            fetched = client.fetch(
                url,
                cache_key=f"preflight:{municipality['area_code_5']}:{url}",
            )
        except OfflineCacheMiss as error:
            root_unavailable = root_unavailable or depth == 0
            warnings.append(
                {
                    "url": url,
                    "status": "offline_cache_miss",
                    "detail": str(error),
                }
            )
            continue
        except RobotsDeniedError as error:
            candidate_kinds = _matching_kinds(
                _evidence_text(url, label),
                keywords=NAVIGATION_KEYWORDS,
            )
            blocked_kinds.update(candidate_kinds)
            root_blocked = root_blocked or depth == 0
            warnings.append(
                {"url": url, "status": "robots_blocked", "detail": str(error)}
            )
            continue
        except RobotsUnavailableError as error:
            candidate_kinds = _matching_kinds(
                _evidence_text(url, label),
                keywords=NAVIGATION_KEYWORDS,
            )
            blocked_kinds.update(candidate_kinds)
            root_blocked = root_blocked or depth == 0
            warnings.append(
                {
                    "url": url,
                    "status": "robots_blocked",
                    "detail": str(error),
                }
            )
            continue
        except FetchError as error:
            warnings.append(
                {"url": url, "status": "fetch_failed", "detail": str(error)}
            )
            continue

        page_record = {
            "requested_url": url,
            "final_url": fetched.final_url,
            "discovered_from": discovered_from or None,
            "link_label": label,
            "depth": depth,
            "fetched_at": fetched.fetched_at,
            "sha256": fetched.sha256,
            "content_type": fetched.content_type,
            "from_cache": fetched.from_cache,
        }
        pages.append(page_record)
        final_is_official = _official_host(fetched.final_url, official_domains)
        if depth == 0:
            official_domains.add(_domain_key(fetched.final_url))
            final_is_official = True
        elif not final_is_official:
            redirected_vendor = _vendor(fetched.final_url)
            redirected_kinds = _matching_kinds(
                _evidence_text(fetched.final_url, label),
                keywords=NAVIGATION_KEYWORDS,
            )
            if redirected_vendor and redirected_vendor[0] not in redirected_kinds:
                redirected_kinds.append(redirected_vendor[0])
            if redirected_kinds:
                evidence.append(
                    {
                        "evidence_type": "redirect_target",
                        "url": fetched.final_url,
                        "observed_on": discovered_from or url,
                        "label": label,
                        "kinds": redirected_kinds,
                        "official_host": False,
                        "vendor": (
                            redirected_vendor[1] if redirected_vendor else None
                        ),
                    }
                )
            continue
        if "html" not in fetched.content_type:
            warnings.append(
                {
                    "url": fetched.final_url,
                    "status": "non_html_navigation_page",
                    "detail": fetched.content_type,
                }
            )
            continue

        parser = PageParser()
        parser.feed(fetched.text())
        if depth == 0 and len(parser.links) < 10 and parser.script_count >= 2:
            dynamic_navigation = True
        page_record["anchor_count"] = len(parser.links)
        page_record["script_count"] = parser.script_count
        context = parser.context()
        context_kinds = _matching_kinds(context, keywords=STRONG_KEYWORDS)
        if context_kinds:
            key = ("page_context", fetched.final_url, fetched.final_url)
            if key not in seen_evidence:
                seen_evidence.add(key)
                evidence.append(
                    {
                        "evidence_type": "page_context",
                        "url": fetched.final_url,
                        "observed_on": fetched.final_url,
                        "label": context,
                        "kinds": context_kinds,
                        "official_host": True,
                        "vendor": None,
                    }
                )

        for link in parser.links:
            resolved = _canonical_url(
                urllib.parse.urljoin(fetched.final_url, link.href)
            )
            if resolved is None:
                continue
            text = _evidence_text(resolved, link.label)
            if any(word in text for word in NEGATIVE_NAVIGATION_WORDS):
                continue
            strong_kinds = _matching_kinds(text, keywords=STRONG_KEYWORDS)
            navigation_kinds = _matching_kinds(
                text,
                keywords=NAVIGATION_KEYWORDS,
            )
            vendor = _vendor(resolved)
            if vendor and vendor[0] not in navigation_kinds:
                navigation_kinds.append(vendor[0])
            official = _official_host(resolved, official_domains)
            extension = _document_extension(resolved)
            evidence_type = (
                "document_link"
                if extension in DOCUMENT_EXTENSIONS and strong_kinds
                else "page_link"
            )
            kinds = strong_kinds or navigation_kinds
            if kinds:
                key = (evidence_type, resolved, fetched.final_url)
                if key not in seen_evidence:
                    seen_evidence.add(key)
                    evidence.append(
                        {
                            "evidence_type": evidence_type,
                            "url": resolved,
                            "observed_on": fetched.final_url,
                            "label": link.label,
                            "kinds": kinds,
                            "official_host": official,
                            "vendor": vendor[1] if vendor else None,
                        }
                    )
            is_sitemap = (
                "サイトマップ" in text or "sitemap" in text.lower()
            )
            if (
                depth < 2
                and official
                and extension in NAVIGATION_EXTENSIONS
                and (navigation_kinds or is_sitemap)
                and resolved not in visited
                and resolved not in queued
            ):
                score = 20 if is_sitemap else _navigation_score(text)
                heapq.heappush(
                    queue,
                    (
                        -score,
                        depth + 1,
                        next(sequence),
                        resolved,
                        fetched.final_url,
                        link.label,
                    ),
                )
                queued.add(resolved)

    sources = {
        kind: _classify_kind(
            kind,
            evidence,
            blocked_kinds=blocked_kinds,
            root_blocked=root_blocked,
            root_unavailable=root_unavailable,
            dynamic_navigation=dynamic_navigation,
        )
        for kind in SOURCE_KINDS
    }
    return {
        "municipality": municipality["municipality_name"],
        "prefecture": municipality["prefecture_name"],
        "area_code_5": municipality["area_code_5"],
        "local_government_code_6": municipality[
            "local_government_code_6"
        ],
        "official_home_url": root_url,
        "status": (
            "ready"
            if all(source["status"] == "ready" for source in sources.values())
            else "needs_attention"
        ),
        "pages_fetched": len(pages),
        "page_limit": max_pages,
        "sources": sources,
        "pages": pages,
        "warnings": warnings,
        "dynamic_navigation_detected": dynamic_navigation,
        "documents_downloaded": 0,
        "database_created": False,
    }


def run_preflight(
    *,
    prefecture: str,
    municipality_names: list[str],
    client: HttpClient,
    max_pages: int,
) -> dict[str, Any]:
    """Run a bounded batch from the bundled municipality registry."""

    candidates = [
        row for row in load_registry() if row["prefecture_name"] == prefecture
    ]
    if municipality_names:
        requested = set(municipality_names)
        candidates = [
            row for row in candidates if row["municipality_name"] in requested
        ]
        missing = requested - {row["municipality_name"] for row in candidates}
        if missing:
            raise PreflightError(
                "registryに対象自治体がありません: " + ", ".join(sorted(missing))
            )
    if not candidates:
        raise PreflightError(f"対象自治体がありません: {prefecture}")

    results: list[dict[str, Any]] = []
    for index, municipality in enumerate(candidates, start=1):
        result = preflight_municipality(
            municipality,
            client,
            max_pages=max_pages,
        )
        results.append(result)
        print(
            f"[{index}/{len(candidates)}] {result['municipality']}: "
            f"{result['status']} pages={result['pages_fetched']}",
            file=sys.stderr,
        )

    status_counts = Counter(result["status"] for result in results)
    registry_metadata = load_metadata()
    source_status_counts = {
        kind: dict(
            sorted(
                Counter(
                    result["sources"][kind]["status"] for result in results
                ).items()
            )
        )
        for kind in SOURCE_KINDS
    }
    return {
        "schema_version": 1,
        "test_type": "municipality_source_preflight",
        "generated_at": _utc_now(),
        "status": (
            "ready"
            if status_counts.get("ready", 0) == len(results)
            else "needs_attention"
        ),
        "prefecture": prefecture,
        "municipality_count": len(results),
        "max_pages_per_municipality": max_pages,
        "registry": {
            "generated_at": registry_metadata.get("generated_at"),
            "sha256": registry_metadata.get("registry_sha256"),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "source_status_counts": source_status_counts,
        "documents_downloaded": 0,
        "database_created": False,
        "results": results,
        "retrieval": client.retrieval_report(),
    }


def _write_new(path: Path, report: dict[str, Any]) -> None:
    """Create a report without replacing an existing file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m bootstrap.cli.preflight",
        description=(
            "同梱registryの公式ホームURLから、少量HTMLだけで議事録・例規・"
            "予算・決算の入口を分類"
        ),
    )
    parser.add_argument("--prefecture", required=True)
    parser.add_argument(
        "--municipality",
        action="append",
        default=[],
        help="対象自治体を限定。複数回指定可。省略時は都道府県内すべて",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--max-pages-per-municipality",
        type=int,
        default=8,
        choices=range(1, 13),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(f"ERROR: 既存reportを上書きしません: {args.output}", file=sys.stderr)
        return 2
    try:
        client = HttpClient(args.cache_dir, offline=args.offline)
        report = run_preflight(
            prefecture=args.prefecture,
            municipality_names=args.municipality,
            client=client,
            max_pages=args.max_pages_per_municipality,
        )
        _write_new(args.output, report)
    except (OSError, PreflightError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
