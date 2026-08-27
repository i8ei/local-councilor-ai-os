"""pi watcher: auto-consume agy handoff and deep-dive (for nationwide unattended).

Polls tmp/herdr-handoff/hard-cases-*.json, runs pi_deep_scout Tier1,
then leaves residual for Tier2 manual (web_search/fetch).

Usage (herdr w38):
  python3 tools/pi_watcher.py --once          # one shot
  python3 tools/pi_watcher.py --poll 30       # poll every 30s (for nationwide)
  python3 tools/pi_watcher.py --poll 30 --prefecture-code 40  # filter

For fully unattended nationwide:
  # w38:p1
  nohup python3 tools/pi_watcher.py --poll 30 > tmp/pi_watcher.log 2>&1 &
  # w37: agy runs
  python3 tools/scout_profiles.py --prefecture-code 40  # -> auto handoff
  python3 tools/scout_profiles.py --prefecture-code 41  # ...
  # or loop all:
  for code in $(seq -w 1 47); do python3 tools/scout_profiles.py --prefecture-code $code; done
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _process_one(handoff: Path) -> bool:
    """Run pi_deep_scout on handoff. Return True if processed."""
    done_marker = handoff.with_suffix(".done")
    if done_marker.exists():
        return False
    # Skip empty
    try:
        data = json.loads(handoff.read_text(encoding="utf-8"))
        if not data:
            done_marker.write_text("empty\n", encoding="utf-8")
            print(f"skip empty {handoff}", flush=True)
            return False
    except Exception as e:
        print(f"skip unreadable {handoff}: {e}", flush=True)
        return False

    print(f"pi_watcher: processing {handoff} ({len(data)} cases)", flush=True)
    # Tier1 deep crawl
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools/pi_deep_scout.py"), "--handoff", str(handoff)],
        check=False,
    )
    if result.returncode == 0:
        done_marker.write_text(json.dumps({"status": "tier1_done", "cases": len(data)}, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"pi_watcher: Tier1 done for {handoff}", flush=True)
        # Check residual
        residual = REPO_ROOT / "tmp/herdr-handoff/hard-cases-residual.json"
        if residual.exists():
            try:
                r = json.loads(residual.read_text(encoding="utf-8"))
                if r:
                    print(f"pi_watcher: residual {len(r)} cases need Tier2 manual (web_search/fetch)", flush=True)
                    print(f"  next: pi should web_search each residual's name + '議事録/例規集' and update profile", flush=True)
            except Exception:
                pass
        return True
    else:
        print(f"pi_watcher: failed {handoff} (code {result.returncode})", flush=True)
        return False


def main() -> int:
    p = argparse.ArgumentParser(description="pi watcher for agy handoff")
    p.add_argument("--once", action="store_true", help="Process existing handoffs once and exit")
    p.add_argument("--poll", type=int, help="Poll interval seconds (e.g. 30)")
    p.add_argument("--prefecture-code", help="Only watch this code")
    args = p.parse_args()

    pattern = f"hard-cases-{args.prefecture_code}.json" if args.prefecture_code else "hard-cases-*.json"

    def scan() -> list[Path]:
        return sorted((REPO_ROOT / "tmp/herdr-handoff").glob(pattern))

    if args.once or not args.poll:
        for h in scan():
            _process_one(h)
        return 0

    print(f"pi_watcher: polling every {args.poll}s for {pattern} (Ctrl+C to stop)", flush=True)
    while True:
        for h in scan():
            _process_one(h)
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
