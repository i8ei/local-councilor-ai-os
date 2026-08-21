"""Live verification for g_reiki source profiles (stdlib only, HttpClient injection)."""

from __future__ import annotations  # noqa: I001

import copy
import re
import urllib.parse
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lcaios.http import CacheTier, RobotsDeniedError
from source_profiles.schema import validate_profile

try:
    from bootstrap.cli.preflight import (  # type: ignore[import-not-found]
        COUNCIL_TEXT_TOKENS,
        COUNCIL_URL_TOKENS,
        NON_COUNCIL_TOKENS,
    )
except Exception:  # pragma: no cover
    COUNCIL_TEXT_TOKENS = (
        "市議会",
        "町議会",
        "村議会",
        "区議会",
        "議会事務局",
        "本会議",
        "定例会",
        "臨時会",
    )
    COUNCIL_URL_TOKENS = (
        "/gikai",
        "/shigikai",
        "/council",
        "/assembly",
        "kaigiroku",
        "gijiroku",
    )
    NON_COUNCIL_TOKENS = (
        "審議会",
        "懇話会",
        "審査会",
        "教育委員会",
        "農業委員会",
        "選挙管理委員会",
        "総合教育会議",
        "監査委員",
    )


def _host(url: str) -> str | None:
    try:
        p = urllib.parse.urlsplit(url)
        if not p.scheme or not p.netloc:
            return None
        return p.netloc.lower()
    except Exception:
        return None


def _has_greiki_structure(html_text: str) -> bool:
    low = html_text.lower()
    markers = ["reiki_honbun", "reiki_kana", "reiki_menu", "reiki_taikei", "reiki-base"]
    if any(m in low for m in markers):
        return True
    # Japanese markers
    if "例規" in html_text or "条例" in html_text:
        return True
    return False


def _is_kaigiroku_host(host: str | None) -> bool:
    if host is None:
        return False
    h = host.lower()
    return h == "ssp.kaigiroku.net" or h.endswith(".kaigiroku.net")


def _parse_kaigiroku_tenant(url: str) -> tuple[str | None, str | None]:
    """Return (host, tenant_slug) if url matches kaigiroku tenant pattern."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return None, None
    host = parts.netloc.lower() if parts.netloc else None
    if not _is_kaigiroku_host(host):
        return host, None
    m = re.match(r"^/tenant/([^/]+)(?:/|$)", parts.path)
    if not m:
        return host, None
    return host, m.group(1)


def _has_kaigiroku_entrance(html_text: str, tenant_slug: str) -> bool:
    low = html_text.lower()
    # Real SpTop.html contains kaigiroku-specific UI traces, not just generic 会議録.
    # Generic static minutes pages also contain 会議録 but lack kaigiroku traces;
    # they must not be mistaken for a kaigiroku entrance.
    if "kaigiroku" in low or "ssp.kaigiroku" in low or "sptop" in low:
        return True
    if "会議録検索" in html_text:
        return True
    if "council_list" in low or "committee_list" in low or "tenant.js" in low:
        return True
    # Require both 会議名/会議録 and tenant hint to avoid generic false positive
    if (
        tenant_slug
        and tenant_slug.lower() in low
        and ("会議録" in html_text or "議事録" in html_text or "会議名" in html_text)
    ):
        return True
    return False


_DBSR_MINUTES_HINT_RE = re.compile(r"(会議録|議事録|定例会|臨時会|本会議)")


def _is_dbsr_host(host: str | None) -> bool:
    if host is None:
        return False
    h = host.lower()
    return h == "dbsr.jp" or h.endswith(".dbsr.jp")


def _is_council_scope(
    *,
    label: str,
    url: str,
    observed_on: str,
    page_context: str | None,
) -> bool:
    """Replicate bootstrap preflight council scope check."""
    combined_text = f"{label} {page_context or ''}"
    if any(token in combined_text for token in NON_COUNCIL_TOKENS):
        lower_url = url.lower()
        lower_obs = observed_on.lower()
        if not any(
            tok in lower_url or tok in lower_obs
            for tok in ("/gikai", "/shigikai", "/council", "/assembly")
        ):
            return False
        if any(t in combined_text for t in ("教育委員会", "農業委員会", "審議会")):
            return False
    blob = f"{label} {url} {observed_on} {page_context or ''}".lower()
    has_text = any(tok.lower() in blob for tok in COUNCIL_TEXT_TOKENS)
    if not has_text and "議会" in f"{label} {page_context or ''}":
        has_text = True
    has_url = any(
        tok in url.lower() or tok in observed_on.lower() for tok in COUNCIL_URL_TOKENS
    )
    return bool(has_text or has_url)


def _is_council_document_scope(*, label: str, url: str, observed_on: str) -> bool:
    """Check council scope for a document link (label/url only, no page_context)."""
    combined_text = f"{label} {url}"
    if any(token in combined_text for token in NON_COUNCIL_TOKENS):
        lower_url = url.lower()
        lower_obs = observed_on.lower()
        if not any(
            tok in lower_url or tok in lower_obs
            for tok in ("/gikai", "/shigikai", "/council", "/assembly")
        ):
            return False
        if any(t in combined_text for t in ("教育委員会", "農業委員会", "審議会")):
            return False
    blob = f"{label} {url} {observed_on}".lower()
    has_text = any(tok.lower() in blob for tok in COUNCIL_TEXT_TOKENS)
    if not has_text and "議会" in label:
        has_text = True
    has_url = any(
        tok in url.lower() or tok in observed_on.lower() for tok in COUNCIL_URL_TOKENS
    )
    return bool(has_text or has_url)


def _is_minutes_document_link(*, label: str, url: str) -> bool:
    """Return True if link is a minutes document: .pdf or label/URL has minutes token."""
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    lower_url = url.lower()
    # pdf qualifies regardless of minutes token
    is_pdf = ext == ".pdf" or lower_url.split("?", 1)[0].endswith(".pdf")
    if is_pdf:
        return True
    decoded_url = urllib.parse.unquote(url)
    has_minutes = (
        "会議録" in label
        or "議事録" in label
        or "会議録" in decoded_url
        or "議事録" in decoded_url
        or "minutes" in label.lower()
        or "minutes" in lower_url
    )
    return bool(has_minutes)


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


class _MinutesPageParser(HTMLParser):
    """Extract title/H1..H6 context and links for minutes verify."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._capture_context = False
        self._context: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name in {"title", "h1", "h2", "h3", "h4", "h5", "h6"}:
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
        if tag_name in {"title", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._capture_context = False
        if tag_name == "a" and self._href is not None:
            self.links.append((self._href, _collapse("".join(self._link_text))))
            self._href = None
            self._link_text = []

    def context(self) -> str:
        return _collapse(" ".join(self._context))


def _canonical_url(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )


def _decode_html(result: Any) -> str:
    if hasattr(result, "text") and callable(result.text):  # type: ignore[no-any-return]
        return str(result.text())  # type: ignore[no-any-return,attr-defined]
    body = result.body if hasattr(result, "body") else b""  # type: ignore[attr-defined]
    enc = result.encoding if hasattr(result, "encoding") else "utf-8"  # type: ignore[attr-defined]
    if isinstance(body, bytes):
        return body.decode(enc or "utf-8", errors="replace")  # type: ignore[union-attr]
    return str(body)


def _verify_minutes_static(
    profile: dict[str, Any],
    updated: dict[str, Any],
    entry: dict[str, Any],
    *,
    client: Any,
    now: str,
    municipality: str,
    status_before: Any,
    adapter: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    index_url = entry.get("index_url")
    if not isinstance(index_url, str) or not index_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "missing index_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report
    # Attempt fetch
    try:
        result = client.fetch(index_url, tier=CacheTier.INDEX)
    except Exception as exc:
        err_name = type(exc).__name__
        reason = f"{err_name}: {exc}"
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": reason,
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    # Host drift check
    official_home_url = profile.get("official_home_url")
    entry_host = (
        _host(str(official_home_url)) if isinstance(official_home_url, str) else None
    )
    final_url_val = result.final_url if hasattr(result, "final_url") else index_url  # type: ignore[attr-defined]
    final_host = _host(str(final_url_val))
    if entry_host is not None and final_host is not None and entry_host != final_host:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: official {entry_host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    # Decode HTML
    try:
        html_text = _decode_html(result)
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"decode_error: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    parser = _MinutesPageParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass

    # New rule: ready iff official host and page has at least one council-scoped minutes document link.
    # Removed old "title/H1 must have council scope" gate; title council scope is bonus only.
    # A council minutes document link = (.pdf OR label/URL contains 会議録/議事録/minutes) AND label/URL has council token AND not non-council (rescued only under /gikai etc).
    def _has_council_doc_on_page(
        links: list[tuple[str, str]], observed_url: str, page_context: str
    ) -> bool:
        for href, label in links:
            resolved = _canonical_url(urllib.parse.urljoin(str(observed_url), href))
            if resolved is None:
                continue
            if not _is_minutes_document_link(label=label, url=resolved):
                continue
            if _is_council_document_scope(
                label=label, url=resolved, observed_on=str(observed_url)
            ):
                return True
            # Fallback: page context contains council token (e.g., headings 定例会)
            # Allows generic "1日目" PDFs under a council heading to count.
            if page_context and _is_council_scope(
                label=label,
                url=resolved,
                observed_on=str(observed_url),
                page_context=page_context,
            ):
                return True
        return False

    page_context = parser.context()
    has_council_doc = _has_council_doc_on_page(
        parser.links, str(final_url_val), page_context
    )
    if has_council_doc:
        # Direct success on index (no follow needed)
        sha256 = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
        fetched_at = result.fetched_at if hasattr(result, "fetched_at") else now  # type: ignore[attr-defined]
        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
            entry["evidence"] = evidence
        duplicate = False
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            if ev.get("url") == index_url and ev.get("sha256") == sha256:
                duplicate = True
                break
        if not duplicate:
            new_ev: dict[str, Any] = {
                "url": index_url,
                "observed_on": index_url,
                "sha256": sha256,
                "fetched_at": fetched_at,
            }
            evidence.append(new_ev)
        entry["verified_at"] = now
        entry["verified_by"] = "verify --live"
        if status_before == "needs_review":
            entry["status"] = "ready"
        elif status_before != "ready":
            entry["status"] = "ready"
        status_after = entry.get("status")
        errs = validate_profile(updated)
        if errs:
            report = {
                "municipality": municipality,
                "kind": "minutes",
                "adapter": adapter,
                "result": "failed",
                "reason": f"post-verify validation failed: {errs}",
                "status_before": status_before,
                "status_after": status_before,
                "index_url": index_url,
            }
            return copy.deepcopy(profile), report
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "verified",
            "reason": "ok",
            "status_before": status_before,
            "status_after": status_after,
            "index_url": index_url,
            "final_url": final_url_val,
            "sha256": sha256,
            "fetched_at": fetched_at,
        }
        return updated, report

    # No document on index -> try follow (BFS, depth/pages configurable)
    config = entry.get("config")
    follow_regex_raw: str | None = None
    if isinstance(config, dict):
        raw = config.get("follow_link_regex")
        if isinstance(raw, str) and raw.strip():
            follow_regex_raw = raw
    if follow_regex_raw is None:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "no_council_document_link: page has no council-scoped minutes document link (.pdf or label/URL contains 会議録/議事録/minutes with council token)",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    # Validate follow_max_depth / follow_max_pages (int, bounded)
    follow_max_depth = 1
    follow_max_pages = 3
    if isinstance(config, dict):
        if "follow_max_depth" in config:
            raw_depth = config["follow_max_depth"]
            if isinstance(raw_depth, bool) or not isinstance(raw_depth, int):
                report = {
                    "municipality": municipality,
                    "kind": "minutes",
                    "adapter": adapter,
                    "result": "failed",
                    "reason": f"invalid follow_max_depth: {raw_depth!r} must be int 1..3",
                    "status_before": status_before,
                    "status_after": status_before,
                    "index_url": index_url,
                    "final_url": final_url_val,
                }
                return updated, report
            if not (1 <= raw_depth <= 3):
                report = {
                    "municipality": municipality,
                    "kind": "minutes",
                    "adapter": adapter,
                    "result": "failed",
                    "reason": f"invalid follow_max_depth: {raw_depth!r} must be 1..3",
                    "status_before": status_before,
                    "status_after": status_before,
                    "index_url": index_url,
                    "final_url": final_url_val,
                }
                return updated, report
            follow_max_depth = raw_depth
        if "follow_max_pages" in config:
            raw_pages = config["follow_max_pages"]
            if isinstance(raw_pages, bool) or not isinstance(raw_pages, int):
                report = {
                    "municipality": municipality,
                    "kind": "minutes",
                    "adapter": adapter,
                    "result": "failed",
                    "reason": f"invalid follow_max_pages: {raw_pages!r} must be int 1..10",
                    "status_before": status_before,
                    "status_after": status_before,
                    "index_url": index_url,
                    "final_url": final_url_val,
                }
                return updated, report
            if not (1 <= raw_pages <= 10):
                report = {
                    "municipality": municipality,
                    "kind": "minutes",
                    "adapter": adapter,
                    "result": "failed",
                    "reason": f"invalid follow_max_pages: {raw_pages!r} must be 1..10",
                    "status_before": status_before,
                    "status_after": status_before,
                    "index_url": index_url,
                    "final_url": final_url_val,
                }
                return updated, report
            follow_max_pages = raw_pages
    # Validate regex
    try:
        follow_pat = re.compile(follow_regex_raw)
    except re.error as exc:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"invalid follow_link_regex: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    # BFS: at every level same-host, HTML-only, regex match, dedupe.
    # follow_max_pages caps fetched follow pages; at most 3 candidates are
    # collected per page (same as the historical depth-1 behavior).
    seen_follow: set[str] = set()
    per_page_limit = 3

    def _collect_follow_links(
        links: list[tuple[str, str]], base_url: str, limit: int
    ) -> list[str]:
        out: list[str] = []
        for href, label in links:
            if len(out) >= limit:
                break
            resolved = _canonical_url(urllib.parse.urljoin(str(base_url), href))
            if resolved is None:
                continue
            cand_host = _host(resolved)
            if (
                entry_host is not None
                and cand_host is not None
                and cand_host != entry_host
            ):
                continue
            path_lower = Path(urllib.parse.urlsplit(resolved).path).suffix.lower()
            if path_lower == ".pdf":
                continue
            decoded = urllib.parse.unquote(resolved)
            if not (
                follow_pat.search(label)
                or follow_pat.search(resolved)
                or follow_pat.search(decoded)
            ):
                continue
            if resolved in seen_follow:
                continue
            seen_follow.add(resolved)
            out.append(resolved)
        return out

    initial = _collect_follow_links(parser.links, str(final_url_val), per_page_limit)
    if not initial:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "no_follow_candidate: no link matches follow_link_regex on same host (HTML only)",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    queue: deque[tuple[str, int]] = deque((u, 1) for u in initial)
    last_error: str | None = None
    fetched_any = False
    success_follow_result: Any | None = None
    success_follow_final: str | None = None
    fetched_follow_pages = 0
    while queue:
        if fetched_follow_pages >= follow_max_pages:
            break
        cand_url, cand_depth = queue.popleft()
        try:
            f_result = client.fetch(cand_url, tier=CacheTier.INDEX)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        fetched_any = True
        fetched_follow_pages += 1
        f_final = f_result.final_url if hasattr(f_result, "final_url") else cand_url  # type: ignore[attr-defined]
        f_host = _host(str(f_final))
        if entry_host is not None and f_host is not None and f_host != entry_host:
            last_error = f"host drift on follow: {entry_host!r} -> {f_host!r}"
            continue
        try:
            f_html = _decode_html(f_result)
        except Exception as exc:  # noqa: BLE001
            last_error = f"decode_error: {exc}"
            continue
        f_parser = _MinutesPageParser()
        try:
            f_parser.feed(f_html)
        except Exception:
            pass
        f_context = f_parser.context()
        if _has_council_doc_on_page(f_parser.links, str(f_final), f_context):
            success_follow_result = f_result
            success_follow_final = str(f_final)
            break
        if cand_depth < follow_max_depth:
            next_cands = _collect_follow_links(
                f_parser.links, str(f_final), per_page_limit
            )
            for nxt in next_cands:
                queue.append((nxt, cand_depth + 1))
    if success_follow_result is None:
        if not fetched_any:
            report = {
                "municipality": municipality,
                "kind": "minutes",
                "adapter": adapter,
                "result": "failed",
                "reason": last_error
                or "no_council_document_link: follow pages contain no council-scoped minutes document link",
                "status_before": status_before,
                "status_after": status_before,
                "index_url": index_url,
                "final_url": final_url_val,
            }
            return updated, report
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "no_council_document_link: follow pages contain no council-scoped minutes document link (.pdf or label/URL contains 会議録/議事録/minutes with council token)",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    # Success via follow: add evidence for both root and follow (idempotent on url+sha256)
    root_sha = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
    root_fetched = result.fetched_at if hasattr(result, "fetched_at") else now  # type: ignore[attr-defined]
    follow_sha = (
        success_follow_result.sha256 if hasattr(success_follow_result, "sha256") else ""
    )  # type: ignore[attr-defined]
    follow_fetched = (
        success_follow_result.fetched_at
        if hasattr(success_follow_result, "fetched_at")
        else now
    )  # type: ignore[attr-defined]
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        entry["evidence"] = evidence
    # Root evidence
    duplicate_root = any(
        isinstance(ev, dict)
        and ev.get("url") == index_url
        and ev.get("sha256") == root_sha
        for ev in evidence
    )
    if not duplicate_root:
        evidence.append(
            {
                "url": index_url,
                "observed_on": index_url,
                "sha256": root_sha,
                "fetched_at": root_fetched,
            }
        )
    # Follow evidence (observed_on is root index)
    follow_url_val = success_follow_final or initial[0]
    duplicate_follow = any(
        isinstance(ev, dict)
        and ev.get("url") == follow_url_val
        and ev.get("sha256") == follow_sha
        for ev in evidence
    )
    if not duplicate_follow:
        evidence.append(
            {
                "url": follow_url_val,
                "observed_on": index_url,
                "sha256": follow_sha,
                "fetched_at": follow_fetched,
            }
        )
    entry["verified_at"] = now
    entry["verified_by"] = "verify --live"
    if status_before == "needs_review":
        entry["status"] = "ready"
    elif status_before != "ready":
        entry["status"] = "ready"
    status_after = entry.get("status")
    errs = validate_profile(updated)
    if errs:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return copy.deepcopy(profile), report
    report = {
        "municipality": municipality,
        "kind": "minutes",
        "adapter": adapter,
        "result": "verified",
        "reason": "ok via follow",
        "status_before": status_before,
        "status_after": status_after,
        "index_url": index_url,
        "final_url": follow_url_val,
        "sha256": follow_sha,
        "fetched_at": follow_fetched,
    }
    return updated, report


def _verify_minutes_kaigiroku_net(
    profile: dict[str, Any],
    updated: dict[str, Any],
    entry: dict[str, Any],
    *,
    client: Any,
    now: str,
    municipality: str,
    status_before: Any,
    adapter: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tenant_url = entry.get("tenant_url")
    if not isinstance(tenant_url, str) or not tenant_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "invalid_tenant_url: missing tenant_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return copy.deepcopy(profile), report
    tenant_url = tenant_url.strip()
    host, tenant_slug = _parse_kaigiroku_tenant(tenant_url)
    if tenant_slug is None:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"invalid_tenant_url: tenant_url {tenant_url!r} must be https://ssp.kaigiroku.net/tenant/<name>/... with host ssp.kaigiroku.net",
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
        }
        return copy.deepcopy(profile), report
    # Fetch only the tenant_url page via HttpClient (robots respected)
    try:
        result = client.fetch(tenant_url, tier=CacheTier.INDEX)
    except Exception as exc:
        err_name = type(exc).__name__
        reason = f"{err_name}: {exc}"
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": reason,
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
        }
        return updated, report
    final_url_val = result.final_url if hasattr(result, "final_url") else tenant_url  # type: ignore[attr-defined]
    final_host = _host(str(final_url_val))
    if not _is_kaigiroku_host(final_host):
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: tenant {host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
            "final_url": final_url_val,
        }
        return updated, report
    try:
        html_text = _decode_html(result)
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"decode_error: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
        }
        return updated, report
    # Structure check: kaigiroku entrance markers
    if not _has_kaigiroku_entrance(html_text, tenant_slug):
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "structure_mismatch: kaigiroku entrance does not contain expected markers (会議録/kaigiroku/SpTop/tenant)",
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
            "final_url": final_url_val,
        }
        return updated, report
    # All checks passed -> promote
    sha256 = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
    fetched_at = result.fetched_at if hasattr(result, "fetched_at") else now  # type: ignore[attr-defined]
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        entry["evidence"] = evidence
    duplicate = False
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        if ev.get("url") == tenant_url and ev.get("sha256") == sha256:
            duplicate = True
            break
    if not duplicate:
        new_ev: dict[str, Any] = {
            "url": tenant_url,
            "observed_on": tenant_url,
            "sha256": sha256,
            "fetched_at": fetched_at,
        }
        evidence.append(new_ev)
    entry["verified_at"] = now
    entry["verified_by"] = "verify --live"
    if status_before == "needs_review":
        entry["status"] = "ready"
    elif status_before != "ready":
        entry["status"] = "ready"
    status_after = entry.get("status")
    errs = validate_profile(updated)
    if errs:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_before": status_before,
            "status_after": status_before,
            "tenant_url": tenant_url,
        }
        return copy.deepcopy(profile), report
    report = {
        "municipality": municipality,
        "kind": "minutes",
        "adapter": adapter,
        "result": "verified",
        "reason": "ok",
        "status_before": status_before,
        "status_after": status_after,
        "tenant_url": tenant_url,
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return updated, report


def _first_dbsr_meeting_link(
    html_text: str, links: list[tuple[str, str]], observed_url: str
) -> str | None:
    """Return first same-host meeting link (detail or query-list or document).

    Shared helper for entrance check and robots-aware body probe to avoid
    duplicating discovery logic. A meeting link is a same-host URL that is
    either under ``/index.php/<id>``, the observed query-list form
    ``/index.php?QueryType=New&Template=List`` with a meeting label, or a
    minutes document link. Requires a minutes hint on the page.
    """
    if not _DBSR_MINUTES_HINT_RE.search(html_text):
        return None
    observed_host = _host(observed_url)
    for href, label in links:
        resolved = _canonical_url(urllib.parse.urljoin(str(observed_url), href))
        if resolved is None:
            continue
        if _host(resolved) != observed_host:
            continue
        resolved_parts = urllib.parse.urlsplit(resolved)
        path = resolved_parts.path
        if re.search(r"/index\.php/.+", path):
            return resolved
        query = urllib.parse.parse_qs(resolved_parts.query)
        if (
            path.rstrip("/") == "/index.php"
            and query.get("QueryType") == ["New"]
            and query.get("Template") == ["List"]
            and _DBSR_MINUTES_HINT_RE.search(label)
        ):
            return resolved
        if _is_minutes_document_link(label=label, url=resolved):
            return resolved
    return None


def _has_dbsr_minutes_entrance(
    html_text: str, links: list[tuple[str, str]], observed_url: str
) -> bool:
    """Confirm a dbsr page is an active council minutes index.

    Requires both a minutes hint term and at least one same-host link into an
    ``/index.php/<id>`` detail page, or an observed dbsr list query
    (``QueryType=New&Template=List``) whose label names a meeting. A bare,
    empty, or error page on the vendor host must not promote.
    """
    return _first_dbsr_meeting_link(html_text, links, observed_url) is not None


def _verify_minutes_dbsr(
    profile: dict[str, Any],
    updated: dict[str, Any],
    entry: dict[str, Any],
    *,
    client: Any,
    now: str,
    municipality: str,
    status_before: Any,
    adapter: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    index_url = entry.get("index_url")
    if not isinstance(index_url, str) or not index_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "invalid_index_url: missing index_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report
    index_url = index_url.strip()
    parts = urllib.parse.urlsplit(index_url)
    if not _is_dbsr_host(parts.netloc.lower() if parts.netloc else None) or (
        "/index.php" not in parts.path
    ):
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"invalid_index_url: index_url {index_url!r} must be a *.dbsr.jp URL containing /index.php",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    # Fetch only the observed index_url via HttpClient (robots respected)
    try:
        result = client.fetch(index_url, tier=CacheTier.INDEX)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": reason,
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    final_url_val = result.final_url if hasattr(result, "final_url") else index_url  # type: ignore[attr-defined]
    final_host = _host(str(final_url_val))
    if not _is_dbsr_host(final_host):
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: dbsr index -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    try:
        html_text = _decode_html(result)
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"decode_error: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report
    parser = _MinutesPageParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    if not _has_dbsr_minutes_entrance(html_text, parser.links, str(final_url_val)):
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": "structure_mismatch: dbsr index has no minutes hint with a supported detail/list link",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report
    # Robots-aware body probe: fetch first meeting link once via HttpClient
    meeting_url = _first_dbsr_meeting_link(html_text, parser.links, str(final_url_val))
    if meeting_url is not None:
        try:
            _probe_result = client.fetch(meeting_url, tier=CacheTier.INDEX)
        except RobotsDeniedError as exc:
            blocked_sha256 = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
            blocked_fetched_at = (
                result.fetched_at if hasattr(result, "fetched_at") else now
            )  # type: ignore[attr-defined]
            evidence = entry.get("evidence")
            if not isinstance(evidence, list):
                evidence = []
                entry["evidence"] = evidence
            duplicate = False
            for ev in evidence:
                if not isinstance(ev, dict):
                    continue
                if ev.get("url") == index_url and ev.get("sha256") == blocked_sha256:
                    duplicate = True
                    break
            if not duplicate:
                blocked_ev: dict[str, Any] = {
                    "url": index_url,
                    "observed_on": index_url,
                    "sha256": blocked_sha256,
                    "fetched_at": blocked_fetched_at,
                }
                evidence.append(blocked_ev)
            entry["verified_at"] = now
            entry["verified_by"] = "verify --live"
            entry["status"] = "blocked"
            blocked_note = (
                "minutes bodies are robots-restricted (robots.txt disallows meeting detail/document paths); "
                "observed Saga dbsr tenants block bodies, so ingestion requires the councilor/user to obtain municipality permission "
                "(out of scope for automated ingestion)"
            )
            existing_notes = entry.get("notes")
            if (
                not isinstance(existing_notes, str)
                or "robots" not in existing_notes.lower()
            ):
                if isinstance(existing_notes, str) and existing_notes.strip():
                    entry["notes"] = existing_notes.rstrip() + " " + blocked_note
                else:
                    entry["notes"] = blocked_note
            errs = validate_profile(updated)
            if errs:
                report = {
                    "municipality": municipality,
                    "kind": "minutes",
                    "adapter": adapter,
                    "result": "failed",
                    "reason": f"post-verify validation failed: {errs}",
                    "status_before": status_before,
                    "status_after": status_before,
                    "index_url": index_url,
                }
                return copy.deepcopy(profile), report
            report = {
                "municipality": municipality,
                "kind": "minutes",
                "adapter": adapter,
                "result": "blocked",
                "reason": f"RobotsDeniedError: {exc} (minutes bodies are robots-restricted)",
                "status_before": status_before,
                "status_after": "blocked",
                "index_url": index_url,
                "final_url": final_url_val,
                "sha256": blocked_sha256,
                "fetched_at": blocked_fetched_at,
                "meeting_url": meeting_url,
            }
            return updated, report
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            report = {
                "municipality": municipality,
                "kind": "minutes",
                "adapter": adapter,
                "result": "failed",
                "reason": reason,
                "status_before": status_before,
                "status_after": status_before,
                "index_url": index_url,
                "final_url": final_url_val,
            }
            return updated, report
    # All checks passed -> promote
    sha256 = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
    fetched_at = result.fetched_at if hasattr(result, "fetched_at") else now  # type: ignore[attr-defined]
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        entry["evidence"] = evidence
    duplicate = False
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        if ev.get("url") == index_url and ev.get("sha256") == sha256:
            duplicate = True
            break
    if not duplicate:
        new_ev: dict[str, Any] = {
            "url": index_url,
            "observed_on": index_url,
            "sha256": sha256,
            "fetched_at": fetched_at,
        }
        evidence.append(new_ev)
    entry["verified_at"] = now
    entry["verified_by"] = "verify --live"
    if status_before != "ready":
        entry["status"] = "ready"
    status_after = entry.get("status")
    errs = validate_profile(updated)
    if errs:
        report = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return copy.deepcopy(profile), report
    report = {
        "municipality": municipality,
        "kind": "minutes",
        "adapter": adapter,
        "result": "verified",
        "reason": "ok",
        "status_before": status_before,
        "status_after": status_after,
        "index_url": index_url,
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return updated, report


def verify_profile(
    profile: dict[str, Any],
    *,
    client: Any,
    now: str,
    kind: str = "regulations",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a single source entry via HttpClient.

    Returns (updated_profile, report).  updated_profile is a copy; original is not mutated.
    Report contains: municipality, kind, adapter, result, reason, status_before, status_after, entry_url.
    """
    updated = copy.deepcopy(profile)
    municipality = str(profile.get("municipality", ""))
    sources = updated.get("sources", {})
    entry = sources.get(kind) if isinstance(sources, dict) else None

    if not isinstance(entry, dict):
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": kind,
            "adapter": None,
            "result": "failed",
            "reason": f"kind {kind!r} not found in profile",
            "status_before": None,
            "status_after": None,
        }
        return updated, report

    adapter = entry.get("adapter")
    status_before = entry.get("status")

    # Minutes: only static is supported in this increment
    if kind == "minutes":
        if adapter == "static":
            return _verify_minutes_static(
                profile,
                updated,
                entry,
                client=client,
                now=now,
                municipality=municipality,
                status_before=status_before,
                adapter=adapter,
            )
        if adapter == "kaigiroku_net":
            return _verify_minutes_kaigiroku_net(
                profile,
                updated,
                entry,
                client=client,
                now=now,
                municipality=municipality,
                status_before=status_before,
                adapter=adapter,
            )
        if adapter == "dbsr":
            return _verify_minutes_dbsr(
                profile,
                updated,
                entry,
                client=client,
                now=now,
                municipality=municipality,
                status_before=status_before,
                adapter=adapter,
            )
        # voices / null / other
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"verify unsupported for adapter {adapter!r} kind {kind!r}: この増分では未対応",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report

    # Only g_reiki regulations is supported for verify (legacy)
    if adapter != "g_reiki" or kind != "regulations":
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"verify unsupported for adapter {adapter!r} kind {kind!r}",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report

    base_url = entry.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": "missing base_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report

    # Ensure base_url ends with /
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    entry_url = urllib.parse.urljoin(base_url, "reiki_menu.html")

    # Attempt fetch
    try:
        result = client.fetch(entry_url, tier=CacheTier.INDEX)
    except Exception as exc:
        # Distinguish robots/offline/fetch
        err_name = type(exc).__name__
        reason = f"{err_name}: {exc}"
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": reason,
            "status_before": status_before,
            "status_after": status_before,
            "entry_url": entry_url,
        }
        return updated, report

    # Host drift check
    entry_host = _host(base_url)
    final_url_val = result.final_url if hasattr(result, "final_url") else entry_url
    final_host = _host(final_url_val)
    if entry_host is not None and final_host is not None and entry_host != final_host:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: base {entry_host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "entry_url": entry_url,
            "final_url": final_url_val,
        }
        return updated, report

    # Structure check: decode HTML
    try:
        # FetchResult has .text() or .body + encoding
        if hasattr(result, "text") and callable(result.text):  # type: ignore[no-any-return]
            html_text_any = result.text()  # type: ignore[no-any-return,attr-defined]
            html_text = str(html_text_any)
        else:
            body = result.body if hasattr(result, "body") else b""  # type: ignore[attr-defined]
            enc = result.encoding if hasattr(result, "encoding") else "utf-8"  # type: ignore[attr-defined]
            if isinstance(body, bytes):
                html_text = body.decode(enc or "utf-8", errors="replace")  # type: ignore[union-attr]
            else:
                html_text = str(body)
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"decode_error: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "entry_url": entry_url,
        }
        return updated, report

    if not _has_greiki_structure(html_text):
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": "structure_mismatch: g-reiki entry does not contain expected markers (reiki_*/例規)",
            "status_before": status_before,
            "status_after": status_before,
            "entry_url": entry_url,
        }
        return updated, report

    # All checks passed -> promote
    # Update evidence (idempotent)
    sha256 = result.sha256 if hasattr(result, "sha256") else ""  # type: ignore[attr-defined]
    fetched_at = result.fetched_at if hasattr(result, "fetched_at") else now  # type: ignore[attr-defined]

    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        entry["evidence"] = evidence

    # Check duplicate url+sha256
    duplicate = False
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        if ev.get("url") == entry_url and ev.get("sha256") == sha256:
            duplicate = True
            break

    if not duplicate:
        new_ev: dict[str, Any] = {
            "url": entry_url,
            "observed_on": base_url,
            "sha256": sha256,
            "fetched_at": fetched_at,
        }
        evidence.append(new_ev)

    entry["verified_at"] = now
    entry["verified_by"] = "verify --live"
    # Promote needs_review -> ready, keep ready as ready
    if status_before == "needs_review":
        entry["status"] = "ready"
    elif status_before != "ready":
        # For other statuses like not_evaluated etc, promote to ready if g_reiki verified
        # But spec expects only needs_review->ready; keep current behavior for safety
        entry["status"] = "ready"

    status_after = entry.get("status")

    # Self-validate before saving
    errs = validate_profile(updated)
    if errs:
        # Rollback? Return failed report without considering saved
        # Restore original status/evidence for report purposes but return failed
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_before": status_before,
            "status_after": status_before,
            "entry_url": entry_url,
        }
        # Return original-like updated without promotion? Actually return failed with original profile copy
        # To avoid partial update, revert to original copy for caller to not save
        return copy.deepcopy(profile), report

    final_url_report = result.final_url if hasattr(result, "final_url") else entry_url  # type: ignore[attr-defined]
    report = {
        "municipality": municipality,
        "kind": kind,
        "adapter": adapter,
        "result": "verified",
        "reason": "ok",
        "status_before": status_before,
        "status_after": status_after,
        "entry_url": entry_url,
        "final_url": final_url_report,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return updated, report
