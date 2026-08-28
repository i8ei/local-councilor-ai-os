"""pi deep-dive scout for hard cases (herdr w38).

Tier 1: deeper crawl (max_pages=30, sitemap含む) — 自動
Tier 2: still not_found は pi が web_search/fetch で手動深掘り
        → docs/ingestion-playbook.md レシピ1 の手順で evidence 付き更新
Tier 3: それでも無ければ not_found のまま notes に理由を残す（推測しない）

Usage (single):
  python3 tools/pi_deep_scout.py --profile source_profiles/municipalities/40-fukuoka/40206-xxx.json

Usage (batch from handoff file):
  python3 tools/pi_deep_scout.py --handoff tmp/herdr-handoff/hard-cases-40.json
  python3 tools/pi_deep_scout.py --handoff tmp/herdr-handoff/hard-cases-40.json --limit 3

Usage (prefecture):
  python3 tools/pi_deep_scout.py --prefecture-code 40 --status not_found
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lcaios.http import BOOTSTRAP_USER_AGENT, CacheTier, HttpClient
from tools.scout_profiles import scout_municipality


def _load_handoff(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def main() -> int:
    p = argparse.ArgumentParser(description="pi deep scout (max_pages=30)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", help="Single profile path")
    g.add_argument("--handoff", help="Handoff JSON from scout_hard_cases.py")
    g.add_argument("--prefecture-code", help="2-digit code, e.g. 40")
    p.add_argument("--kinds", nargs="*", default=None, help="Filter kinds (minutes/regulations/budget/settlement)")
    p.add_argument("--status", default="not_found", choices=["not_found", "any"])
    p.add_argument("--max-pages", type=int, default=30, help="Pages to crawl (default 30)")
    p.add_argument("--limit", type=int, help="Limit number to process")
    p.add_argument("--cache-dir", default=".tasks/cache/scout")
    args = p.parse_args()

    cache_path = Path(args.cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    client = HttpClient(cache_path, user_agent=BOOTSTRAP_USER_AGENT)

    if args.profile:
        profiles = [Path(args.profile)]
    elif args.handoff:
        items = _load_handoff(Path(args.handoff))
        if args.limit:
            items = items[:args.limit]
        profiles = [REPO_ROOT / it["profile"] for it in items]
    else:
        # prefecture-code mode: reuse hard-case extraction inline
        from tools.scout_hard_cases import KINDS  # noqa
        roots = sorted((REPO_ROOT / "source_profiles" / "municipalities").rglob("*.json"))
        roots = [x for x in roots if x.parent.name.startswith(f"{args.prefecture_code}-")]
        profiles = []
        for path in roots:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for k in (args.kinds or list(KINDS)):
                st = (data.get("sources", {}).get(k, {}) or {}).get("status")
                if args.status == "any":
                    if st in ("not_found", "needs_review"):
                        profiles.append(path)
                        break
                elif st == args.status:
                    profiles.append(path)
                    break
        profiles = sorted(set(profiles))
        if args.limit:
            profiles = profiles[:args.limit]

    if not profiles:
        print("no profiles to process", flush=True)
        return 0

    print(f"pi deep scout: {len(profiles)} profiles, max_pages={args.max_pages}", flush=True)
    # Try sitemap prefetch for each profile's home (best-effort, no fail)
    for path in profiles:
        try:
            d = json.loads((REPO_ROOT / path.relative_to(REPO_ROOT) if path.is_absolute() else REPO_ROOT / path).read_text(encoding="utf-8"))
            home = d.get("official_home_url", "")
            if home:
                import urllib.parse
                parts = urllib.parse.urlsplit(home)
                sitemap_url = f"{parts.scheme}://{parts.netloc}/sitemap.xml"
                try:
                    r = client.fetch(sitemap_url, tier=CacheTier.INDEX)
                    # just warming cache; scout_municipality will discover links via normal crawl
                    _ = r.text()[:1]
                except Exception:
                    pass
        except Exception:
            pass

    ok = 0
    err = 0
    residual: list[dict] = []
    for i, path in enumerate(profiles, 1):
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
        # resolve relative to repo
        abs_path = REPO_ROOT / rel if not path.is_absolute() else path
        try:
            before = json.loads(abs_path.read_text(encoding="utf-8"))
            before_s = {k: before.get("sources", {}).get(k, {}).get("status") for k in ("minutes","regulations","budget","settlement")}
            data = scout_municipality(abs_path, client, max_pages=args.max_pages)
            after_s = {k: data.get("sources", {}).get(k, {}).get("status") for k in ("minutes","regulations","budget","settlement")}
            # show diff
            diff = ",".join(f"{k}:{before_s[k]}->{after_s[k]}" for k in before_s if before_s[k] != after_s[k]) or "no change"
            print(f"[{i}/{len(profiles)}] {data.get('municipality',''):8s} {diff} | {rel}", flush=True)
            ok += 1
            # collect still not_found for Tier 2 manual
            still = [k for k in ("minutes","regulations","budget","settlement") if after_s[k] == "not_found"]
            if still:
                residual.append({"profile": str(rel), "name": data.get("municipality",""), "still_missing": still})
        except Exception as exc:
            print(f"[{i}/{len(profiles)}] ERROR {rel}: {exc}", file=sys.stderr, flush=True)
            err += 1

    print(f"done: {ok} ok, {err} errors", flush=True)
    if residual:
        out = REPO_ROOT / "tmp/herdr-handoff/hard-cases-residual.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(residual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"residual not_found: {len(residual)} -> {out}", flush=True)
        print("Tier 2: pi が web_search/fetch で手動深掘りしてください。例:", flush=True)
        for res_item in residual[:5]:
            print(f"  web_search: \"{res_item['name']} 議事録\" / \"{res_item['name']} 例規集\" -> fetch検証 -> profile更新", flush=True)
        print("  参考: docs/ingestion-playbook.md レシピ1", flush=True)
    else:
        print("all hard cases resolved (no residual)", flush=True)
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
