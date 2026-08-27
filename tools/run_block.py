"""Block runner for regional scout tasks with concurrency and early exit."""

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BLOCKS = {
    "kyushu_okinawa": ["40", "42", "43", "44", "45", "46", "47"],
    "chugoku_shikoku": ["31", "32", "33", "34", "35", "36", "37", "38", "39"],
    "kinki": ["24", "25", "26", "27", "28", "29", "30"],
    "chubu": ["15", "16", "17", "18", "19", "20", "21", "22", "23"],
    "kanto": ["08", "09", "10", "11", "12", "13", "14"],
    "tohoku_hokkaido": ["01", "02", "03", "04", "05", "06", "07"],
}

from lcaios.http import BOOTSTRAP_USER_AGENT, HttpClient
from tools.scout_profiles import scout_municipality
from source_profiles.schema import validate_profile
import json

print_lock = threading.Lock()


def scout_worker(path: Path, cache_dir: str) -> tuple[bool, str, dict]:
    client = HttpClient(Path(cache_dir), user_agent=BOOTSTRAP_USER_AGENT)
    try:
        data = scout_municipality(path, client)
        return True, path.stem, data
    except Exception as exc:
        return False, path.stem, {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scout for a regional block.")
    parser.add_argument("--block", required=True, choices=list(BLOCKS.keys()), help="Regional block name")
    parser.add_argument("--concurrency", type=int, default=8, help="Number of concurrent workers (default: 8)")
    parser.add_argument("--cache-dir", default=".tasks/cache/scout", help="Cache directory")
    args = parser.parse_args()

    pref_codes = BLOCKS[args.block]
    cache_path = Path(args.cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    profiles_root = REPO_ROOT / "source_profiles" / "municipalities"
    profile_paths: list[Path] = []

    for pref_code in pref_codes:
        for p in sorted(profiles_root.glob(f"{pref_code}-*/*.json")):
            if "41-saga" in str(p):
                continue
            profile_paths.append(p)

    print(f"=== Block: {args.block} (Prefectures: {','.join(pref_codes)}) ===", flush=True)
    print(f"Total municipalities to scout: {len(profile_paths)} (Concurrency: {args.concurrency})", flush=True)

    success = 0
    errors = 0
    completed_count = 0
    total = len(profile_paths)

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(scout_worker, path, args.cache_dir): path for path in profile_paths}
        for future in as_completed(futures):
            ok, name, data = future.result()
            with print_lock:
                completed_count += 1
                if ok:
                    m_name = data.get("municipality", name)
                    s = data.get("sources", {})
                    min_st = s.get("minutes", {}).get("status")
                    reg_st = s.get("regulations", {}).get("status")
                    bud_st = s.get("budget", {}).get("status")
                    set_st = s.get("settlement", {}).get("status")
                    print(f"[{completed_count:3d}/{total:3d}] {m_name:8s} | min: {min_st:12s} | reg: {reg_st:12s} | bud: {bud_st:12s} | set: {set_st:12s}", flush=True)
                    success += 1
                else:
                    print(f"[{completed_count:3d}/{total:3d}] ERROR {name}: {data.get('error')}", file=sys.stderr, flush=True)
                    errors += 1

    print(f"\n=== Block {args.block} Finished: {success} succeeded, {errors} errors ===", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
