"""CLI for source profiles: validate and ingest-command."""

from __future__ import annotations  # noqa: I001

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from lcaios.html import LinkParser
from lcaios.http import (
    BOOTSTRAP_USER_AGENT,
    CacheTier,
    FetchResult,
    HttpClient,
    MINUTES_USER_AGENT,
    REGULATIONS_USER_AGENT,
)

from source_profiles.schema import (
    validate_profile,  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
)
from source_profiles.verify import (
    verify_profile,  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
)

PACKAGE_ROOT = Path(__file__).resolve().parent
MUNICIPALITIES_ROOT = PACKAGE_ROOT / "municipalities"


def _effective_municipalities_root(profiles_dir: str | None) -> Path:
    if profiles_dir:
        return Path(profiles_dir)
    env = os.environ.get("SOURCE_PROFILES_DIR") or os.environ.get(
        "SOURCE_PROFILES_MUNICIPALITIES_ROOT"
    )
    if env:
        return Path(env)
    return MUNICIPALITIES_ROOT


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFC", name).replace("\u3000", " ").strip()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load {path}: {exc}") from exc


def _find_profiles(
    prefecture: str | None = None, base_dir: str | Path | None = None
) -> list[Path]:
    root = _effective_municipalities_root(str(base_dir) if base_dir else None)
    if not root.exists():
        return []
    results: list[Path] = []
    # Support both structures: root is municipalities/ or root/41-saga/ or flat temp dir
    # Collect all *.json recursively, then filter by prefecture if needed
    for json_file in root.rglob("*.json"):
        # Skip non-profile json? Keep all, validate will filter
        if prefecture is not None:
            try:
                data = _load_json(json_file)
                if data.get("prefecture") != prefecture:
                    continue
            except Exception:
                # If cannot load, still include to report error
                pass
        results.append(json_file)
    results.sort()
    return results


def _cmd_validate(args: argparse.Namespace) -> int:
    profiles: list[Path] = []
    profiles_dir = getattr(args, "profiles_dir", None)
    if args.profile:
        p = Path(args.profile)
        if not p.exists():
            not_found_report: dict[str, Any] = {
                "status": "error",
                "errors": [f"profile not found: {p}"],
            }
            print(json.dumps(not_found_report, ensure_ascii=False, indent=2))
            return 2
        profiles = [p]
    elif args.all:
        profiles = _find_profiles(args.prefecture, base_dir=profiles_dir)
        if not profiles:
            # If --prefecture given and no profiles, report
            if args.prefecture:
                report_err: dict[str, Any] = {
                    "status": "error",
                    "errors": [f"no profiles found for prefecture {args.prefecture}"],
                    "profile_count": 0,
                }
                print(json.dumps(report_err, ensure_ascii=False, indent=2))
                return 2
    else:
        print(
            json.dumps(
                {"status": "error", "errors": ["specify --profile or --all"]},
                ensure_ascii=False,
            )
        )
        return 2

    results: list[dict[str, Any]] = []
    error_count = 0
    for path in profiles:
        try:
            data = _load_json(path)
        except Exception as exc:
            results.append(
                {
                    "profile": str(path),
                    "status": "error",
                    "errors": [f"failed to load JSON: {exc}"],
                }
            )
            error_count += 1
            continue
        errs = validate_profile(data)
        status = "ok" if not errs else "error"
        if errs:
            error_count += 1
        results.append({"profile": str(path), "status": status, "errors": errs})

    final_report: dict[str, Any] = {
        "status": "ok" if error_count == 0 else "error",
        "profile_count": len(profiles),
        "error_count": error_count,
        "results": results,
    }
    print(json.dumps(final_report, ensure_ascii=False, indent=2))
    return 0 if error_count == 0 else 2


def _resolve_profile_by_municipality(
    municipality: str,
    prefecture: str | None,
    base_dir: str | Path | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    norm_muni = _normalize(municipality)
    norm_pref = _normalize(prefecture) if prefecture else None
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in _find_profiles(None, base_dir=base_dir):
        try:
            data = _load_json(path)
        except Exception:
            continue
        if _normalize(str(data.get("municipality", ""))) != norm_muni:
            continue
        if (
            norm_pref is not None
            and _normalize(str(data.get("prefecture", ""))) != norm_pref
        ):
            continue
        candidates.append((path, data))
    if not candidates:
        return None
    # If multiple and prefecture not specified, require disambiguation
    if len(candidates) > 1 and prefecture is None:
        return None
    # Prefer exact prefecture match if provided
    if norm_pref is not None:
        # filter already done; if multiple with same name/pref, take first sorted
        candidates.sort(key=lambda x: str(x[0]))
        return candidates[0]
    candidates.sort(key=lambda x: str(x[0]))
    return candidates[0]


def _cmd_ingest_command(args: argparse.Namespace) -> int:
    kind: str = args.kind
    limit: int = args.limit
    municipality: str = args.municipality
    prefecture: str | None = args.prefecture
    profiles_dir = getattr(args, "profiles_dir", None)

    resolved = _resolve_profile_by_municipality(
        municipality, prefecture, base_dir=profiles_dir
    )
    if resolved is None:
        # Try to provide helpful error
        print(
            f"municipality not found: {municipality!r} prefecture={prefecture!r}",
            file=sys.stderr,
        )
        return 2
    path, data = resolved
    sources = data.get("sources", {})
    entry = sources.get(kind)
    if entry is None:
        print(f"kind {kind!r} not found in profile {path}", file=sys.stderr)
        return 2

    status = entry.get("status")
    adapter = entry.get("adapter")
    area = data.get("area_code_5", "unknown")
    muni_name = data.get("municipality", municipality)

    # Supported case: g_reiki with ready/needs_review
    if adapter == "g_reiki" and status in {"ready", "needs_review"}:
        base_url = entry.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            print(f"profile {path} g_reiki missing base_url", file=sys.stderr)
            return 2
        # Ensure base_url ends with /
        if not base_url.endswith("/"):
            base_url = base_url + "/"
        db_path = f"/tmp/{area}-reg.db"
        source_name = f"{muni_name}例規集"
        cmd = f'python3 modules/regulations/vendor_greiki.py --base-url {base_url} --db {db_path} --source-name "{source_name}" --limit {limit}'
        if status == "needs_review":
            print("# NEEDS LIVE VERIFICATION")
        print(cmd)
        return 0

    # Unsupported path
    reason_parts: list[str] = []
    if adapter is None:
        reason_parts.append("adapter is null (no supported ingestion method)")
    elif adapter in {"d1_law", "joureikun", "dbsr", "voices"}:
        reason_parts.append(f"adapter {adapter} is not supported by vendor_greiki")
    else:
        reason_parts.append(
            f"adapter {adapter!r} with status {status!r} cannot be ingested via g_reiki"
        )

    reason = "; ".join(reason_parts)
    # Next action guidance
    if adapter in {"d1_law", "joureikun"}:
        action = "requires a dedicated adapter (not yet implemented); manual collection or new vendor module is needed"
    elif adapter is None and status == "needs_review":
        action = "requires manual browser verification; run `python3 -m source_profiles.cli validate --profile {}` then verify entry URL".format(
            path
        )
    elif status == "not_evaluated":
        action = "profile not evaluated; verify and set status/adapter first"
    else:
        action = "check profile status/adapter and update verified_at/evidence before ingestion"

    print(f"cannot generate ingest command: {reason}", file=sys.stderr)
    print(f"next action: {action}", file=sys.stderr)
    print(
        f"profile: {path} kind={kind} status={status} adapter={adapter}",
        file=sys.stderr,
    )
    return 2


def _cmd_verify(args: argparse.Namespace) -> int:
    kind: str = args.kind
    municipality: str = args.municipality
    prefecture: str | None = args.prefecture
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    offline: bool = bool(args.offline)
    profiles_dir = getattr(args, "profiles_dir", None)

    resolved = _resolve_profile_by_municipality(
        municipality, prefecture, base_dir=profiles_dir
    )
    if resolved is None:
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": kind,
            "adapter": None,
            "result": "failed",
            "reason": f"municipality not found: {municipality!r} prefecture={prefecture!r}",
            "status_before": None,
            "status_after": None,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    path, data = resolved
    sources = data.get("sources", {})
    entry = sources.get(kind) if isinstance(sources, dict) else None
    adapter = entry.get("adapter") if isinstance(entry, dict) else None
    status_before = entry.get("status") if isinstance(entry, dict) else None

    # Delegate unsupported check to verify_profile, but keep early exit for unknown entry
    # Supported: g_reiki/regulations, static|kaigiroku_net|dbsr/minutes,
    # official_document_index/{budget,settlement}. Others are handled by
    # verify_profile which returns failed with proper reason and exit 2.
    # We still require cache_dir before calling verify_profile.
    if cache_dir is None:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": "--cache-dir is required",
            "status_before": status_before,
            "status_after": status_before,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        client = HttpClient(
            cache_dir,
            user_agent=_user_agent_for_kind(kind),
            offline=offline,
            timeout=90,
        )
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"HttpClient init failed: {exc}",
            "status_before": status_before,
            "status_after": status_before,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    now = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    updated, v_report = verify_profile(data, client=client, now=now, kind=kind)

    # Persist any verdict that carries a recorded status (verified/blocked/
    # needs_review). needs_review must reach disk too: a R4 "read but no
    # identifiable records" or a budget/settlement structure verdict must
    # replace a stale preflight-derived status instead of silently leaving it
    # in place (有田 lesson). "failed" carries no new status and is not saved.
    if v_report.get("result") in ("verified", "blocked", "needs_review"):
        try:
            # Write atomically via temp file
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(path)
        except Exception as exc:
            err_report = {
                "municipality": municipality,
                "kind": kind,
                "adapter": adapter,
                "result": "failed",
                "reason": f"failed to save profile: {exc}",
                "status_before": status_before,
                "status_after": status_before,
            }
            print(json.dumps(err_report, ensure_ascii=False, indent=2))
            return 2

    print(json.dumps(v_report, ensure_ascii=False, indent=2))
    return 0 if v_report.get("result") in ("verified", "blocked") else 2


DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".csv",
)


_YEAR_LABEL_RE = re.compile(r"(令和|平成|昭和)(元|[0-9]+)")
_CONTENT_LABEL_RE = re.compile(r"(予算|決算|概要|報告|資料)")
_PRIORITY_LABEL_RE = re.compile(r"(当初|概要|決算報告)")


def _rank_content_candidates(
    candidates: list[dict[str, str]], exclude_urls: set[str]
) -> list[dict[str, str]]:
    """Most specific first: year+content labels > content-only labels.

    Already-visited URLs (hub, index) are dropped so navigation links
    pointing back up the tree never win.
    """

    def score(item: dict[str, str]) -> tuple[int, int]:
        label = item["label"]
        has_year = bool(_YEAR_LABEL_RE.search(label))
        priority = bool(_PRIORITY_LABEL_RE.search(label))
        # year+priority (当初/概要/決算報告) beats year-only beats rest
        base = int(has_year) * 2 + int(priority)
        return (base, len(label))

    ranked = [c for c in candidates if c["url"] not in exclude_urls]
    ranked.sort(key=score, reverse=True)
    return ranked


def _content_subpage_links(html: str, page_url: str) -> list[dict[str, str]]:
    """Same-host HTML links whose label matches content keywords."""
    parser = LinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    page_host = urlparse(page_url).netloc
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, raw_label in parser.links:
        label = raw_label[:120] or href[:120]
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != page_host:
            continue
        if absolute in seen:
            continue
        path_lower = parsed.path.lower()
        if path_lower.endswith(DOCUMENT_EXTENSIONS):
            continue
        if not _CONTENT_LABEL_RE.search(label):
            continue
        seen.add(absolute)
        candidates.append({"label": label, "url": absolute})
    return candidates


def _entry_url_for_resolve(entry: dict[str, Any]) -> str | None:
    adapter = entry.get("adapter")
    index_url = entry.get("index_url")
    base_url = entry.get("base_url")
    tenant_url = entry.get("tenant_url")
    if index_url:
        return str(index_url)
    if adapter == "g_reiki" and base_url:
        return str(base_url).rstrip("/") + "/reiki_menu.html"
    if adapter == "kaigiroku_net" and tenant_url:
        return str(tenant_url)
    return None


def _extract_document_links(
    html: str, page_url: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (document links, year-labelled sub-page links), same-host only."""
    parser = LinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass  # malformed HTML: keep whatever links were collected
    page_host = urlparse(page_url).netloc
    seen: set[str] = set()
    documents: list[dict[str, str]] = []
    year_pages: list[dict[str, str]] = []
    for href, raw_label in parser.links:
        label = raw_label[:120] or href[:120]
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != page_host:
            continue  # same-host policy: no cross-site document listing
        if absolute in seen:
            continue
        path_lower = parsed.path.lower()
        if path_lower.endswith(DOCUMENT_EXTENSIONS):
            seen.add(absolute)
            documents.append({"label": label, "url": absolute})
        elif _YEAR_LABEL_RE.search(label):
            seen.add(absolute)
            year_pages.append({"label": label, "url": absolute})
    return documents, year_pages


def _user_agent_for_kind(kind: str) -> str:
    if kind == "minutes":
        return MINUTES_USER_AGENT
    if kind in ("budget", "settlement"):
        return BOOTSTRAP_USER_AGENT
    return REGULATIONS_USER_AGENT


def _cmd_resolve(
    args: argparse.Namespace,
    client_factory: Any = None,
) -> int:
    kind: str = args.kind
    municipality: str = args.municipality
    prefecture: str | None = args.prefecture
    profiles_dir = getattr(args, "profiles_dir", None)
    offline = bool(args.offline)
    get_index: int | None = getattr(args, "get", None)

    resolved = _resolve_profile_by_municipality(
        municipality, prefecture, base_dir=profiles_dir
    )
    if resolved is None:
        print(
            json.dumps(
                {
                    "municipality": municipality,
                    "kind": kind,
                    "result": "failed",
                    "reason": f"municipality not found: {municipality!r} prefecture={prefecture!r}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    _path, data = resolved
    sources = data.get("sources", {})
    entry = sources.get(kind) if isinstance(sources, dict) else None
    if not isinstance(entry, dict):
        print(
            json.dumps(
                {
                    "municipality": municipality,
                    "kind": kind,
                    "result": "failed",
                    "reason": f"source kind {kind!r} missing in profile",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    status = entry.get("status")
    adapter = entry.get("adapter")
    entry_url = _entry_url_for_resolve(entry)
    warnings: list[str] = []
    if entry_url is None:
        print(
            json.dumps(
                {
                    "municipality": municipality,
                    "kind": kind,
                    "result": "failed",
                    "reason": f"no resolvable entry URL (adapter={adapter!r}, status={status!r})",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if status != "ready":
        warnings.append(
            f"profile status is {status!r} (not ready); result may be stale or restricted"
        )
    cache_dir = Path(args.cache_dir)
    if client_factory is None:
        client_factory = lambda: HttpClient(  # noqa: E731
            cache_dir,
            user_agent=_user_agent_for_kind(kind),
            offline=offline,
        )
    client = client_factory()
    try:
        result: FetchResult = client.fetch(entry_url, tier=CacheTier.INDEX)
    except Exception as exc:
        reason = type(exc).__name__
        detail = str(exc)
        if "RobotsDenied" in reason:
            reason = "robots_denied"
        print(
            json.dumps(
                {
                    "municipality": municipality,
                    "kind": kind,
                    "result": "failed",
                    "reason": f"fetch failed: {reason}: {detail}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    html_text = result.body.decode(result.encoding or "utf-8", errors="replace")
    documents, year_pages = _extract_document_links(html_text, result.final_url)
    followed_pages: list[dict[str, str]] = []
    if not documents and year_pages:
        # Year-index hub: documents live deeper. Follow up to --follow-pages
        # total same-host pages (depth capped at 2): year pages first; a year
        # page yielding zero documents may have exactly ONE content sub-page
        # followed (label matches content keywords).
        budget = max(0, int(getattr(args, "follow_pages", 8)))
        seen_doc_urls: set[str] = set()

        def _fetch_page(url: str) -> FetchResult | None:
            nonlocal budget
            if budget <= 0:
                return None
            budget -= 1
            try:
                fetched: FetchResult = client.fetch(url, tier=CacheTier.INDEX)
            except Exception:
                return None  # one broken page must not kill the run
            if urlparse(fetched.final_url).netloc != urlparse(entry_url).netloc:
                return None
            return fetched

        for page in year_pages:
            if budget <= 0:
                break
            fetched = _fetch_page(page["url"])
            if fetched is None:
                continue
            page_html = fetched.body.decode(
                fetched.encoding or "utf-8", errors="replace"
            )
            followed_pages.append({"label": page["label"], "url": page["url"]})
            page_docs, _ = _extract_document_links(page_html, fetched.final_url)
            added = 0
            for doc in page_docs:
                if doc["url"] in seen_doc_urls:
                    continue
                seen_doc_urls.add(doc["url"])
                documents.append(
                    {
                        "label": f"[{page['label']}] {doc['label']}",
                        "url": doc["url"],
                        "observed_on": fetched.final_url,
                    }
                )
                added += 1
            if added == 0:
                # One content sub-page fallback, ranked by label specificity;
                # already-visited URLs (hub/index) are excluded.
                sub_candidates = _rank_content_candidates(
                    _content_subpage_links(page_html, fetched.final_url),
                    exclude_urls=seen_doc_urls
                    | {entry_url, page["url"], result.final_url},
                )
                if sub_candidates:
                    sub_fetched = _fetch_page(sub_candidates[0]["url"])
                    if sub_fetched is not None:
                        sub_html = sub_fetched.body.decode(
                            sub_fetched.encoding or "utf-8", errors="replace"
                        )
                        followed_pages.append(
                            {
                                "label": f"[{page['label']}] {sub_candidates[0]['label']}",
                                "url": sub_fetched.final_url,
                            }
                        )
                        sub_docs, _ = _extract_document_links(
                            sub_html, sub_fetched.final_url
                        )
                        for doc in sub_docs:
                            if doc["url"] in seen_doc_urls:
                                continue
                            seen_doc_urls.add(doc["url"])
                            documents.append(
                                {
                                    "label": (
                                        f"[{page['label']}] {sub_candidates[0]['label']}"
                                        f" {doc['label']}"
                                    ),
                                    "url": doc["url"],
                                    "observed_on": sub_fetched.final_url,
                                }
                            )
    report: dict[str, Any] = {
        "municipality": municipality,
        "kind": kind,
        "status": status,
        "adapter": adapter,
        "result": "ok",
        "index_url": entry_url,
        "final_url": result.final_url,
        "fetched_at": result.fetched_at,
        "sha256": result.sha256,
        "from_cache": result.from_cache,
        "document_count": len(documents),
        "documents": documents,
        "cache_dir": str(cache_dir),
    }
    if followed_pages:
        report["followed_pages"] = followed_pages
    if warnings:
        report["warnings"] = warnings

    if get_index is not None:
        if get_index < 1 or get_index > len(documents):
            report["result"] = "failed"
            report["reason"] = (
                f"--get {get_index} out of range (1..{len(documents)})"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        doc = documents[get_index - 1]
        try:
            doc_result = client.fetch(doc["url"], tier=CacheTier.DOCUMENT)
        except Exception as exc:
            reason = type(exc).__name__
            if "RobotsDenied" in reason:
                reason = "robots_denied"
            report["result"] = "failed"
            report["reason"] = (
                f"document fetch failed: {reason}: {exc} ({doc['url']})"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        report["fetched_document"] = {
            "label": doc["label"],
            "url": doc_result.url,
            "local_path": str(doc_result.cache_path),
            "sha256": doc_result.sha256,
            "fetched_at": doc_result.fetched_at,
            "from_cache": doc_result.from_cache,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="source_profiles.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate source profiles")
    g = p_validate.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", help="Path to a single profile JSON")
    g.add_argument("--all", action="store_true", help="Validate all profiles")
    p_validate.add_argument("--prefecture", help="Filter by prefecture (with --all)")
    p_validate.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    p_ingest = sub.add_parser(
        "ingest-command", help="Generate ingestion command for a municipality"
    )
    p_ingest.add_argument(
        "--municipality", required=True, help="Municipality name (e.g. 太良町)"
    )
    p_ingest.add_argument("--prefecture", help="Prefecture name (e.g. 佐賀県)")
    p_ingest.add_argument(
        "--kind",
        required=True,
        choices=["regulations", "minutes", "budget", "settlement"],
        help="Source kind",
    )
    p_ingest.add_argument(
        "--limit", type=int, default=3, help="Limit for ingestion (default 3)"
    )
    p_ingest.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    p_verify = sub.add_parser(
        "verify", help="Verify a source entry live via HttpClient"
    )
    p_verify.add_argument(
        "--municipality", required=True, help="Municipality name (e.g. 太良町)"
    )
    p_verify.add_argument("--prefecture", help="Prefecture name (e.g. 佐賀県)")
    p_verify.add_argument(
        "--kind",
        required=True,
        choices=["regulations", "minutes", "budget", "settlement"],
        help="Source kind",
    )
    p_verify.add_argument(
        "--cache-dir", required=True, help="Cache directory for HttpClient"
    )
    p_verify.add_argument(
        "--offline", action="store_true", help="Use cached responses only"
    )
    p_verify.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    p_resolve = sub.add_parser(
        "resolve",
        help="Resolve a source entry to its index page and document links",
    )
    p_resolve.add_argument(
        "--municipality", required=True, help="Municipality name (e.g. 伊万里市)"
    )
    p_resolve.add_argument("--prefecture", help="Prefecture name (e.g. 佐賀県)")
    p_resolve.add_argument(
        "--kind",
        required=True,
        choices=["regulations", "minutes", "budget", "settlement"],
        help="Source kind",
    )
    p_resolve.add_argument(
        "--cache-dir", required=True, help="Cache directory for HttpClient"
    )
    p_resolve.add_argument(
        "--offline", action="store_true", help="Use cached responses only"
    )
    p_resolve.add_argument(
        "--get",
        type=int,
        metavar="N",
        help="Also fetch the N-th document (1-based) from the listing",
    )
    p_resolve.add_argument(
        "--follow-pages",
        type=int,
        default=8,
        metavar="N",
        help=(
            "When the index has no documents but year-labelled sub-pages, "
            "follow at most N of them (depth 1, default 8)"
        ),
    )
    p_resolve.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "ingest-command":
        # validate limit
        if args.limit < 1:
            print("--limit must be at least 1", file=sys.stderr)
            return 2
        return _cmd_ingest_command(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "resolve":
        return _cmd_resolve(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
