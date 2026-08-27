#!/usr/bin/env python3
"""Concurrently verify unpromoted minutes source profiles across the nation."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lcaios.http import MINUTES_USER_AGENT, HttpClient
from source_profiles.schema import validate_profile
from source_profiles.verify import verify_profile

MUNI_DIR = REPO_ROOT / "source_profiles" / "municipalities"
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"


def process_municipality(task: tuple[int, int, Path, dict, str]) -> tuple[str, bool]:
    idx, total, path, profile, now = task
    muni_name = str(profile.get("municipality") or path.stem)
    min_src = profile.get("sources", {}).get("minutes", {})
    if not min_src or min_src.get("status") == "ready":
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> SKIP (already ready)", False

    url = min_src.get("index_url") or min_src.get("tenant_url") or min_src.get("base_url") or ""
    if not url:
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> SKIP (no URL)", False

    client = HttpClient(
        CACHE_DIR,
        user_agent=MINUTES_USER_AGENT,
        timeout=8,
        max_retries=1,
        min_interval_seconds=0.2,
    )

    try:
        updated, report = verify_profile(
            profile, client=client, now=now, kind="minutes"
        )
        if report.get("result") == "verified" and report.get("status_after") == "ready":
            errors = validate_profile(updated)
            if errors:
                return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> schema error: {errors}", False
            path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> READY ({min_src.get('adapter')})!", True

        if min_src.get("adapter") == "static":
            candidate = json.loads(json.dumps(profile))
            candidate["sources"]["minutes"].setdefault("config", {})["pdf"] = True
            cand_updated, cand_report = verify_profile(
                candidate, client=client, now=now, kind="minutes"
            )
            if cand_report.get("result") == "verified" and cand_report.get("status_after") == "ready":
                errors = validate_profile(cand_updated)
                if errors:
                    return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> schema error: {errors}", False
                path.write_text(
                    json.dumps(cand_updated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> READY (static PDF)!", True

        reason = str(report.get("reason") or "unverified")[:50]
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> {report.get('result')}: {reason}", False
    except Exception as exc:
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> error: {type(exc).__name__}: {str(exc)[:50]}", False


def main(max_workers: int = 16) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tasks_raw = []
    for p in sorted(MUNI_DIR.glob("*/*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        min_src = data.get("sources", {}).get("minutes", {})
        if min_src.get("status") in {"needs_review", "not_evaluated"}:
            tasks_raw.append((p, data))

    total = len(tasks_raw)
    tasks = [(i, total, p, d, now) for i, (p, d) in enumerate(tasks_raw, 1)]

    print(f"Verifying {total} unpromoted minutes profiles with {max_workers} workers...", flush=True)
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
                print(msg, flush=True)

    print("\n=== Minutes Verification Summary ===", flush=True)
    print(f"  Newly promoted to ready: {promoted}", flush=True)
    print(f"  Unpromoted             : {unpromoted}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
