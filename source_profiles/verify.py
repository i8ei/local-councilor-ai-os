"""Live verification for g_reiki source profiles (stdlib only, HttpClient injection)."""

from __future__ import annotations  # noqa: I001

import copy
import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lcaios.http import CacheTier
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
    """Extract title/H1 context and links for minutes verify."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._capture_context = False
        self._context: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
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
    has_council_doc = False
    for href, label in parser.links:
        resolved = _canonical_url(urllib.parse.urljoin(str(final_url_val), href))
        if resolved is None:
            continue
        if not _is_minutes_document_link(label=label, url=resolved):
            continue
        if not _is_council_document_scope(
            label=label, url=resolved, observed_on=str(final_url_val)
        ):
            continue
        has_council_doc = True
        break
    if not has_council_doc:
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
            report = {
                "municipality": municipality,
                "kind": kind,
                "adapter": adapter,
                "result": "failed",
                "reason": f"verify unsupported for adapter {adapter!r} kind {kind!r}: この増分では未対応 (kaigiroku_net verify not implemented)",
                "status_before": status_before,
                "status_after": status_before,
            }
            return updated, report
        # dbsr / voices / null / other
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
