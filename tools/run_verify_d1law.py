#!/usr/bin/env python3
"""Run targeted verification for D1-Law regulations across all municipalities."""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lcaios.http import REGULATIONS_USER_AGENT, HttpClient
from source_profiles.verify import verify_profile

MUNI_DIR = REPO_ROOT / "source_profiles" / "municipalities"
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"


def main() -> int:
    client = HttpClient(
        CACHE_DIR,
        user_agent=REGULATIONS_USER_AGENT,
        timeout=30,
        min_interval_seconds=0.5,
    )
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tasks: list[tuple[str, Path, dict]] = []
    for p in sorted(MUNI_DIR.glob("*/*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        reg = data.get("sources", {}).get("regulations", {})
        if reg.get("adapter") == "d1_law" and reg.get("status") == "needs_review":
            muni_name = str(data.get("municipality") or p.stem)
            tasks.append((muni_name, p, data))

    print(f"Found {len(tasks)} D1-Law municipalities in needs_review state.", flush=True)
    verified_count = 0
    failed_count = 0

    for idx, (muni_name, path, profile) in enumerate(tasks, 1):
        updated, report = verify_profile(
            profile, client=client, now=now, kind="regulations"
        )
        res = report.get("result")
        reason = report.get("reason", "")
        status_after = report.get("status_after")

        if res == "verified" and status_after == "ready":
            verified_count += 1
            path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"[{idx:2d}/{len(tasks):2d}] {muni_name:12s} -> READY (verified)", flush=True)
        else:
            failed_count += 1
            print(f"[{idx:2d}/{len(tasks):2d}] {muni_name:12s} -> {status_after} ({res}: {reason[:60]})", flush=True)

    print(f"\nSummary: {verified_count} verified & promoted to ready, {failed_count} unpromoted.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
