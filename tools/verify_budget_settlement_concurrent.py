#!/usr/bin/env python3
"""Concurrently verify unpromoted budget and settlement source profiles across Japan.

Level 2 expansion: probes finance landing pages, discovers and probes actual general
account budget/settlement books (PDF/Excel), verifies structural financial markers,
and promotes validated profiles to `ready` with SHA256 evidence.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from bootstrap.cli.xlsx import read_workbook
from lcaios.html import LinkParser
from lcaios.http import (
    REGULATIONS_USER_AGENT,
    CacheTier,
    FetchResult,
    HttpClient,
    RobotsDeniedError,
)
from source_profiles.schema import validate_profile

MUNI_DIR = REPO_ROOT / "source_profiles" / "municipalities"
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"

DOC_EXTENSIONS = (".pdf", ".xlsx", ".xls")

BUDGET_MARKERS = [
    "歳入",
    "歳出",
    "款",
    "項",
    "節",
    "予算現額",
    "予算額",
    "予算総額",
    "当初予算",
    "一般会計",
]

SETTLEMENT_MARKERS = [
    "歳入",
    "歳出",
    "款",
    "項",
    "節",
    "決算額",
    "決算総額",
    "予算現額",
    "収入済額",
    "支出済額",
    "不用額",
    "実質収支",
    "決算カード",
    "財政状況資料集",
    "地方債",
    "基準財政需要額",
    "標準財政規模",
]

NEGATIVE_SUBPAGE_TOKENS = (
    "パブリックコメント",
    "パブコメ",
    "議事録",
    "会議録",
    "審議会",
    "公募",
    "広告",
    "入札",
    "契約",
    "選挙",
    "採用",
    "議会だより",
    "アンケート",
    "要綱",
    "条例",
    "例規",
)

_ROBOTS_BLOCKED_NOTE = (
    "document bodies reached through this entry are robots-restricted "
    "(robots.txt disallows document paths); ingestion requires the "
    "councilor/user to obtain municipality permission "
    "(out of scope for automated ingestion)"
)


def _score_subpage(label: str, url: str, kind: str) -> int:
    l_low = label.lower()
    u_low = url.lower()

    if any(tok in l_low for tok in NEGATIVE_SUBPAGE_TOKENS):
        return 0

    score = 0
    if kind == "budget":
        if any(k in label for k in ("当初予算書", "一般会計予算書", "予算書", "当初予算概要", "予算概要")):
            score = 35
        elif label.strip() in ("予算", "当初予算", "市の予算", "各会計当初予算", "予算の概要"):
            score = 30
        elif "当初予算" in label:
            score = 25
        elif "予算" in label:
            score = 20
        elif "財政状況資料集" in label:
            score = 15
        elif any(k in label for k in ("財政状況", "財政公表", "財政の状況", "財政事情", "財政資料")):
            score = 12
        elif "財政" in label:
            score = 6
        elif "/yosan" in u_low or "yosan" in u_low:
            score = 10
        elif "/zaisei" in u_low:
            score = 5
    else:
        if any(k in label for k in ("決算書", "一般会計決算書", "決算カード", "決算概要", "財政状況資料集")):
            score = 35
        elif label.strip() in ("決算", "市の決算", "各会計決算", "決算の概要"):
            score = 30
        elif "決算カード" in label:
            score = 28
        elif "決算" in label:
            score = 20
        elif "財政状況資料集" in label:
            score = 18
        elif any(k in label for k in ("財政状況", "財政公表", "財政の状況", "財政事情", "財政資料")):
            score = 12
        elif "財政" in label:
            score = 6
        elif "/kessan" in u_low or "kessan" in u_low:
            score = 10
        elif "/zaisei" in u_low:
            score = 5

    return score


def _score_doc(label: str, url: str, kind: str) -> int:
    l_low = label.lower()
    u_low = urllib.parse.unquote(url).lower()

    score = 0
    if kind == "budget":
        if any(k in label or k in u_low for k in ("当初予算書", "一般会計予算書", "予算書")):
            score = 50
        elif any(k in label or k in u_low for k in ("当初予算概要", "予算概要", "予算の概要", "予算説明書", "予算のあらまし")):
            score = 40
        elif any(k in label or k in u_low for k in ("当初予算", "予算案")):
            score = 30
        elif any(k in label or k in u_low for k in ("財政状況資料集",)):
            score = 25
        elif "予算" in label or "yosan" in u_low:
            score = 20
        elif "財政" in label or "zaisei" in u_low:
            score = 8
        else:
            score = 2
    else:
        if any(k in label or k in u_low for k in ("決算書", "一般会計決算書")):
            score = 50
        elif any(k in label or k in u_low for k in ("決算カード",)):
            score = 45
        elif any(k in label or k in u_low for k in ("決算概要", "決算の概要", "決算説明書")):
            score = 40
        elif any(k in label or k in u_low for k in ("財政状況資料集", "主要な施策の成果", "主要施策")):
            score = 35
        elif "決算" in label or "kessan" in u_low:
            score = 20
        elif "財政" in label or "zaisei" in u_low:
            score = 8
        else:
            score = 2

    return score


def _probe_doc(client: HttpClient, doc_url: str) -> tuple[str, FetchResult | None]:
    """Fetch and extract text from a PDF, XLSX, or HTML financial document."""
    # Let RobotsDeniedError raise directly so caller can classify as blocked
    try:
        res = client.fetch(doc_url, tier=CacheTier.DOCUMENT)
    except RobotsDeniedError:
        raise
    except Exception:
        return "", None

    path_low = doc_url.lower().split("?")[0]
    media_type = str(getattr(res, "content_type", "")).lower()

    if path_low.endswith(".pdf") or "pdf" in media_type:
        tool = shutil.which("pdftotext")
        if not tool:
            return "", res
        cmd = [tool, "-layout", "-l", "30", str(res.cache_path), "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
            return proc.stdout.decode("utf-8", errors="replace"), res
        except Exception:
            return "", res

    if path_low.endswith(".xlsx"):
        try:
            wb = read_workbook(res.cache_path)
            t = " ".join(c.value for ws in wb for c in ws.cells)
            return t, res
        except Exception:
            return "", res

    if "html" in media_type or path_low.endswith((".html", ".htm")):
        try:
            return res.text(), res
        except Exception:
            return "", res

    return "", res


def _extract_and_verify_doc(
    index_url: str,
    kind: str,
    client: HttpClient,
    max_depth: int = 2,
) -> dict[str, Any] | None:
    """Explore index and subpages to find and verify a budget/settlement document."""
    markers = BUDGET_MARKERS if kind == "budget" else SETTLEMENT_MARKERS

    # Fetch root index URL (let RobotsDeniedError raise if denied)
    idx_res = client.fetch(index_url, tier=CacheTier.INDEX)
    start_host = urllib.parse.urlsplit(idx_res.final_url).netloc.lower()

    # Direct document check if index_url is already a PDF/XLSX
    idx_path_low = urllib.parse.urlsplit(idx_res.final_url).path.lower()
    if any(idx_path_low.endswith(ext) for ext in DOC_EXTENSIONS):
        text, d_res = _probe_doc(client, idx_res.final_url)
        no_space = re.sub(r"\s+", "", text)
        found_m = [m for m in markers if m in no_space]
        if len(found_m) >= 2 and d_res is not None:
            return {
                "doc_url": idx_res.final_url,
                "doc_label": "Direct Document",
                "observed_on": idx_res.final_url,
                "idx_sha256": idx_res.sha256,
                "idx_fetched_at": idx_res.fetched_at,
                "doc_sha256": d_res.sha256,
                "doc_fetched_at": d_res.fetched_at,
                "markers": found_m,
            }

    queue = [(0, idx_res.final_url, idx_res)]
    visited = {idx_res.final_url}
    doc_cands: list[tuple[int, str, str, str]] = []

    while queue:
        depth, cur_url, cur_res = queue.pop(0)
        parser = LinkParser()
        try:
            parser.feed(cur_res.text())
        except Exception:
            continue

        sub_cands: list[tuple[int, str, str]] = []
        for href, label in parser.links:
            resolved = urllib.parse.urljoin(cur_res.final_url, href)
            parts = urllib.parse.urlsplit(resolved)
            if parts.scheme not in ("http", "https"):
                continue

            h_low = parts.netloc.lower()
            if (
                h_low != start_host
                and not h_low.endswith("." + start_host)
                and not start_host.endswith("." + h_low)
            ):
                continue

            clean_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
            path_low = parts.path.lower()

            if any(path_low.endswith(ext) for ext in DOC_EXTENSIONS):
                score = _score_doc(label, resolved, kind)
                doc_cands.append((score - depth * 2, resolved, label, cur_url))
            elif depth < max_depth:
                score = _score_subpage(label, resolved, kind)
                if score > 0:
                    sub_cands.append((score, clean_url, label))

        # Sort and probe top document candidates immediately
        doc_cands.sort(key=lambda x: x[0], reverse=True)
        for _, doc_url, d_label, obs_on in doc_cands[:5]:
            text, d_res = _probe_doc(client, doc_url)
            no_space = re.sub(r"\s+", "", text)
            found_m = [m for m in markers if m in no_space]
            if len(found_m) >= 2 and d_res is not None:
                return {
                    "doc_url": doc_url,
                    "doc_label": d_label,
                    "observed_on": obs_on,
                    "idx_sha256": idx_res.sha256,
                    "idx_fetched_at": idx_res.fetched_at,
                    "doc_sha256": d_res.sha256,
                    "doc_fetched_at": d_res.fetched_at,
                    "markers": found_m,
                }

        # Add top subpage candidates to queue (up to 5 per page)
        sub_cands.sort(key=lambda x: x[0], reverse=True)
        added = 0
        for _, sub_url, _ in sub_cands:
            if sub_url not in visited:
                visited.add(sub_url)
                try:
                    s_res = client.fetch(sub_url, tier=CacheTier.INDEX)
                    queue.append((depth + 1, sub_url, s_res))
                    added += 1
                    if added >= 5:
                        break
                except Exception:
                    continue

    # Final pass on any remaining document candidates
    doc_cands.sort(key=lambda x: x[0], reverse=True)
    for _, doc_url, d_label, obs_on in doc_cands[:10]:
        text, d_res = _probe_doc(client, doc_url)
        no_space = re.sub(r"\s+", "", text)
        found_m = [m for m in markers if m in no_space]
        if len(found_m) >= 2 and d_res is not None:
            return {
                "doc_url": doc_url,
                "doc_label": d_label,
                "observed_on": obs_on,
                "idx_sha256": idx_res.sha256,
                "idx_fetched_at": idx_res.fetched_at,
                "doc_sha256": d_res.sha256,
                "doc_fetched_at": d_res.fetched_at,
                "markers": found_m,
            }

    return None


def verify_municipality_kind(
    path: Path,
    profile: dict[str, Any],
    kind: str,
    client: HttpClient,
    now: str,
    dry_run: bool = False,
) -> tuple[str, bool, dict[str, Any] | None]:
    """Verify one source kind (budget or settlement) for a municipality."""
    muni_name = str(profile.get("municipality") or path.stem)
    src = profile.setdefault("sources", {}).setdefault(kind, {})

    if src.get("status") == "ready":
        return f"{muni_name} {kind} -> SKIP (already ready)", False, None

    index_url = src.get("index_url") or src.get("base_url") or ""
    if not index_url:
        return f"{muni_name} {kind} -> SKIP (no index_url)", False, None

    try:
        ver_res = _extract_and_verify_doc(index_url, kind, client)
    except RobotsDeniedError as exc:
        src["status"] = "blocked"
        src["verified_at"] = now
        src["verified_by"] = "verify --live"
        notes = src.get("notes") or ""
        if "robots" not in notes.lower():
            src["notes"] = (
                (notes.rstrip() + " " if notes else "")
                + _ROBOTS_BLOCKED_NOTE
            )
        evidence = src.setdefault("evidence", [])
        if not any(isinstance(e, dict) and e.get("url") == index_url for e in evidence):
            evidence.append({
                "url": index_url,
                "observed_on": profile.get("official_home_url"),
                "fetched_at": now,
            })
        if not dry_run:
            errors = validate_profile(profile)
            if not errors:
                path.write_text(
                    json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        return f"{muni_name} {kind} -> BLOCKED (robots: {exc})", False, None
    except Exception as exc:
        return f"{muni_name} {kind} -> error: {type(exc).__name__}: {str(exc)[:50]}", False, None

    if not ver_res:
        return f"{muni_name} {kind} -> unverified (no structural doc found)", False, None

    # Promote to ready
    doc_url = ver_res["doc_url"]
    doc_label = ver_res["doc_label"]
    obs_on = ver_res["observed_on"]
    markers = ver_res["markers"]

    src["status"] = "ready"
    src["adapter"] = "official_document_index"
    src["index_url"] = index_url
    src["verified_at"] = now
    src["verified_by"] = "verify --live"

    # Clean legacy keys
    src.pop("base_url", None)
    src.pop("tenant_url", None)

    # Prepare evidence idempotently
    evidence = src.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        src["evidence"] = evidence

    def _add_ev(item: dict[str, Any]) -> None:
        if not any(
            isinstance(e, dict)
            and e.get("url") == item.get("url")
            and e.get("sha256") == item.get("sha256")
            for e in evidence
        ):
            evidence.append(item)

    _add_ev({
        "url": index_url,
        "observed_on": profile.get("official_home_url"),
        "sha256": ver_res["idx_sha256"],
        "fetched_at": ver_res["idx_fetched_at"],
    })
    _add_ev({
        "url": doc_url,
        "observed_on": obs_on,
        "sha256": ver_res["doc_sha256"],
        "fetched_at": ver_res["doc_fetched_at"],
    })

    file_name = Path(urllib.parse.urlsplit(doc_url).path).name
    sha_short = ver_res["doc_sha256"][:8]
    note_text = (
        f"deepest_doc=P1。{kind}文書（{file_name}「{doc_label}」, sha256:{sha_short}）"
        f"にて構造マーカー（{'・'.join(markers[:4])}等）を確認済み。"
    )
    src["notes"] = note_text

    if not dry_run:
        errors = validate_profile(profile)
        if errors:
            return f"{muni_name} {kind} -> SCHEMA ERROR: {errors}", False, None
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return (
        f"{muni_name} {kind} -> READY (doc: {file_name}, markers: {markers[:3]})!",
        True,
        ver_res,
    )


def process_task(task: tuple[int, int, Path, str, str, bool]) -> tuple[str, bool]:
    idx, total, path, kind, now, dry_run = task
    profile = json.loads(path.read_text(encoding="utf-8"))
    client = HttpClient(
        CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        timeout=8,
        max_retries=1,
        min_interval_seconds=0.2,
    )
    msg, is_promoted, _ = verify_municipality_kind(
        path, profile, kind, client, now, dry_run=dry_run
    )
    return f"[{idx:4d}/{total:4d}] {msg}", is_promoted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Concurrent Level 2 Budget & Settlement Extractor and Verifier"
    )
    parser.add_argument(
        "--workers", type=int, default=16, help="Number of concurrent worker threads (default 16)"
    )
    parser.add_argument(
        "--prefecture-code", help="2-digit prefecture code to filter (e.g. 01, 13, 41)"
    )
    parser.add_argument(
        "--kind", choices=["budget", "settlement", "both"], default="both", help="Kind to verify"
    )
    parser.add_argument("--limit", type=int, help="Limit number of tasks to process")
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not write changes to source profile files"
    )
    parser.add_argument(
        "--cache-dir", default=str(CACHE_DIR), help="Cache directory for HTTP responses"
    )

    args = parser.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    kinds_to_run = ["budget", "settlement"] if args.kind == "both" else [args.kind]

    tasks_raw: list[tuple[Path, str]] = []
    profile_paths = sorted(MUNI_DIR.glob("*/*.json"))
    if args.prefecture_code:
        prefix = f"{args.prefecture_code}-"
        profile_paths = [p for p in profile_paths if p.parent.name.startswith(prefix)]

    for p in profile_paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        src = data.get("sources", {})
        for k in kinds_to_run:
            entry = src.get(k, {})
            if entry.get("status") in {"needs_review", "not_evaluated"}:
                if entry.get("index_url") or entry.get("base_url"):
                    tasks_raw.append((p, k))

    if args.limit:
        tasks_raw = tasks_raw[: args.limit]

    total = len(tasks_raw)
    tasks = [
        (i, total, p, k, now, args.dry_run)
        for i, (p, k) in enumerate(tasks_raw, 1)
    ]

    print(
        f"Starting Budget & Settlement Level 2 verification for {total} unpromoted entries "
        f"with {args.workers} workers (dry_run={args.dry_run})...",
        flush=True,
    )

    promoted_count = 0
    unpromoted_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process_task, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            msg, is_promoted = f.result()
            if is_promoted:
                promoted_count += 1
                print(f"*** {msg} ***", flush=True)
            else:
                unpromoted_count += 1
                print(msg, flush=True)

    print("\n=== Budget & Settlement Verification Summary ===", flush=True)
    print(f"  Total processed        : {total}", flush=True)
    print(f"  Newly promoted to READY: {promoted_count}", flush=True)
    print(f"  Unpromoted / Skipped   : {unpromoted_count}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
