"""Live verification for g_reiki source profiles (stdlib only, HttpClient injection)."""

from __future__ import annotations  # noqa: I001

import copy
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from lcaios.http import CacheTier, RobotsDeniedError, canonical_url
from modules.minutes_db.adapters.dbsr import DbsrAdapter
from modules.minutes_db.adapters.kaigiroku_net import KaigirokuNetAdapter
from modules.minutes_db.adapters.static_html import StaticHtmlAdapter
from modules.regulations.vendor_d1law_opensearch import (
    discover_documents as discover_documents_d1law_opensearch,
    fetch_document as fetch_document_d1law_opensearch,
)
from modules.regulations.vendor_d1law_reiki import (
    discover_documents as discover_documents_d1law,
    fetch_document as fetch_document_d1law,
)
from modules.regulations.vendor_greiki import (
    discover_documents as discover_documents_greiki,
    fetch_document as fetch_document_greiki,
)
from modules.regulations.vendor_joureikun import (
    discover_documents as discover_documents_joureikun,
    fetch_document as fetch_document_joureikun,
)
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
        if p.hostname:
            return p.hostname.lower()
        return p.netloc.lower().split(":")[0]
    except Exception:
        return None


def _is_allowed_host_drift(
    entry_host: str | None,
    final_host: str | None,
    *,
    adapter: str | None = None,
    kind: str | None = None,
) -> bool:
    """Check if redirection between entry_host and final_host is a safe, allowed transition."""
    if entry_host is None or final_host is None:
        return False
    if entry_host == final_host:
        return True

    def _strip_www(h: str) -> str:
        return h[4:] if h.startswith("www.") else h

    # 1. www. prefix normalization: asukamura.jp <-> www.asukamura.jp
    if _strip_www(entry_host) == _strip_www(final_host):
        return True

    # 2. Domain migration / transition within same municipality prefix:
    # e.g., www.city.naruto.tokushima.jp -> www.city.naruto.lg.jp
    # e.g., reiki.town.kumenan.okayama.jp -> reiki.town.kumenan.lg.jp
    # e.g., www.city.kashiwara.osaka.jp -> www.city.kashiwara.lg.jp
    # e.g., www.mikurasima.jp -> www.vill.mikurasima.tokyo.jp
    e_clean = _strip_www(entry_host)
    f_clean = _strip_www(final_host)
    e_parts = e_clean.split(".")
    f_parts = f_clean.split(".")
    if (
        entry_host.endswith(".jp")
        and final_host.endswith(".jp")
        and len(e_parts) >= 2
        and len(f_parts) >= 2
    ):
        if e_parts[0] == f_parts[0]:
            return True
        if len(e_parts) >= 3 and len(f_parts) >= 3 and e_parts[-3] == f_parts[-3]:
            return True

    # 3. Municipality domain redirecting to official vendor domain for that adapter
    if adapter == "d1_law":
        if final_host.endswith(".d1-law.com"):
            return True
    elif adapter == "g_reiki":
        if final_host.endswith(".g-reiki.net") or final_host.endswith(".legal-square.com"):
            return True
    elif adapter == "joureikun":
        if "joureikun" in final_host:
            return True

    return False


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
    page_context: str | None = None,
) -> bool:
    """Replicate bootstrap preflight council scope check.

    With ``page_context=None`` this doubles as the document-link-only check
    (formerly _is_council_document_scope).
    """
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


# ---------------------------------------------------------------------------
# budget / settlement verification (kind "budget" / "settlement")
#
# Boundary: the repo provides NO generic budget/settlement web extractor
# (extraction_guidance delegates record extraction to the user's AI, and
# ingest_csv consumes human/AI-made CSVs). So verify can never reach `ready`
# for these kinds under the ready definition (adapter-extracted records): the
# reachable honest states are `needs_review` (entry + real document +
# structural markers confirmed) and `blocked` (robots). A human grants
# `ready` after ingestion. Preflight-derived `ready` entries are re-verified
# here and land on needs_review with fresh evidence.
# ---------------------------------------------------------------------------

_BUDGET_MARKERS = ("歳入", "歳出", "款", "項", "予算額", "予算総額")
_SETTLEMENT_MARKERS = ("歳入", "歳出", "款", "項", "決算額", "決算総額")
_BUDGET_DOC_EXTENSIONS = (".pdf", ".xlsx", ".xls")


def _is_budget_settlement_document_link(
    *, label: str, url: str, kind: str
) -> bool:
    """A document link whose label/URL matches the budget/settlement theme."""
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if ext not in _BUDGET_DOC_EXTENSIONS:
        return False
    blob = f"{label} {urllib.parse.unquote(url)}".lower()
    if kind == "settlement":
        return "決算" in blob
    return "予算" in blob and "決算" not in blob


def _budget_settlement_doc_urls(
    links: list[tuple[str, str]], observed_url: str, kind: str
) -> list[str]:
    out: list[str] = []
    for href, label in links:
        resolved = canonical_url(
            urllib.parse.urljoin(str(observed_url), href),
            strict=False,
        )
        if resolved is None:
            continue
        if _host(str(resolved)) != _host(str(observed_url)):
            continue
        if _is_budget_settlement_document_link(label=label, url=resolved, kind=kind):
            out.append(resolved)
    return out


def _probe_budget_settlement_doc(
    *, document_url: str, client: Any
) -> tuple[str, Any, str]:
    """Fetch one document and return (text, adapter_status, media_type).

    Mirrors the minutes PDF probe: a missing pdftotext binary is
    INCONCLUSIVE (learns nothing about the source), never a verdict.
    """
    fetched = client.fetch(document_url, tier=CacheTier.DOCUMENT)
    media_type = (
        str(fetched.content_type).lower()
        if hasattr(fetched, "content_type") and fetched.content_type
        else ""
    )
    path_lower = document_url.lower().split("?", 1)[0]
    is_pdf = path_lower.endswith(".pdf") or media_type == "application/pdf"
    if is_pdf:
        tool = shutil.which("pdftotext")
        if tool is None:
            return "", "pdf_cached_pdftotext_unavailable", media_type
        input_path = (
            Path(fetched.cache_path) if hasattr(fetched, "cache_path") else None  # type: ignore[attr-defined]
        )
        temporary_path: str | None = None
        if input_path is None or not input_path.exists():
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(fetched.body)  # type: ignore[attr-defined]
                temporary_path = handle.name
            input_path = Path(temporary_path)
        try:
            completed: Any = subprocess.run(  # type: ignore[assignment]
                [tool, "-layout", str(input_path), "-"],
                check=False,
                capture_output=True,
                timeout=120,
            )
        except Exception:  # noqa: BLE001
            # Bare token only: statuses must match _PROBE_STATUS_CLASSIFICATION
            # exactly, or an error would be misread as "document was read".
            return "", "pdf_text_extraction_failed", media_type
        finally:
            if temporary_path:
                Path(temporary_path).unlink(missing_ok=True)
        if completed.returncode != 0:
            return "", "pdf_text_extraction_failed", media_type
        text = completed.stdout.decode("utf-8", errors="replace")
        return text, "extracted", media_type
    if "html" in media_type or path_lower.endswith((".html", ".htm")):
        try:
            return _decode_html(fetched), "extracted", media_type
        except Exception:  # noqa: BLE001
            return "", "decode_error", media_type
    return "", "unprobeable_media_type", media_type


_BUDGET_SETTLEMENT_NOTE_READ = (
    "検証で実文書（予算書/決算書）に到達し、構造マーカー（歳入・歳出・款・項 等）の存在を確認した。"
    "レコード抽出は利用者（既存 CSV 契約）に委ねるため verify は ready を付けられない。"
    "取込後に human が ready を付与する。"
)


def _verify_budget_settlement(
    profile: dict[str, Any],
    updated: dict[str, Any],
    entry: dict[str, Any],
    *,
    client: Any,
    now: str,
    municipality: str,
    status_before: Any,
    adapter: Any,
    kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a budget/settlement entry: entry alive -> real document -> markers.

    Markers confirmed => needs_review + evidence (never ready, see boundary
    note above). robots denial => blocked. Unreadable doc / unprobeable
    format => failed, status untouched.
    """
    index_url = entry.get("index_url")
    if not isinstance(index_url, str) or not index_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": "missing index_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report
    try:
        result = client.fetch(index_url, tier=CacheTier.INDEX)
    except RobotsDeniedError as exc:
        entry["verified_at"] = now
        entry["verified_by"] = "verify --live"
        entry["status"] = "blocked"
        _append_note(entry, _ROBOTS_BLOCKED_NOTE, marker="robots")
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "blocked",
            "reason": f"RobotsDeniedError: {exc} (bodies are robots-restricted)",
            "status_before": status_before,
            "status_after": "blocked",
        }
        return updated, report
    except Exception as exc:  # noqa: BLE001
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report

    final_url_val = result.final_url if hasattr(result, "final_url") else index_url  # type: ignore[attr-defined]
    entry_host = _host(str(index_url))
    final_host = _host(str(final_url_val))
    if entry_host is not None and final_host is not None and entry_host != final_host:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: entry {entry_host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report

    parser = _MinutesPageParser()
    try:
        parser.feed(_decode_html(result))
    except Exception:
        pass

    # Prefer the previously recorded deepest document (preflight evidence —
    # the label is not stored, so extension alone qualifies the candidate;
    # the marker probe below proves it IS a budget/settlement document),
    # then discover fresh from the index page.
    document_url: str | None = None
    evidence = entry.get("evidence")
    if isinstance(evidence, list):
        for item in reversed(evidence):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
            if ext in _BUDGET_DOC_EXTENSIONS:
                document_url = url
                break
    if document_url is None:
        doc_urls = _budget_settlement_doc_urls(
            parser.links, str(final_url_val), kind
        )
        if doc_urls:
            document_url = doc_urls[0]

    pending_evidence = [
        _evidence_item(str(final_url_val), str(final_url_val), result, now)
    ]
    report_base = {
        "municipality": municipality,
        "kind": kind,
        "adapter": adapter,
        "status_before": status_before,
        "index_url": index_url,
        "final_url": final_url_val,
    }

    if document_url is None:
        entry["status"] = "needs_review"
        _append_pending_evidence(entry, pending_evidence)
        _append_note(
            entry,
            f"入口 {final_url_val} に {kind} の文書リンク（PDF/XLSX・予算/決算ラベル）"
            "が見つからない; 本文に到達できたとは言えない",
            marker=f"no {kind} document link",
        )
        errs = validate_profile(updated)
        if errs:
            report = {
                **report_base,
                "result": "failed",
                "reason": f"post-verify validation failed: {errs}",
                "status_after": status_before,
            }
            return copy.deepcopy(profile), report
        report = {
            **report_base,
            "result": "needs_review",
            "reason": "no_document_link: entry fetched but no budget/settlement document link found",
            "status_after": "needs_review",
        }
        return updated, report

    probe_status, probe_payload = _run_extraction_probe(
        lambda: _probe_budget_settlement_doc(
            document_url=document_url, client=client
        )
    )
    if probe_status == "robots":
        robots_exc = probe_payload
        _append_pending_evidence(entry, pending_evidence)
        entry["verified_at"] = now
        entry["verified_by"] = "verify --live"
        entry["status"] = "blocked"
        _append_note(entry, _ROBOTS_BLOCKED_NOTE, marker="robots")
        report = {
            **report_base,
            "result": "blocked",
            "reason": f"RobotsDeniedError: {robots_exc} (bodies are robots-restricted)",
            "status_after": "blocked",
        }
        return updated, report
    if probe_status == "error":
        probe_err = probe_payload
        report = {
            **report_base,
            "result": "failed",
            "reason": f"{type(probe_err).__name__}: {probe_err}",
            "status_after": status_before,
        }
        return updated, report

    text, adapter_status, media_type = probe_payload
    if _classify_probe_status(adapter_status) == "inconclusive" or adapter_status == "unprobeable_media_type":
        report = {
            **report_base,
            "result": "failed",
            "reason": (
                "probe_inconclusive: the adapter could not read the probed "
                f"document (adapter status {adapter_status!r}, media {media_type!r}); "
                "fix the local extraction tooling and re-run"
            ),
            "status_after": status_before,
        }
        return updated, report

    markers = _BUDGET_MARKERS if kind == "budget" else _SETTLEMENT_MARKERS
    confirmed = sum(1 for marker in markers if marker in (text or "")) >= 2
    _append_pending_evidence(entry, pending_evidence)
    _append_pending_evidence(
        entry,
        [
            {
                "url": document_url,
                "observed_on": str(final_url_val),
                "fetched_at": now,
            }
        ],
    )
    entry["status"] = "needs_review"
    if confirmed:
        _append_note(entry, _BUDGET_SETTLEMENT_NOTE_READ, marker="structure markers")
        reason = (
            f"document_structure_confirmed: {kind} document at {document_url} "
            f"read with structural markers {list(markers)}; extraction is the "
            "user's step (no generic extractor), ready stays human-granted"
        )
    else:
        _append_note(
            entry,
            f"{kind} document に到達したが構造マーカー（{'・'.join(markers[:3])} 等）"
            "を確認できなかった",
            marker="no structure markers",
        )
        reason = (
            f"document_reached_without_markers: {kind} document at {document_url} "
            "read but no structural markers found"
        )
    errs = validate_profile(updated)
    if errs:
        report = {
            **report_base,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_after": status_before,
        }
        return copy.deepcopy(profile), report
    report = {
        **report_base,
        "result": "needs_review",
        "reason": reason,
        "status_after": "needs_review",
    }
    return updated, report


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


# R1: `ready` may only be granted from these prior statuses.
_PROMOTABLE_PRIOR_STATUSES = ("needs_review", "not_evaluated")

_ROBOTS_BLOCKED_NOTE = (
    "document bodies reached through this entry are robots-restricted "
    "(robots.txt disallows document paths); ingestion requires the "
    "councilor/user to obtain municipality permission "
    "(out of scope for automated ingestion)"
)


def _append_note(entry: dict[str, Any], note: str, *, marker: str) -> None:
    """Append a note once (idempotent on marker)."""
    existing = entry.get("notes")
    if isinstance(existing, str) and marker in existing.lower():
        return
    if isinstance(existing, str) and existing.strip():
        entry["notes"] = existing.rstrip() + " " + note
    else:
        entry["notes"] = note


def _fetch_meta(result: Any, now: str) -> tuple[str, str]:
    """Return (sha256, fetched_at) tolerating minimal fake clients."""
    sha256 = result.sha256 if hasattr(result, "sha256") else ""
    fetched_at = result.fetched_at if hasattr(result, "fetched_at") else now
    return str(sha256), str(fetched_at)


def _evidence_item(
    url: str, observed_on: str, result: Any, now: str
) -> dict[str, Any]:
    sha256, fetched_at = _fetch_meta(result, now)
    return {
        "url": url,
        "observed_on": observed_on,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }


def _append_pending_evidence(entry: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """Append evidence idempotently on url+sha256."""
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        entry["evidence"] = evidence
    for item in items:
        duplicate = any(
            isinstance(ev, dict)
            and ev.get("url") == item.get("url")
            and ev.get("sha256") == item.get("sha256")
            for ev in evidence
        )
        if not duplicate:
            evidence.append(item)


def _run_extraction_probe(
    probe: Callable[[], tuple[Any, ...]],
) -> tuple[str, Any]:
    """Run one extraction probe; classify outcome as ok/robots/error."""
    try:
        return "ok", probe()
    except RobotsDeniedError as exc:
        return "robots", exc
    except Exception as exc:  # noqa: BLE001
        return "error", exc


# Single classification point for the adapter provenance status returned by a
# probe (C1/C2). "extracted" means the adapter really READ the document, so a
# zero record count is a verdict about the SOURCE (R4: needs_review).
# "inconclusive" means the adapter could NOT read the document (e.g. the
# pdftotext binary was missing), so we learned nothing about the source and
# must not change the status (R5: failed, status untouched).
# Any future adapter status meaning "could not read" MUST be declared here,
# otherwise it will be treated as "the document was read".
_PROBE_STATUS_CLASSIFICATION: dict[str, str] = {
    "extracted": "extracted",
    "html_no_text": "extracted",
    "text_without_segments": "extracted",
    "pdf_no_text": "extracted",  # tool ran fine, output empty: document was read
    "pdf_cached_pdftotext_unavailable": "inconclusive",
    "pdf_text_extraction_failed": "inconclusive",
    # budget/settlement document probe statuses
    "decode_error": "inconclusive",
    "unprobeable_media_type": "inconclusive",
}
# Statuses not listed above (e.g. kaigiroku_net's "discovered") belong to
# adapters that RAISE instead of returning unreadable bodies, so their record
# counts are trusted as-is.


def _classify_probe_status(status: Any) -> str:
    if isinstance(status, str):
        return _PROBE_STATUS_CLASSIFICATION.get(status, "extracted")
    return "extracted"


# C4: promotion requires STRUCTURAL identification, not a bare record count.
# Both extractors fall back to paragraph/document chunks so ingestion never
# loses content; those fallback records carry no identifying field:
#   minutes     -> speech["speaker"] empty on fallback chunks
#   regulations -> article["article_no"] None on document fallback
# A document counts as verified source material only if at least one record
# carries that field. Do not add keyword heuristics here.
def _identifiable_records(
    kind: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if kind == "regulations":
        return [
            r
            for r in records
            if isinstance(r, dict) and str(r.get("article_no") or "").strip()
        ]
    return [
        r
        for r in records
        if isinstance(r, dict) and str(r.get("speaker") or "").strip()
    ]


def _finalize_with_probe(
    probe: Callable[[], tuple[list[dict[str, Any]], Any]],
    *,
    updated: dict[str, Any],
    profile: dict[str, Any],
    entry: dict[str, Any],
    status_before: Any,
    municipality: str,
    kind: str,
    adapter: Any,
    now: str,
    pending_evidence: list[dict[str, Any]],
    report_base: dict[str, Any],
    probe_subject: str,
    ok_reason: str = "ok",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the R2 extraction probe and finish the entry per R1-R5.

    - robots denial on the probe -> blocked (R3)
    - any other probe error -> failed, status untouched (R5)
    - probe INCONCLUSIVE (adapter could not read the document) -> failed,
      status untouched, reason names the adapter status (R5)
    - EXTRACTED with 0 records -> needs_review, never ready (R4)
    - EXTRACTED with >=1 record -> evidence + stamps; promote only per R1
    """
    probe_status, probe_payload = _run_extraction_probe(probe)
    if probe_status == "robots":
        exc = probe_payload
        _append_pending_evidence(entry, pending_evidence)
        entry["verified_at"] = now
        entry["verified_by"] = "verify --live"
        entry["status"] = "blocked"
        _append_note(entry, _ROBOTS_BLOCKED_NOTE, marker="robots")
        errs = validate_profile(updated)
        if errs:
            report = {
                **report_base,
                "result": "failed",
                "reason": f"post-verify validation failed: {errs}",
                "status_after": status_before,
            }
            return copy.deepcopy(profile), report
        report = {
            **report_base,
            "result": "blocked",
            "reason": f"RobotsDeniedError: {exc} (bodies are robots-restricted)",
            "status_after": "blocked",
        }
        return updated, report
    if probe_status == "error":
        exc = probe_payload
        report = {
            **report_base,
            "result": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "status_after": status_before,
        }
        return updated, report
    records, adapter_status = probe_payload
    if _classify_probe_status(adapter_status) == "inconclusive":
        # C3: name the adapter status so a human can tell "install poppler
        # and re-run" apart from "this site does not publish minutes".
        report = {
            **report_base,
            "result": "failed",
            "reason": (
                "probe_inconclusive: the adapter could not read the probed "
                f"document (adapter status {adapter_status!r}); fix the local "
                "extraction tooling and re-run"
            ),
            "status_after": status_before,
        }
        return updated, report
    if not _identifiable_records(kind, records):
        # R4: the document was READ, but nothing structurally identifiable
        # was found (fallback paragraph/document chunks do not count).
        what = (
            "numbered articles"
            if kind == "regulations"
            else "speaker-attributed speeches"
        )
        entry["status"] = "needs_review"
        _append_note(
            entry,
            f"extraction probe read one document at {probe_subject} but found "
            f"no {what}; reachability alone is not ingest evidence",
            marker=f"no {what}",
        )
        errs = validate_profile(updated)
        if errs:
            report = {
                **report_base,
                "result": "failed",
                "reason": f"post-verify validation failed: {errs}",
                "status_after": status_before,
            }
            return copy.deepcopy(profile), report
        report = {
            **report_base,
            "result": "needs_review",
            "reason": (
                f"probe_found_no_identifiable_records: probed {probe_subject}; "
                f"document was read but yielded no {what}"
            ),
            "status_after": "needs_review",
        }
        return updated, report
    # Probe succeeded: stamp evidence, then apply the R1 promotion gate.
    _append_pending_evidence(entry, pending_evidence)
    entry["verified_at"] = now
    entry["verified_by"] = "verify --live"
    promoted = status_before in _PROMOTABLE_PRIOR_STATUSES
    if promoted:
        entry["status"] = "ready"
    errs = validate_profile(updated)
    if errs:
        report = {
            **report_base,
            "result": "failed",
            "reason": f"post-verify validation failed: {errs}",
            "status_after": status_before,
        }
        return copy.deepcopy(profile), report
    reason = (
        ok_reason
        if promoted
        else (
            f"promotion withheld: prior status {status_before!r}; only "
            "needs_review/not_evaluated can be promoted to ready"
        )
    )
    report = {
        **report_base,
        "result": "verified",
        "reason": reason,
        "status_after": entry.get("status"),
    }
    return updated, report


def _probe_sort_key(ref: dict[str, Any]) -> int:
    title = str(ref.get("title") or "")
    if any(k in title for k in ("条例", "規則", "規程", "会則")):
        return 0
    return 1


def _probe_greiki_regulations(
    *, base_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract articles from g-reiki regulations via the real extractor."""
    refs = discover_documents_greiki(base_url, client=client, limit=30)
    if not refs:
        return [], "no_act_links"
    last_payload: dict[str, Any] | None = None
    sorted_refs = sorted(refs, key=_probe_sort_key)
    for ref in sorted_refs:
        payload = fetch_document_greiki(ref, base_url=base_url, client=client)
        last_payload = payload
        articles = payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        if any(a.get("article_no") for a in records):
            provenance = payload.get("provenance")
            status = provenance.get("status") if isinstance(provenance, dict) else None
            return records, status

    if last_payload:
        articles = last_payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        provenance = last_payload.get("provenance")
        status = provenance.get("status") if isinstance(provenance, dict) else None
        return records, status

    return [], "no_act_links"


def _probe_joureikun_regulations(
    *, index_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract articles from joureikun regulations via the real extractor."""
    refs = discover_documents_joureikun(index_url, client=client, limit=30)
    if not refs:
        return [], "no_act_links"
    last_payload: dict[str, Any] | None = None
    sorted_refs = sorted(refs, key=_probe_sort_key)
    for ref in sorted_refs:
        payload = fetch_document_joureikun(ref, index_url=index_url, client=client)
        last_payload = payload
        articles = payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        if any(a.get("article_no") for a in records):
            provenance = payload.get("provenance")
            status = provenance.get("status") if isinstance(provenance, dict) else None
            return records, status

    if last_payload:
        articles = last_payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        provenance = last_payload.get("provenance")
        status = provenance.get("status") if isinstance(provenance, dict) else None
        return records, status

    return [], "no_act_links"


def _probe_d1law_regulations(
    *, index_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract articles from D1-Law regulation via the real extractor."""
    refs = discover_documents_d1law(index_url, client=client, limit=30)
    if not refs:
        return [], "no_act_links"
    last_payload: dict[str, Any] | None = None
    sorted_refs = sorted(refs, key=_probe_sort_key)
    for ref in sorted_refs:
        payload = fetch_document_d1law(ref, index_url=index_url, client=client)
        last_payload = payload
        articles = payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        if any(a.get("article_no") for a in records):
            provenance = payload.get("provenance")
            status = provenance.get("status") if isinstance(provenance, dict) else None
            return records, status

    if last_payload:
        articles = last_payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        provenance = last_payload.get("provenance")
        status = provenance.get("status") if isinstance(provenance, dict) else None
        return records, status

    return [], "no_act_links"


def _probe_d1law_opensearch_regulations(
    *, index_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract articles from D1-Law OpenSearch regulations via the real extractor."""
    refs = discover_documents_d1law_opensearch(index_url, client=client, limit=30)
    if not refs:
        return [], "no_act_links"
    last_payload: dict[str, Any] | None = None
    sorted_refs = sorted(refs, key=_probe_sort_key)
    for ref in sorted_refs:
        payload = fetch_document_d1law_opensearch(ref, index_url=index_url, client=client)
        last_payload = payload
        articles = payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        if any(a.get("article_no") for a in records):
            provenance = payload.get("provenance")
            status = provenance.get("status") if isinstance(provenance, dict) else None
            return records, status

    if last_payload:
        articles = last_payload.get("articles")
        records = (
            [a for a in articles if isinstance(a, dict)]
            if isinstance(articles, list)
            else []
        )
        provenance = last_payload.get("provenance")
        status = provenance.get("status") if isinstance(provenance, dict) else None
        return records, status

    return [], "no_act_links"


def _probe_static_minutes(
    *,
    config: Any,
    index_url: str,
    document_url: str,
    client: Any,
) -> tuple[list[dict[str, Any]], Any]:
    """Extract speeches from ONE minutes document via the real static adapter."""
    cfg = dict(config) if isinstance(config, dict) else {}
    cfg.setdefault("index_url", [index_url])
    adapter_obj = StaticHtmlAdapter(cfg, client=client)
    payload = adapter_obj.fetch_meeting(
        {
            "source_url": document_url,
            "is_pdf": document_url.lower().split("?", 1)[0].endswith(".pdf"),
        }
    )
    speeches = payload.get("speeches")
    provenance = payload.get("provenance")
    status = provenance.get("status") if isinstance(provenance, dict) else None
    return (speeches if isinstance(speeches, list) else []), status


def _probe_kaigiroku_minutes(
    *, tenant_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract speeches from ONE kaigiroku meeting via the real adapter."""
    adapter_obj = KaigirokuNetAdapter(tenant_url, client=client)
    meetings = adapter_obj.list_meetings(limit=1)
    if not meetings:
        return [], None
    payload = adapter_obj.fetch_meeting(meetings[0])
    speeches = payload.get("speeches")
    provenance = payload.get("provenance")
    status = provenance.get("status") if isinstance(provenance, dict) else None
    return (speeches if isinstance(speeches, list) else []), status


def _probe_dbsr_minutes(
    *, index_url: str, meeting_url: str, client: Any
) -> tuple[list[dict[str, Any]], Any]:
    """Extract speeches from ONE dbsr meeting document via the real adapter."""
    payload = DbsrAdapter(index_url, client=client).fetch_meeting(
        {"source_url": meeting_url}
    )
    speeches = payload.get("speeches")
    provenance = payload.get("provenance")
    status = provenance.get("status") if isinstance(provenance, dict) else None
    return (speeches if isinstance(speeches, list) else []), status


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
    # Host drift check: the entry URL (index_url) is the authority we
    # fetched; comparing against official_home_url would fail every
    # legitimate vendor-CMS index (kaigiroku/g-reiki tenants) that lives on
    # a different host from the municipal home page.
    entry_host = _host(str(index_url))
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

    # Council-scoped minutes document link = (.pdf OR label/URL contains
    # 会議録/議事録/minutes) AND label/URL has council token AND not non-council
    # (rescued only under /gikai etc).
    def _council_doc_urls(
        links: list[tuple[str, str]], observed_url: str, page_context: str
    ) -> list[str]:
        urls: list[str] = []
        for href, label in links:
            resolved = canonical_url(
                urllib.parse.urljoin(str(observed_url), href),
                strict=False,
            )
            if resolved is None:
                continue
            if not _is_minutes_document_link(label=label, url=resolved):
                continue
            if _is_council_scope(
                label=label, url=resolved, observed_on=str(observed_url)
            ):
                urls.append(resolved)
                continue
            # Fallback: page context contains council token (e.g., headings 定例会)
            # Allows generic "1日目" PDFs under a council heading to count.
            if page_context and _is_council_scope(
                label=label,
                url=resolved,
                observed_on=str(observed_url),
                page_context=page_context,
            ):
                urls.append(resolved)
        return urls

    def _has_council_doc_on_page(
        links: list[tuple[str, str]], observed_url: str, page_context: str
    ) -> bool:
        return bool(_council_doc_urls(links, observed_url, page_context))

    def _first_council_doc_url(
        links: list[tuple[str, str]],
        observed_url: str,
        context: str,
        *,
        prefer_pdf: bool,
    ) -> str | None:
        urls = _council_doc_urls(links, observed_url, context)
        for url in urls:
            is_pdf = url.lower().split("?", 1)[0].endswith(".pdf")
            if is_pdf == prefer_pdf:
                return url
        return None


    prefer_pdf = (
        bool(entry["config"].get("pdf"))
        if isinstance(entry.get("config"), dict)
        else False
    )

    def _finalize_static(
        *,
        page_result: Any,
        page_final_url: str,
        page_links: list[tuple[str, str]],
        page_context: str,
        via_follow: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        doc_url = _first_council_doc_url(
            page_links, page_final_url, page_context, prefer_pdf=prefer_pdf
        )
        is_pdf_doc = bool(doc_url and doc_url.lower().split("?", 1)[0].endswith(".pdf"))
        raw_cfg = entry.get("config")
        cfg: dict[str, Any] = copy.deepcopy(raw_cfg) if isinstance(raw_cfg, dict) else {}
        if is_pdf_doc:
            cfg["pdf"] = True

        pending_sources = [(index_url, index_url, result)]
        if via_follow:
            pending_sources.append((page_final_url, index_url, page_result))
        pending_evidence = [
            _evidence_item(url, observed_on, res, now)
            for url, observed_on, res in pending_sources
        ]
        page_sha = page_result.sha256 if hasattr(page_result, "sha256") else ""
        page_fetched = (
            page_result.fetched_at if hasattr(page_result, "fetched_at") else now
        )
        report_base = {
            "municipality": municipality,
            "kind": "minutes",
            "adapter": adapter,
            "status_before": status_before,
            "index_url": index_url,
            "final_url": page_final_url,
            "sha256": page_sha,
            "fetched_at": page_fetched,
        }
        probe_subject = doc_url or "the first council-scoped document link"

        def _probe_and_record():
            records, status = _probe_static_minutes(
                config=cfg,
                index_url=index_url,
                document_url=doc_url or "",
                client=client,
            )
            if records and is_pdf_doc:
                entry.setdefault("config", {})["pdf"] = True
            return records, status

        return _finalize_with_probe(
            _probe_and_record if doc_url is not None else (lambda: ([], None)),
            updated=updated,
            profile=profile,
            entry=entry,
            status_before=status_before,
            municipality=municipality,
            kind="minutes",
            adapter=adapter,
            now=now,
            pending_evidence=pending_evidence,
            report_base=report_base,
            probe_subject=str(probe_subject),
            ok_reason="ok via follow" if via_follow else "ok",
        )

    page_context = parser.context()
    has_council_doc = _has_council_doc_on_page(
        parser.links, str(final_url_val), page_context
    )
    if has_council_doc:
        # Direct success on index (no follow needed): run the extraction probe.
        return _finalize_static(
            page_result=result,
            page_final_url=str(final_url_val),
            page_links=parser.links,
            page_context=page_context,
            via_follow=False,
        )

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
            resolved = canonical_url(
                urllib.parse.urljoin(str(base_url), href),
                strict=False,
            )
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
    success_follow_parser: _MinutesPageParser | None = None
    success_follow_context: str | None = None
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
            success_follow_parser = f_parser
            success_follow_context = f_context
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
    # Success via follow: run the extraction probe against a document on the
    # follow page; evidence covers root and follow page.
    follow_url_val = success_follow_final or initial[0]
    assert success_follow_parser is not None  # narrowing for mypy
    return _finalize_static(
        page_result=success_follow_result,
        page_final_url=follow_url_val,
        page_links=success_follow_parser.links,
        page_context=success_follow_context or "",
        via_follow=True,
    )


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
    # Entrance checks passed -> run the R2 extraction probe via the real
    # kaigiroku adapter (list one meeting, fetch its speeches).
    sha256, fetched_at = _fetch_meta(result, now)
    pending_evidence = [_evidence_item(tenant_url, tenant_url, result, now)]
    report_base = {
        "municipality": municipality,
        "kind": "minutes",
        "adapter": adapter,
        "status_before": status_before,
        "tenant_url": tenant_url,
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return _finalize_with_probe(
        lambda: _probe_kaigiroku_minutes(
            tenant_url=tenant_url,
            client=client,
        ),
        updated=updated,
        profile=profile,
        entry=entry,
        status_before=status_before,
        municipality=municipality,
        kind="minutes",
        adapter=adapter,
        now=now,
        pending_evidence=pending_evidence,
        report_base=report_base,
        probe_subject="the first meeting listed by the kaigiroku.net tenant API",
    )


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
        resolved = canonical_url(
            urllib.parse.urljoin(str(observed_url), href),
            strict=False,
        )
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
    # Robots-aware extraction probe (R2/R3): fetch the first meeting link
    # through the real dbsr adapter and require >=1 extracted speech.
    meeting_url = _first_dbsr_meeting_link(html_text, parser.links, str(final_url_val))
    sha256, fetched_at = _fetch_meta(result, now)
    pending_evidence = [_evidence_item(index_url, index_url, result, now)]
    report_base = {
        "municipality": municipality,
        "kind": "minutes",
        "adapter": adapter,
        "status_before": status_before,
        "index_url": index_url,
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
        "meeting_url": meeting_url,
    }
    return _finalize_with_probe(
        lambda: (
            _probe_dbsr_minutes(
                index_url=index_url,
                meeting_url=meeting_url,
                client=client,
            )
            if meeting_url is not None
            else ([], None)
        ),
        updated=updated,
        profile=profile,
        entry=entry,
        status_before=status_before,
        municipality=municipality,
        kind="minutes",
        adapter=adapter,
        now=now,
        pending_evidence=pending_evidence,
        report_base=report_base,
        probe_subject=meeting_url or "the first same-host meeting link",
    )


def _verify_joureikun_regulations(
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
    """Verify a joureikun regulations entry (index_url + act links)."""
    index_url = entry.get("index_url")
    if not isinstance(index_url, str) or not index_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": "missing index_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report
    try:
        result = client.fetch(index_url, tier=CacheTier.INDEX)
    except Exception as exc:  # noqa: BLE001
        report = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report

    final_url_val = result.final_url if hasattr(result, "final_url") else index_url  # type: ignore[attr-defined]
    entry_host = _host(str(index_url))
    final_host = _host(str(final_url_val))
    if not _is_allowed_host_drift(
        entry_host, final_host, adapter=adapter, kind="regulations"
    ):
        report = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: entry {entry_host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report

    if final_url_val != index_url:
        entry["index_url"] = str(final_url_val)

    sha256, fetched_at = _fetch_meta(result, now)
    pending_evidence = [_evidence_item(str(final_url_val), str(index_url), result, now)]
    report_base = {
        "municipality": municipality,
        "kind": "regulations",
        "adapter": adapter,
        "status_before": status_before,
        "entry_url": str(final_url_val),
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return _finalize_with_probe(
        lambda: _probe_joureikun_regulations(
            index_url=str(final_url_val), client=client
        ),
        updated=updated,
        profile=profile,
        entry=entry,
        status_before=status_before,
        municipality=municipality,
        kind="regulations",
        adapter=adapter,
        now=now,
        pending_evidence=pending_evidence,
        report_base=report_base,
        probe_subject="the first regulation document discovered from the joureikun catalog",
    )


def _verify_d1law_regulations(
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
    """Verify a D1-Law regulations entry (index_url + frames/act links)."""
    index_url = entry.get("index_url")
    if not isinstance(index_url, str) or not index_url.strip():
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": "missing index_url (cannot derive entry URL without guessing)",
            "status_before": status_before,
            "status_after": status_before,
        }
        return updated, report
    try:
        result = client.fetch(index_url, tier=CacheTier.INDEX)
    except Exception as exc:  # noqa: BLE001
        report = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
        }
        return updated, report

    final_url_val = result.final_url if hasattr(result, "final_url") else index_url  # type: ignore[attr-defined]
    entry_host = _host(str(index_url))
    final_host = _host(str(final_url_val))
    if not _is_allowed_host_drift(
        entry_host, final_host, adapter=adapter, kind="regulations"
    ):
        report = {
            "municipality": municipality,
            "kind": "regulations",
            "adapter": adapter,
            "result": "failed",
            "reason": f"host drift: entry {entry_host!r} -> final {final_host!r}",
            "status_before": status_before,
            "status_after": status_before,
            "index_url": index_url,
            "final_url": final_url_val,
        }
        return updated, report

    if final_url_val != index_url:
        entry["index_url"] = str(final_url_val)

    sha256, fetched_at = _fetch_meta(result, now)
    pending_evidence = [_evidence_item(str(final_url_val), str(index_url), result, now)]
    report_base = {
        "municipality": municipality,
        "kind": "regulations",
        "adapter": adapter,
        "status_before": status_before,
        "entry_url": str(final_url_val),
        "final_url": final_url_val,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    is_opensearch = "opensearch" in str(index_url).lower() or "jctcd=" in str(index_url).lower()
    probe_fn = (
        (lambda: _probe_d1law_opensearch_regulations(index_url=str(final_url_val), client=client))
        if is_opensearch
        else (lambda: _probe_d1law_regulations(index_url=str(final_url_val), client=client))
    )
    return _finalize_with_probe(
        probe_fn,
        updated=updated,
        profile=profile,
        entry=entry,
        status_before=status_before,
        municipality=municipality,
        kind="regulations",
        adapter=adapter,
        now=now,
        pending_evidence=pending_evidence,
        report_base=report_base,
        probe_subject="the first regulation document discovered from D1-Law index",
    )


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

    # budget / settlement: entry + real document + structural markers. Never
    # grants ready (no generic extractor exists; ready stays human-granted
    # after CSV ingestion) — see the boundary note above the verifier.
    if kind in ("budget", "settlement"):
        if adapter != "official_document_index":
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
        return _verify_budget_settlement(
            profile,
            updated,
            entry,
            client=client,
            now=now,
            municipality=municipality,
            status_before=status_before,
            adapter=adapter,
            kind=kind,
        )

    # Only g_reiki regulations is supported for verify (legacy)
    if kind == "regulations" and adapter == "joureikun":
        return _verify_joureikun_regulations(
            profile,
            updated,
            entry,
            client=client,
            now=now,
            municipality=municipality,
            status_before=status_before,
            adapter=adapter,
        )
    if kind == "regulations" and adapter == "d1_law":
        return _verify_d1law_regulations(
            profile,
            updated,
            entry,
            client=client,
            now=now,
            municipality=municipality,
            status_before=status_before,
            adapter=adapter,
        )
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
    if not _is_allowed_host_drift(entry_host, final_host, adapter=adapter, kind=kind):
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

    actual_base_url = base_url
    final_url_str = str(final_url_val)
    if entry_host != final_host and "reiki_menu.html" in final_url_str:
        actual_base_url = final_url_str.rsplit("reiki_menu.html", 1)[0]
        entry["base_url"] = actual_base_url

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

    # All index-level checks passed -> run the R2 extraction probe through
    # the real vendor_greiki extractor before any promotion is considered.
    sha256, fetched_at = _fetch_meta(result, now)
    final_url_report = result.final_url if hasattr(result, "final_url") else entry_url  # type: ignore[attr-defined]
    pending_evidence = [_evidence_item(str(final_url_report), base_url, result, now)]
    report_base = {
        "municipality": municipality,
        "kind": kind,
        "adapter": adapter,
        "status_before": status_before,
        "entry_url": entry_url,
        "final_url": final_url_report,
        "sha256": sha256,
        "fetched_at": fetched_at,
    }
    return _finalize_with_probe(
        lambda: _probe_greiki_regulations(base_url=actual_base_url, client=client),
        updated=updated,
        profile=profile,
        entry=entry,
        status_before=status_before,
        municipality=municipality,
        kind=kind,
        adapter=adapter,
        now=now,
        pending_evidence=pending_evidence,
        report_base=report_base,
        probe_subject=(
            "the first regulation document discovered from reiki_menu.html"
        ),
    )
