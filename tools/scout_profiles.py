"""Scout municipality source entrances and update source profiles."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lcaios.html import LinkParser
from lcaios.http import BOOTSTRAP_USER_AGENT, CacheTier, HttpClient, RobotsDeniedError
from source_profiles.schema import validate_profile


def _clean_url(url: str, base: str) -> str | None:
    try:
        if url.startswith("//"):
            base_scheme = urllib.parse.urlsplit(base).scheme or "http"
            url = f"{base_scheme}:{url}"
        joined = urllib.parse.urljoin(base, url)
        parts = urllib.parse.urlsplit(joined)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return None
        return urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
        )
    except Exception:
        return None


def _is_same_or_subdomain(host1: str, host2: str) -> bool:
    h1 = host1.lower().removeprefix("www.")
    h2 = host2.lower().removeprefix("www.")
    return h1 == h2 or h1.endswith("." + h2) or h2.endswith("." + h1)


def probe_vendor_regulations(muni_name: str, slug: str, client: HttpClient) -> dict[str, Any] | None:
    """Safely probe major regulations vendor with strict municipality name verification."""
    clean_slug = slug.removeprefix("town-").removeprefix("vill-").removeprefix("city-")
    candidates = [
        ("g_reiki", f"https://www1.g-reiki.net/{clean_slug}/reiki_menu.html", f"https://www1.g-reiki.net/{clean_slug}/"),
        ("g_reiki", f"https://www1.g-reiki.net/town.{clean_slug}/reiki_menu.html", f"https://www1.g-reiki.net/town.{clean_slug}/"),
        ("g_reiki", f"https://www1.g-reiki.net/vill.{clean_slug}/reiki_menu.html", f"https://www1.g-reiki.net/vill.{clean_slug}/"),
    ]
    for adapter, check_url, base_url in candidates:
        try:
            res = client.fetch(check_url, tier=CacheTier.INDEX)
            text = res.text()
            if muni_name in text and any(k in text for k in ("例規", "条例", "規則")):
                return {
                    "status": "needs_review",
                    "adapter": adapter,
                    "base_url": base_url,
                    "verified_at": None,
                    "verified_by": None,
                    "evidence": [
                        {
                            "url": check_url,
                            "observed_on": check_url,
                            "fetched_at": res.fetched_at,
                        }
                    ],
                    "notes": f"Verified vendor regulations probe at {check_url} for {muni_name}",
                }
        except Exception:
            continue
    return None


def probe_vendor_minutes(muni_name: str, slug: str, client: HttpClient) -> dict[str, Any] | None:
    """Safely probe major minutes vendors with strict municipality name verification."""
    clean_slug = slug.removeprefix("town-").removeprefix("vill-").removeprefix("city-")
    candidates = [
        ("kaigiroku_net", f"https://ssp.kaigiroku.net/tenant/{clean_slug}/SpTop.html", f"https://ssp.kaigiroku.net/tenant/{clean_slug}/"),
        ("kaigiroku_net", f"https://ssp.kaigiroku.net/tenant/{clean_slug}/MinuteSearch.html", f"https://ssp.kaigiroku.net/tenant/{clean_slug}/"),
    ]
    for adapter, check_url, tenant_url in candidates:
        try:
            res = client.fetch(check_url, tier=CacheTier.INDEX)
            text = res.text()
            if muni_name in text and any(k in text for k in ("議会", "会議録", "議事録")):
                return {
                    "status": "needs_review",
                    "adapter": adapter,
                    "tenant_url": check_url,
                    "verified_at": None,
                    "verified_by": None,
                    "evidence": [
                        {
                            "url": check_url,
                            "observed_on": check_url,
                            "fetched_at": res.fetched_at,
                        }
                    ],
                    "notes": f"Verified vendor minutes probe at {check_url} for {muni_name}",
                }
        except Exception:
            continue
    return None


def _match_sources(
    all_observed_links: list[tuple[str, str, str]],
    page_evidences: dict[str, dict[str, str]],
    official_host: str,
    muni_name: str,
    slug: str,
    client: HttpClient,
    allow_probe: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Match regulations, minutes, budget, and settlement candidates from observed links."""
    def _make_evidence(target_url: str, observed_on_url: str) -> list[dict[str, Any]]:
        ev = {"url": target_url, "observed_on": observed_on_url}
        if observed_on_url in page_evidences:
            ev["fetched_at"] = page_evidences[observed_on_url]["fetched_at"]
        return [ev]

    # 1. Regulations
    reg_cand = None
    for url, label, observed_on in all_observed_links:
        u_low = url.lower()
        parts = urllib.parse.urlsplit(url)
        host = (parts.netloc or "").lower()
        path = parts.path.lower()

        if "g-reiki.net" in host or path.endswith("/reiki_menu.html") or "reiki_honbun" in u_low or "/reiki_int/" in u_low or "reiki.html" in path or "reiki.htm" in path:
            base_dir = url[: url.rfind("/") + 1] if "/" in url else url
            if not base_dir.endswith("/"):
                base_dir += "/"
            reg_cand = {
                "status": "needs_review",
                "adapter": "g_reiki",
                "base_url": base_dir,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed g_reiki at {url} (label: {label})",
            }
            break
        elif "d1-law.com" in host or "d1w_reiki" in u_low or "d1.lg.jp" in host or "d1w_reiki" in path:
            reg_cand = {
                "status": "needs_review",
                "adapter": "d1_law",
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed d1_law at {url} (label: {label})",
            }
            break
        elif "joureikun.jp" in host or "/joureikun/" in u_low:
            reg_cand = {
                "status": "needs_review",
                "adapter": "joureikun",
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed joureikun at {url} (label: {label})",
            }
            break
        elif (any(k in label for k in ("例規集", "例規データベース", "条例・規則", "条例集", "例規検索", "例規類集", "例規")) or "reiki" in path) and not reg_cand:
            adapter = "static" if _is_same_or_subdomain(host, official_host) else "official_document_index"
            reg_cand = {
                "status": "needs_review",
                "adapter": adapter,
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed regulations index at {url} (label: {label})",
            }

    if not reg_cand and allow_probe:
        reg_cand = probe_vendor_regulations(muni_name, slug, client)

    # 2. Minutes
    min_cand = None
    for url, label, observed_on in all_observed_links:
        parts = urllib.parse.urlsplit(url)
        host = (parts.netloc or "").lower()
        u_low = url.lower()

        if "kaigiroku.net" in host or "ssp.kaigiroku.net" in host or "/tenant/" in u_low:
            min_cand = {
                "status": "needs_review",
                "adapter": "kaigiroku_net",
                "tenant_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed kaigiroku_net at {url} (label: {label})",
            }
            break
        elif "gijiroku.com" in host or "voices" in host or "voices2" in host:
            min_cand = {
                "status": "needs_review",
                "adapter": "voices",
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed voices at {url} (label: {label})",
            }
            break
        elif "dbsr.jp" in host or "dbsr" in host:
            min_cand = {
                "status": "needs_review",
                "adapter": "dbsr",
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed dbsr at {url} (label: {label})",
            }
            break
        elif (any(k in label for k in ("会議録検索", "議事録検索", "本会議録", "本会議会議録", "議会会議録", "会議録の閲覧", "会議録", "定例会会議録", "会議録（速報版）", "会議録速報版", "議事録", "本会議議事録", "定例会議事録", "議会議事録", "議事録・採択一覧", "議事の記録")) or any(k in u_low for k in ("gijiroku", "kaigiroku", "/50400/", "/gikai/minutes", "/gikai/minute"))) and not min_cand:
            if "審議会" not in label and "委員会" not in label and "監査" not in label:
                min_cand = {
                    "status": "needs_review",
                    "adapter": "static",
                    "index_url": url,
                    "verified_at": None,
                    "verified_by": None,
                    "evidence": _make_evidence(url, observed_on),
                    "notes": f"Observed council minutes index at {url} (label: {label})",
                }

    if not min_cand and allow_probe:
        min_cand = probe_vendor_minutes(muni_name, slug, client)

    # 3. Budget & Settlement
    bud_cand = None
    set_cand = None
    for url, label, observed_on in all_observed_links:
        l_low = label.lower()
        u_low = url.lower()
        if (any(k in l_low for k in ("当初予算", "予算書", "予算の概要", "予算概要", "年度予算", "予算のあらまし", "予算", "今年の予算")) or "/yosan/" in u_low or "/40800/" in u_low) and not bud_cand:
            if "予算案" not in l_low and "パブリックコメント" not in l_low:
                bud_cand = {
                    "status": "needs_review",
                    "adapter": "official_document_index",
                    "index_url": url,
                    "verified_at": None,
                    "verified_by": None,
                    "evidence": _make_evidence(url, observed_on),
                    "notes": f"Observed budget link at {url} (label: {label})",
                }
        if (any(k in l_low for k in ("決算書", "決算の概要", "決算概要", "主要施策", "主要な施策", "決算カード", "年度決算", "決算実績報告", "決算")) or "/kessan/" in u_low or "/40800/" in u_low) and not set_cand:
            set_cand = {
                "status": "needs_review",
                "adapter": "official_document_index",
                "index_url": url,
                "verified_at": None,
                "verified_by": None,
                "evidence": _make_evidence(url, observed_on),
                "notes": f"Observed settlement link at {url} (label: {label})",
            }

    if not bud_cand or not set_cand:
        for url, label, observed_on in all_observed_links:
            l_low = label.lower()
            u_low = url.lower()
            if any(k in l_low for k in ("予算・決算", "予算決算", "財政状況", "行財政", "財政課", "財政公表", "財政事情", "財政の状況", "財政・財産", "財政・管財", "財政情報", "財政")) or "/zaisei/" in u_low or "/40800/" in u_low:
                if not bud_cand:
                    bud_cand = {
                        "status": "needs_review",
                        "adapter": "official_document_index",
                        "index_url": url,
                        "verified_at": None,
                        "verified_by": None,
                        "evidence": _make_evidence(url, observed_on),
                        "notes": f"Observed finance section at {url} (label: {label})",
                    }
                if not set_cand:
                    set_cand = {
                        "status": "needs_review",
                        "adapter": "official_document_index",
                        "index_url": url,
                        "verified_at": None,
                        "verified_by": None,
                        "evidence": _make_evidence(url, observed_on),
                        "notes": f"Observed finance section at {url} (label: {label})",
                    }
                break

    return reg_cand, min_cand, bud_cand, set_cand


def scout_municipality(
    profile_path: Path,
    client: HttpClient,
    max_pages: int = 12,
) -> dict[str, Any]:
    with profile_path.open("r", encoding="utf-8") as f:
        profile = json.load(f)

    home_url = profile["official_home_url"]
    home_parts = urllib.parse.urlsplit(home_url)
    official_host = (home_parts.netloc or "").lower()
    muni_name = profile["municipality"]
    slug = profile_path.stem.split("-", 1)[1] if "-" in profile_path.stem else profile_path.stem

    visited_urls: set[str] = set()
    to_visit: list[tuple[int, str, str]] = [(0, home_url, "official_home")]
    all_observed_links: list[tuple[str, str, str]] = []
    page_evidences: dict[str, dict[str, str]] = {}

    while to_visit and len(visited_urls) < max_pages:
        to_visit.sort(key=lambda x: x[0])
        _, current_url, current_label = to_visit.pop(0)

        if current_url in visited_urls:
            continue
        visited_urls.add(current_url)

        try:
            fetch_res = client.fetch(current_url, tier=CacheTier.INDEX)
            html_text = fetch_res.text()
            page_evidences[current_url] = {
                "sha256": fetch_res.sha256,
                "fetched_at": fetch_res.fetched_at,
            }
        except RobotsDeniedError:
            if current_url == home_url:
                for k in ("minutes", "regulations", "budget", "settlement"):
                    profile["sources"][k] = {
                        "status": "blocked",
                        "adapter": None,
                        "verified_at": None,
                        "verified_by": None,
                        "evidence": [{"url": current_url, "observed_on": current_url}],
                        "notes": "official_home robots.txt denied",
                    }
                errors = validate_profile(profile)
                if errors:
                    raise ValueError(f"Validation error for {profile_path}: {errors}")
                with profile_path.open("w", encoding="utf-8") as f:
                    json.dump(profile, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                return profile
            continue
        except Exception:
            continue

        parser = LinkParser()
        try:
            parser.feed(html_text)
        except Exception:
            pass

        for raw_href, raw_label in parser.links:
            clean = _clean_url(raw_href, current_url)
            if not clean:
                continue
            all_observed_links.append((clean, raw_label, current_url))

            c_lower = clean.lower()
            l_lower = raw_label.lower()
            p_parts = urllib.parse.urlsplit(clean)
            link_host = (p_parts.netloc or "").lower()

            if clean not in visited_urls and (_is_same_or_subdomain(link_host, official_host) or "gikai" in link_host):
                # Priority 0: Main administrative hub links (くらし・行政, default.html, index2.html)
                if any(k in c_lower for k in ("default", "index2", "home", "main", "top", "0000")) or any(k in l_lower for k in ("くらし・行政", "くらしの情報", "行政情報", "市政情報", "町政情報", "村政情報")):
                    to_visit.append((0, clean, raw_label or "admin_hub"))
                elif any(k in l_lower or k in c_lower for k in ("議会", "gikai", "shigikai", "chougikai", "songikai", "council", "assembly")):
                    to_visit.append((1, clean, raw_label))
                elif any(k in l_lower or k in c_lower for k in ("例規", "条例", "reiki")):
                    to_visit.append((2, clean, raw_label))
                elif any(k in l_lower or k in c_lower for k in ("財政", "予算", "決算", "zaisei", "yosan", "kessan")):
                    to_visit.append((2, clean, raw_label))
                elif any(k in l_lower or k in c_lower for k in ("まちづくり", "組織から探す", "市の行財政", "町の行財政")):
                    to_visit.append((3, clean, raw_label))
                elif any(k in l_lower or k in c_lower for k in ("サイトマップ", "sitemap")):
                    to_visit.append((4, clean, raw_label))

        # Early exit check: If all 4 are found from site links, stop exploring further pages immediately
        r_c, m_c, b_c, s_c = _match_sources(all_observed_links, page_evidences, official_host, muni_name, slug, client, allow_probe=False)
        if r_c and m_c and b_c and s_c:
            break

    # Final match with probe fallback
    reg_cand, min_cand, bud_cand, set_cand = _match_sources(
        all_observed_links, page_evidences, official_host, muni_name, slug, client, allow_probe=True
    )

    sources = profile.setdefault("sources", {})

    if reg_cand:
        sources["regulations"] = reg_cand
    elif sources.get("regulations", {}).get("status") == "not_evaluated":
        sources["regulations"]["status"] = "not_found"
        sources["regulations"]["notes"] = "No regulations entrance found within page limit"

    if min_cand:
        sources["minutes"] = min_cand
    elif sources.get("minutes", {}).get("status") == "not_evaluated":
        sources["minutes"]["status"] = "not_found"
        sources["minutes"]["notes"] = "No council minutes entrance found within page limit"

    if bud_cand:
        sources["budget"] = bud_cand
    elif sources.get("budget", {}).get("status") == "not_evaluated":
        sources["budget"]["status"] = "not_found"
        sources["budget"]["notes"] = "No budget entrance found within page limit"

    if set_cand:
        sources["settlement"] = set_cand
    elif sources.get("settlement", {}).get("status") == "not_evaluated":
        sources["settlement"]["status"] = "not_found"
        sources["settlement"]["notes"] = "No settlement entrance found within page limit"

    errors = validate_profile(profile)
    if errors:
        raise ValueError(f"Validation error for {profile_path}: {errors}")

    with profile_path.open("w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Scout municipality sources and update profiles.")
    parser.add_argument("--prefecture", help="Prefecture name, e.g. 福岡県")
    parser.add_argument("--prefecture-code", help="2-digit prefecture code, e.g. 40")
    parser.add_argument("--limit", type=int, help="Limit number of municipalities to scout")
    parser.add_argument("--cache-dir", default=".tasks/cache/scout", help="Cache directory")
    args = parser.parse_args()

    cache_path = Path(args.cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    client = HttpClient(cache_path, user_agent=BOOTSTRAP_USER_AGENT)

    profiles_root = REPO_ROOT / "source_profiles" / "municipalities"
    profile_paths: list[Path] = []

    for path in sorted(profiles_root.rglob("*.json")):
        if "41-saga" in str(path):
            continue
        if args.prefecture_code:
            if not path.parent.name.startswith(f"{args.prefecture_code}-"):
                continue
        if args.prefecture:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("prefecture") != args.prefecture:
                    continue
            except Exception:
                continue
        profile_paths.append(path)

    if args.limit:
        profile_paths = profile_paths[: args.limit]

    print(f"Scouting {len(profile_paths)} municipalities...", flush=True)
    success = 0
    errors = 0

    for i, path in enumerate(profile_paths, 1):
        try:
            data = scout_municipality(path, client)
            m_name = data.get("municipality")
            s = data.get("sources", {})
            min_st = s.get("minutes", {}).get("status")
            reg_st = s.get("regulations", {}).get("status")
            bud_st = s.get("budget", {}).get("status")
            set_st = s.get("settlement", {}).get("status")
            print(f"[{i}/{len(profile_paths)}] {m_name:8s} | min: {min_st:12s} | reg: {reg_st:12s} | bud: {bud_st:12s} | set: {set_st:12s}", flush=True)
            success += 1
        except Exception as exc:
            print(f"[{i}/{len(profile_paths)}] ERROR {path.name}: {exc}", file=sys.stderr, flush=True)
            errors += 1

    print(f"\nScout completed: {success} succeeded, {errors} errors.", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
