#!/usr/bin/env python3
"""Discover hidden minutes indexes & vendor URLs and verify them concurrently across the nation."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lcaios.html import LinkParser
from lcaios.http import MINUTES_USER_AGENT, CacheTier, HttpClient
from source_profiles.schema import validate_profile
from source_profiles.verify import verify_profile

MUNI_DIR = REPO_ROOT / "source_profiles" / "municipalities"
CACHE_DIR = REPO_ROOT / ".tasks" / "cache" / "verify"

NON_COUNCIL_TEXT_TOKENS = (
    "教育委員会",
    "農業委員会",
    "選挙管理委員会",
    "総合教育会議",
    "監査委員",
    "審議会",
    "審査会",
    "懇話会",
    "行政不服",
    "情報公開審査",
    "個人情報保護審査",
    "固定資産評価",
    "民生委員",
    "協議会",
    "選挙",
)

NON_COUNCIL_URL_TOKENS = (
    "kyouiku",
    "kyoiku",
    "nougyou",
    "nogyo",
    "senkyo",
    "election",
    "kansa",
    "shingikai",
    "shingi",
    "shinsakai",
    "shinsa",
    "konwakai",
    "sougoukyouiku",
    "minsei",
)

COMMON_SUBPATHS = (
    "gikai/",
    "gikai/index.html",
    "shigikai/",
    "shigikai/index.html",
    "chosei/gikai/",
    "chosei/shigikai/",
    "chosei/kaigiroku/",
    "kaigiroku/",
    "gijiroku/",
    "council/",
    "assembly/",
    "shisei/gikai/",
    "shisei/shigikai/",
    "gikai/kaigiroku/",
    "gikai/gijiroku/",
    "gikai/teireikai/",
    "gikai/honkaigi/",
    "gikai/kaigiroku.html",
    "site/gikai/",
    "soshiki/gikai/",
    "gyosei/gikai/",
    "gikai_jimukyoku/",
)

FOLLOW_LINK_REGEX = (
    r"(?:令和|平成|\d+年|定例会|臨時会|本会議|会議録|議事録|第\d+回|kaigiroku|gijiroku)"
)


def _host(url: str) -> str | None:
    try:
        p = urllib.parse.urlsplit(url)
        return p.netloc.lower() if p.netloc else None
    except Exception:
        return None


def _is_same_or_council_host(target_host: str | None, base_host: str | None) -> bool:
    if not target_host or not base_host:
        return False
    t = target_host.removeprefix("www.")
    b = base_host.removeprefix("www.")
    if t == b:
        return True
    if t.startswith("gikai.") or t.startswith("shigikai.") or t.startswith("council."):
        if t.split(".", 1)[1] == b:
            return True
    return False


def _is_non_council(label: str, url: str) -> bool:
    lbl = label.strip()
    url_lower = urllib.parse.unquote(url).lower()
    for tok in NON_COUNCIL_TEXT_TOKENS:
        if tok in lbl:
            return True
    for tok in NON_COUNCIL_URL_TOKENS:
        if f"/{tok}" in url_lower or f"_{tok}" in url_lower or f"-{tok}" in url_lower or f"{tok}." in url_lower:
            return True
    return False


def _score_link(label: str, url: str) -> int:
    if _is_non_council(label, url):
        return -100
    lbl_lower = label.lower()
    url_lower = url.lower()
    score = 0
    if "会議録" in label or "議事録" in label:
        score += 25
    if "kaigiroku" in url_lower or "gijiroku" in url_lower:
        score += 20
    if "定例会" in label or "本会議" in label or "臨時会" in label:
        score += 15
    if "gikai" in url_lower or "shigikai" in url_lower:
        score += 10
    if any(k in label for k in ("市議会", "町議会", "村議会", "区議会", "議会")):
        score += 8
    if "pdf" in lbl_lower or "pdf" in url_lower:
        score += 5
    return score


def _try_save_verified(
    candidate: dict[str, Any],
    client: HttpClient,
    now: str,
    path: Path,
    muni_name: str,
    label: str,
) -> tuple[str, bool] | None:
    try:
        updated, report = verify_profile(
            candidate, client=client, now=now, kind="minutes"
        )
        if report.get("result") == "verified" and report.get("status_after") == "ready":
            errors = validate_profile(updated)
            if errors:
                return f"{muni_name:12s} -> schema error: {errors}", False
            path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return f"{muni_name:12s} -> READY ({label})!", True
    except Exception:
        pass
    return None


def process_municipality(task: tuple[int, int, Path, dict, str]) -> tuple[str, bool]:
    idx, total, path, profile, now = task
    muni_name = str(profile.get("municipality") or path.stem)
    min_src = profile.get("sources", {}).get("minutes", {})
    if not min_src or min_src.get("status") == "ready":
        return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> SKIP (already ready)", False

    client = HttpClient(
        CACHE_DIR,
        user_agent=MINUTES_USER_AGENT,
        timeout=8,
        max_retries=1,
        min_interval_seconds=0.2,
    )

    current_url = (
        min_src.get("index_url")
        or min_src.get("tenant_url")
        or min_src.get("base_url")
        or ""
    )
    is_discuss = "discussvision.net" in current_url or min_src.get("adapter") == "discuss"

    # 1. Direct verification of existing URL (if not discussvision and not non-council)
    if current_url and not is_discuss and not _is_non_council("", current_url):
        res_v = _try_save_verified(profile, client, now, path, muni_name, "direct existing")
        if res_v and res_v[1]:
            return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

        if min_src.get("adapter") == "static":
            cand = json.loads(json.dumps(profile))
            cand["sources"]["minutes"].setdefault("config", {})["pdf"] = True
            res_v = _try_save_verified(cand, client, now, path, muni_name, "direct static pdf")
            if res_v and res_v[1]:
                return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

            cand["sources"]["minutes"]["config"] = {
                "pdf": True,
                "follow_link_regex": FOLLOW_LINK_REGEX,
                "follow_max_depth": 2,
                "follow_max_pages": 5,
            }
            res_v = _try_save_verified(cand, client, now, path, muni_name, "direct static follow")
            if res_v and res_v[1]:
                return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

    # 2. Collect Seed URLs
    home_url = profile.get("official_home_url") or ""
    home_host = _host(home_url)
    seed_urls: list[str] = []

    for ev in min_src.get("evidence", []):
        obs = ev.get("observed_on")
        if obs and obs not in seed_urls and not _is_non_council("", obs):
            seed_urls.append(obs)

    for k in ("budget", "settlement", "regulations"):
        for ev in profile.get("sources", {}).get(k, {}).get("evidence", []):
            obs = ev.get("observed_on")
            if obs and _host(obs) == home_host and obs not in seed_urls and not _is_non_council("", obs):
                seed_urls.append(obs)

    if home_url:
        if home_url not in seed_urls:
            seed_urls.append(home_url)
        parsed = urllib.parse.urlsplit(home_url)
        base = f"{parsed.scheme}://{parsed.netloc}/"
        for sub in COMMON_SUBPATHS:
            u = urllib.parse.urljoin(base, sub)
            if u not in seed_urls:
                seed_urls.append(u)

    seen_urls: set[str] = set()
    level1_candidates: list[tuple[int, str, str, str]] = []

    # Level 1 crawl: scan seed URLs
    for s_url in seed_urls:
        if s_url in seen_urls:
            continue
        seen_urls.add(s_url)
        try:
            res = client.fetch(s_url, tier=CacheTier.INDEX)
            parser = LinkParser()
            parser.feed(res.text())
            for href, label in parser.links:
                full = urllib.parse.urljoin(res.final_url, href)
                if full in seen_urls:
                    continue

                # Vendor Check: ssp.kaigiroku.net
                if "ssp.kaigiroku.net/tenant/" in full and not _is_non_council(label, full):
                    cand = json.loads(json.dumps(profile))
                    cand["sources"]["minutes"] = {
                        "status": "needs_review",
                        "adapter": "kaigiroku_net",
                        "tenant_url": full,
                        "evidence": [{"url": full, "observed_on": s_url, "fetched_at": now}],
                    }
                    res_v = _try_save_verified(cand, client, now, path, muni_name, f"kaigiroku_net: {full}")
                    if res_v and res_v[1]:
                        return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

                # Vendor Check: dbsr.jp
                if ".dbsr.jp" in full and "/index.php" in full and not _is_non_council(label, full):
                    cand = json.loads(json.dumps(profile))
                    cand["sources"]["minutes"] = {
                        "status": "needs_review",
                        "adapter": "dbsr",
                        "index_url": full,
                        "evidence": [{"url": full, "observed_on": s_url, "fetched_at": now}],
                    }
                    res_v = _try_save_verified(cand, client, now, path, muni_name, f"dbsr: {full}")
                    if res_v and res_v[1]:
                        return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

                # Static candidate check on municipal domain
                if not _is_same_or_council_host(_host(full), home_host):
                    continue

                score = _score_link(label, full)
                if score > 0:
                    level1_candidates.append((score, full, s_url, label))
        except Exception:
            pass

    # Sort Level 1 candidates by score descending
    level1_candidates.sort(key=lambda x: x[0], reverse=True)

    # Test top Level 1 candidate pages
    level2_subpages: list[tuple[int, str, str, str]] = []
    for _, c_url, obs_on, _c_lbl in level1_candidates[:15]:
        if c_url in seen_urls:
            continue
        seen_urls.add(c_url)

        # 1. Test direct static PDF
        cand = json.loads(json.dumps(profile))
        cand["sources"]["minutes"] = {
            "status": "needs_review",
            "adapter": "static",
            "index_url": c_url,
            "config": {"pdf": True},
            "evidence": [{"url": c_url, "observed_on": obs_on, "fetched_at": now}],
        }
        res_v = _try_save_verified(cand, client, now, path, muni_name, f"static direct: {c_url}")
        if res_v and res_v[1]:
            return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

        # 2. Test static with follow link regex
        cand["sources"]["minutes"]["config"] = {
            "pdf": True,
            "follow_link_regex": FOLLOW_LINK_REGEX,
            "follow_max_depth": 2,
            "follow_max_pages": 5,
        }
        res_v = _try_save_verified(cand, client, now, path, muni_name, f"static follow: {c_url}")
        if res_v and res_v[1]:
            return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

        # 3. Level 2 deep scan: fetch candidate page to find subpages & vendor links
        try:
            c_res = client.fetch(c_url, tier=CacheTier.INDEX)
            c_parser = LinkParser()
            c_parser.feed(c_res.text())
            for href, label in c_parser.links:
                full2 = urllib.parse.urljoin(c_res.final_url, href)
                if full2 in seen_urls:
                    continue

                if "ssp.kaigiroku.net/tenant/" in full2 and not _is_non_council(label, full2):
                    cand = json.loads(json.dumps(profile))
                    cand["sources"]["minutes"] = {
                        "status": "needs_review",
                        "adapter": "kaigiroku_net",
                        "tenant_url": full2,
                        "evidence": [{"url": full2, "observed_on": c_url, "fetched_at": now}],
                    }
                    res_v = _try_save_verified(cand, client, now, path, muni_name, f"kaigiroku_net: {full2}")
                    if res_v and res_v[1]:
                        return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

                if ".dbsr.jp" in full2 and "/index.php" in full2 and not _is_non_council(label, full2):
                    cand = json.loads(json.dumps(profile))
                    cand["sources"]["minutes"] = {
                        "status": "needs_review",
                        "adapter": "dbsr",
                        "index_url": full2,
                        "evidence": [{"url": full2, "observed_on": c_url, "fetched_at": now}],
                    }
                    res_v = _try_save_verified(cand, client, now, path, muni_name, f"dbsr: {full2}")
                    if res_v and res_v[1]:
                        return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

                if not _is_same_or_council_host(_host(full2), home_host):
                    continue

                score2 = _score_link(label, full2)
                if score2 > 0:
                    if re.search(FOLLOW_LINK_REGEX, label) or re.search(FOLLOW_LINK_REGEX, full2):
                        score2 += 10
                    level2_subpages.append((score2, full2, c_url, label))
        except Exception:
            pass

    # Test top Level 2 subpages
    level2_subpages.sort(key=lambda x: x[0], reverse=True)
    for _, l2_url, l2_obs, _l2_lbl in level2_subpages[:10]:
        if l2_url in seen_urls:
            continue
        seen_urls.add(l2_url)

        cand = json.loads(json.dumps(profile))
        cand["sources"]["minutes"] = {
            "status": "needs_review",
            "adapter": "static",
            "index_url": l2_url,
            "config": {"pdf": True},
            "evidence": [{"url": l2_url, "observed_on": l2_obs, "fetched_at": now}],
        }
        res_v = _try_save_verified(cand, client, now, path, muni_name, f"static l2 direct: {l2_url}")
        if res_v and res_v[1]:
            return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

        cand["sources"]["minutes"]["config"] = {
            "pdf": True,
            "follow_link_regex": FOLLOW_LINK_REGEX,
            "follow_max_depth": 2,
            "follow_max_pages": 5,
        }
        res_v = _try_save_verified(cand, client, now, path, muni_name, f"static l2 follow: {l2_url}")
        if res_v and res_v[1]:
            return f"[{idx:3d}/{total:3d}] {res_v[0]}", True

    return f"[{idx:3d}/{total:3d}] {muni_name:12s} -> unpromoted", False


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and verify minutes profiles nationwide.")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent worker count (default: 16)")
    parser.add_argument("--prefecture", type=str, default=None, help="Filter by prefecture code or directory (e.g. 41 or 41-saga)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of municipalities to process")
    args = parser.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.prefecture:
        pref_pattern = f"*{args.prefecture}*/*.json"
        all_files = sorted(MUNI_DIR.glob(pref_pattern))
    else:
        all_files = sorted(MUNI_DIR.glob("*/*.json"))

    tasks_raw = []
    for p in all_files:
        data = json.loads(p.read_text(encoding="utf-8"))
        min_src = data.get("sources", {}).get("minutes", {})
        if min_src.get("status") != "ready":
            tasks_raw.append((p, data))

    if args.limit:
        tasks_raw = tasks_raw[: args.limit]

    total = len(tasks_raw)
    tasks = [(i, total, p, d, now) for i, (p, d) in enumerate(tasks_raw, 1)]

    print(
        f"Scanning & verifying {total} unpromoted minutes profiles with {args.workers} workers...",
        flush=True,
    )
    promoted = 0
    unpromoted = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process_municipality, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            msg, is_promoted = f.result()
            if is_promoted:
                promoted += 1
                print(f"*** {msg} ***", flush=True)
            else:
                unpromoted += 1
                print(msg, flush=True)

    print("\n=== Minutes Discover & Verify Summary ===", flush=True)
    print(f"  Newly promoted to ready: {promoted}", flush=True)
    print(f"  Unpromoted             : {unpromoted}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
