#!/usr/bin/env python3
"""Discover hidden vendor URLs from static/needs_review landing pages and verify them concurrently."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lcaios.http import REGULATIONS_USER_AGENT, CacheTier, HttpClient
from source_profiles.schema import validate_profile
from source_profiles.verify import verify_profile

MUNI_DIR = Path(
    os.environ.get("LCAIOS_MUNI_DIR", REPO_ROOT / "source_profiles" / "municipalities")
)
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"

VENDOR_PATTERNS = [
    (re.compile(r"ops-jg\.d1-law\.com", re.I), "d1_law", "index_url"),
    (re.compile(r"d1w_reiki/reiki\.html?", re.I), "d1_law", "index_url"),
    (re.compile(r"www\d*\.g-reiki\.net", re.I), "g_reiki", "base_url"),
    (re.compile(r"(?:public\d*\.)?joureikun\.jp", re.I), "joureikun", "index_url"),
    (re.compile(r"(?:public\d*\.)?legalcrud\.com", re.I), "joureikun", "index_url"),
]


def process_municipality(task: tuple[int, int, Path, dict, str]) -> tuple[str, bool]:
    idx, total, path, profile, now = task
    muni_name = str(profile.get("municipality") or path.stem)
    reg = profile.get("sources", {}).get("regulations", {})
    if not reg or reg.get("status") == "ready":
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> SKIP (already ready)", False

    current_url = reg.get("index_url") or reg.get("base_url") or ""
    if not current_url:
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> SKIP (no URL)", False

    client = HttpClient(
        CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        timeout=8,
        max_retries=1,
        min_interval_seconds=0.2,
    )

    # First try direct verification if already a known vendor adapter
    if reg.get("adapter") in {"g_reiki", "d1_law", "joureikun"}:
        try:
            updated, report = verify_profile(
                profile, client=client, now=now, kind="regulations"
            )
            if report.get("result") == "verified" and report.get("status_after") == "ready":
                path.write_text(
                    json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> READY directly verified!", True
        except Exception:
            pass

    # Next, crawl landing page to discover vendor links
    discovered_target = None
    try:
        res = client.fetch(current_url, tier=CacheTier.INDEX)
        html = res.text()
        links = re.findall(r'<a\s+[^>]*href=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        for href in links:
            resolved = urllib.parse.urljoin(res.final_url, href)
            for pat, adapter, url_field in VENDOR_PATTERNS:
                if pat.search(resolved):
                    discovered_target = (resolved, adapter, url_field)
                    break
            if discovered_target:
                break
    except Exception:
        pass

    if not discovered_target:
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> no vendor links found on landing page", False

    resolved_url, adapter, url_field = discovered_target
    if adapter == "g_reiki" and (resolved_url.endswith("/reiki_menu.html") or resolved_url.endswith("/reiki.html")):
        resolved_url = resolved_url.rsplit("/", 1)[0] + "/"

    candidate_profile = json.loads(json.dumps(profile))
    candidate_reg = candidate_profile["sources"]["regulations"]
    candidate_reg["adapter"] = adapter
    if url_field == "index_url":
        candidate_reg["index_url"] = resolved_url
        candidate_reg.pop("base_url", None)
    else:
        candidate_reg["base_url"] = resolved_url
        candidate_reg.pop("index_url", None)

    try:
        updated, report = verify_profile(
            candidate_profile, client=client, now=now, kind="regulations"
        )
        if report.get("result") == "verified" and report.get("status_after") == "ready":
            errors = validate_profile(updated)
            if errors:
                return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> schema error on verified profile: {errors}", False
            path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> READY discovered ({adapter}: {resolved_url})!", True
        else:
            reason = report.get("reason") or ""
            return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> discovered ({adapter}) but verify failed: {reason[:50]}", False
    except Exception as exc:
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> error: {type(exc).__name__}: {str(exc)[:50]}", False


def main(max_workers: int = 16) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tasks_raw = []
    for p in sorted(MUNI_DIR.glob("*/*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        reg = data.get("sources", {}).get("regulations", {})
        if reg.get("status") != "ready":
            tasks_raw.append((p, data))

    total = len(tasks_raw)
    tasks = [(i, total, p, d, now) for i, (p, d) in enumerate(tasks_raw, 1)]

    print(f"Scanning & verifying {total} unpromoted regulation profiles with {max_workers} workers...", flush=True)
    promoted = 0
    unpromoted = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(process_municipality, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            msg, is_promoted = f.result()
            if is_promoted:
                promoted += 1
                print(f"*** {msg} ***", flush=True)
            else:
                unpromoted += 1
                if "no vendor links found" not in msg and "SKIP" not in msg:
                    print(msg, flush=True)

    print("\n=== Discover & Verify Summary ===", flush=True)
    print(f"  Newly promoted to ready: {promoted}", flush=True)
    print(f"  Unpromoted             : {unpromoted}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
