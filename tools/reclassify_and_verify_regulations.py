#!/usr/bin/env python3
"""Fix regulation adapter misclassifications and verify all needs_review regulations concurrently."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lcaios.http import REGULATIONS_USER_AGENT, HttpClient
from source_profiles.schema import validate_profile
from source_profiles.verify import verify_profile

MUNI_DIR = REPO_ROOT / "source_profiles" / "municipalities"
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"


def reclassify_profiles() -> list[tuple[str, Path, dict]]:
    reclassified = []
    for p in sorted(MUNI_DIR.glob("*/*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        reg = data.get("sources", {}).get("regulations", {})
        if not reg:
            continue
        ad = reg.get("adapter")
        url = reg.get("base_url") or reg.get("index_url") or ""
        muni = data.get("municipality") or p.stem

        new_ad = ad
        if any(
            x in url.lower()
            for x in ("d1w_reiki", "d1-law.com", "ops-jg", "en3-jg")
        ):
            new_ad = "d1_law"
        elif "joureikun" in url.lower():
            new_ad = "joureikun"
        elif "g-reiki.net" in url.lower() or "reiki.metro.tokyo" in url.lower():
            new_ad = "g_reiki"

        if new_ad != ad:
            reg["adapter"] = new_ad
            if new_ad in {"d1_law", "joureikun"} and "base_url" in reg:
                reg["index_url"] = reg.pop("base_url")
            elif new_ad == "g_reiki" and "index_url" in reg:
                reg["base_url"] = reg.pop("index_url")

            # Validate the modified profile
            errors = validate_profile(data)
            if errors:
                print(f"Validation error in {p.stem} after reclassification: {errors}", file=sys.stderr)
                continue

            p.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            reclassified.append((muni, p, data))

    return reclassified


def verify_one(task: tuple[int, int, str, Path, dict, str]) -> tuple[str, str, str, str, bool]:
    idx, total, muni_name, path, profile, now = task
    client = HttpClient(
        CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        timeout=8,
        max_retries=1,
        min_interval_seconds=0.2,
    )
    ad = profile["sources"]["regulations"].get("adapter", "")
    try:
        updated, report = verify_profile(
            profile, client=client, now=now, kind="regulations"
        )
        res = report.get("result", "")
        reason = report.get("reason", "")
        status_after = report.get("status_after", "")

        if res == "verified" and status_after == "ready":
            path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return (f"[{idx:3d}/{total:3d}] {muni_name:12s} ({ad:8s}) -> READY (verified)", res, status_after, reason, True)
        else:
            return (f"[{idx:3d}/{total:3d}] {muni_name:12s} ({ad:8s}) -> {status_after} ({res}: {reason[:50]})", res, status_after, reason, False)
    except Exception as exc:
        return (f"[{idx:3d}/{total:3d}] {muni_name:12s} ({ad:8s}) -> error ({type(exc).__name__}: {str(exc)[:50]})", "error", "needs_review", str(exc), False)


def verify_all_needs_review(max_workers: int = 16) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tasks_raw = []
    for p in sorted(MUNI_DIR.glob("*/*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        reg = data.get("sources", {}).get("regulations", {})
        if reg.get("status") == "needs_review" and reg.get("adapter") in {
            "g_reiki",
            "d1_law",
            "joureikun",
        }:
            muni_name = str(data.get("municipality") or p.stem)
            tasks_raw.append((muni_name, p, data))

    total = len(tasks_raw)
    tasks = [(i, total, muni, p, d, now) for i, (muni, p, d) in enumerate(tasks_raw, 1)]

    print(f"\nStarting concurrent verification for {total} needs_review regulations ({max_workers} workers)...", flush=True)
    promoted = 0
    unpromoted = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(verify_one, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            msg, res, status_after, reason, is_promoted = f.result()
            if is_promoted:
                promoted += 1
            else:
                unpromoted += 1
            print(msg, flush=True)

    print("\n=== Verification Summary ===", flush=True)
    print(f"  Promoted to ready: {promoted}", flush=True)
    print(f"  Unpromoted       : {unpromoted}", flush=True)


def main() -> int:
    reclassified = reclassify_profiles()
    print(f"Reclassified {len(reclassified)} misclassified regulation profiles.")
    verify_all_needs_review(max_workers=16)
    return 0


if __name__ == "__main__":
    sys.exit(main())
