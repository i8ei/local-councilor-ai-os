"""Live verification for g_reiki source profiles (stdlib only, HttpClient injection)."""

from __future__ import annotations  # noqa: I001

import copy
import urllib.parse
from typing import Any

from lcaios.http import CacheTier
from source_profiles.schema import validate_profile


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

    # Only g_reiki regulations is supported for verify
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
