"""Extract not_found / needs_review profiles for pi deep-dive handoff.

Usage:
  python3 tools/scout_hard_cases.py --prefecture-code 40
  python3 tools/scout_hard_cases.py --prefecture-code 40 --status not_found
  python3 tools/scout_hard_cases.py --prefecture-code 40 --output tmp/herdr-handoff/hard-cases-40.json

Reads source_profiles/municipalities/** and writes a JSON array that
pi (w38) can consume one-by-one. No dependencies beyond stdlib.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KINDS = ("minutes", "regulations", "budget", "settlement")


def main() -> int:
    p = argparse.ArgumentParser(description="Extract hard cases for pi deep dive")
    p.add_argument("--prefecture-code", help="2-digit code, e.g. 40")
    p.add_argument("--prefecture", help="Prefecture name, e.g. 福岡県")
    p.add_argument("--status", default="not_found", choices=["not_found", "needs_review", "any"],
                   help="Which status to extract (default: not_found)")
    p.add_argument("--kinds", nargs="*", default=list(KINDS), choices=list(KINDS))
    p.add_argument("--output", help="Output path (default: tmp/herdr-handoff/hard-cases-{code}.json)")
    args = p.parse_args()

    roots = sorted((REPO_ROOT / "source_profiles" / "municipalities").rglob("*.json"))
    if args.prefecture_code:
        roots = [x for x in roots if x.parent.name.startswith(f"{args.prefecture_code}-")]

    hard: list[dict] = []
    for path in roots:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"skip {path}: {e}", file=sys.stderr)
            continue
        if args.prefecture and data.get("prefecture") != args.prefecture:
            continue
        missing = []
        for k in args.kinds:
            st = (data.get("sources", {}).get(k, {}) or {}).get("status")
            if args.status == "any":
                if st in ("not_found", "needs_review"):
                    missing.append(k)
            elif st == args.status:
                missing.append(k)
        if not missing:
            continue
        hard.append({
            "profile": str(path.relative_to(REPO_ROOT)),
            "code": data.get("code") or path.stem.split("-")[0],
            "name": data.get("municipality") or data.get("name") or path.stem,
            "prefecture": data.get("prefecture"),
            "home": data.get("official_home_url"),
            "missing": missing,
            "current_status": {k: data.get("sources", {}).get(k, {}).get("status") for k in KINDS},
        })

    hard.sort(key=lambda x: x["code"])

    if args.output:
        out = Path(args.output)
    elif args.prefecture_code:
        out = REPO_ROOT / f"tmp/herdr-handoff/hard-cases-{args.prefecture_code}.json"
    else:
        out = REPO_ROOT / "tmp/herdr-handoff/hard-cases.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(hard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Summary to stdout (agy/pi log friendly)
    print(f"hard cases: {len(hard)} -> {out}", flush=True)
    by_kind: dict[str, int] = {k: 0 for k in KINDS}
    for h in hard:
        for k in h["missing"]:
            by_kind[k] += 1
    for k in KINDS:
        if by_kind[k]:
            print(f"  {k}: {by_kind[k]}", flush=True)
    for h in hard[:20]:
        print(f"  {h['code']} {h['name']:10s} missing={','.join(h['missing'])}", flush=True)
    if len(hard) > 20:
        print(f"  ... and {len(hard)-20} more", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
