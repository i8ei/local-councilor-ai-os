"""Scaffold initial source profiles for all 1,741 municipalities."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bootstrap.municipalities.registry import load_registry
from source_profiles.schema import validate_profile

PREFECTURE_SLUGS: dict[str, str] = {
    "01": "hokkaido",
    "02": "aomori",
    "03": "iwate",
    "04": "miyagi",
    "05": "akita",
    "06": "yamagata",
    "07": "fukushima",
    "08": "ibaraki",
    "09": "tochigi",
    "10": "gunma",
    "11": "saitama",
    "12": "chiba",
    "13": "tokyo",
    "14": "kanagawa",
    "15": "niigata",
    "16": "toyama",
    "17": "ishikawa",
    "18": "fukui",
    "19": "yamanashi",
    "20": "nagano",
    "21": "gifu",
    "22": "shizuoka",
    "23": "aichi",
    "24": "mie",
    "25": "shiga",
    "26": "kyoto",
    "27": "osaka",
    "28": "hyogo",
    "29": "nara",
    "30": "wakayama",
    "31": "tottori",
    "32": "shimane",
    "33": "okayama",
    "34": "hiroshima",
    "35": "yamaguchi",
    "36": "tokushima",
    "37": "kagawa",
    "38": "ehime",
    "39": "kochi",
    "40": "fukuoka",
    "41": "saga",
    "42": "nagasaki",
    "43": "kumamoto",
    "44": "oita",
    "45": "miyazaki",
    "46": "kagoshima",
    "47": "okinawa",
}


def extract_slug(url: str, default: str = "muni") -> str:
    host = urlparse(url).netloc.lower()
    parts = host.split(".")
    for i, p in enumerate(parts):
        if p in ("city", "town", "vill", "villages", "village") and i + 1 < len(parts):
            return parts[i + 1]
    meaningful = [
        p
        for p in parts
        if p
        not in (
            "www",
            "www1",
            "www2",
            "city",
            "town",
            "vill",
            "village",
            "pref",
            "lg",
            "jp",
            "go",
            "hokkaido",
            "tokyo",
            "osaka",
            "kyoto",
        )
    ]
    if meaningful:
        return meaningful[0]
    return default


def make_initial_profile(row: dict[str, str]) -> dict:
    return {
        "schema_version": 1,
        "area_code_5": row["area_code_5"],
        "prefecture": row["prefecture_name"],
        "municipality": row["municipality_name"],
        "official_home_url": row["official_home_url"],
        "sources": {
            "minutes": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "regulations": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "budget": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
            "settlement": {
                "status": "not_evaluated",
                "adapter": None,
                "verified_at": None,
                "verified_by": None,
                "evidence": [],
                "notes": None,
            },
        },
    }


def scaffold_all(
    dest_root: Path = REPO_ROOT / "source_profiles" / "municipalities",
) -> tuple[int, int]:
    rows = load_registry()
    created = 0
    skipped = 0

    # Build index of existing area_code_5 across dest_root
    existing_area_codes: set[str] = set()
    if dest_root.exists():
        for path in dest_root.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "area_code_5" in data:
                    existing_area_codes.add(str(data["area_code_5"]))
            except Exception:
                pass

    for row in rows:
        area_code = row["area_code_5"]
        if area_code in existing_area_codes:
            skipped += 1
            continue

        pref_code = row["prefecture_code_2"]
        pref_slug = PREFECTURE_SLUGS.get(pref_code, f"pref{pref_code}")
        pref_dir = dest_root / f"{pref_code}-{pref_slug}"
        pref_dir.mkdir(parents=True, exist_ok=True)

        muni_slug = extract_slug(row["official_home_url"], default=area_code)
        file_path = pref_dir / f"{area_code}-{muni_slug}.json"

        profile_data = make_initial_profile(row)
        errors = validate_profile(profile_data)
        if errors:
            raise ValueError(
                f"Generated invalid profile for {row['municipality_name']}: {errors}"
            )

        file_path.write_text(
            json.dumps(profile_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created += 1

    return created, skipped


if __name__ == "__main__":
    c, s = scaffold_all()
    print(
        f"Scaffolding complete: {c} created, {s} skipped (existing). Total: {c + s}"
    )
