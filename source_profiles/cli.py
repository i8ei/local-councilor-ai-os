"""CLI for source profiles: validate and ingest-command."""

from __future__ import annotations  # noqa: I001

import argparse
import datetime
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any

from lcaios.http import HttpClient, REGULATIONS_USER_AGENT

from source_profiles.schema import (
    validate_profile,  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
)
from source_profiles.verify import (
    verify_profile,  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
)

PACKAGE_ROOT = Path(__file__).resolve().parent
MUNICIPALITIES_ROOT = PACKAGE_ROOT / "municipalities"


def _effective_municipalities_root(profiles_dir: str | None) -> Path:
    if profiles_dir:
        return Path(profiles_dir)
    env = os.environ.get("SOURCE_PROFILES_DIR") or os.environ.get(
        "SOURCE_PROFILES_MUNICIPALITIES_ROOT"
    )
    if env:
        return Path(env)
    return MUNICIPALITIES_ROOT


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFC", name).replace("\u3000", " ").strip()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load {path}: {exc}") from exc


def _find_profiles(
    prefecture: str | None = None, base_dir: str | Path | None = None
) -> list[Path]:
    root = _effective_municipalities_root(str(base_dir) if base_dir else None)
    if not root.exists():
        return []
    results: list[Path] = []
    # Support both structures: root is municipalities/ or root/41-saga/ or flat temp dir
    # Collect all *.json recursively, then filter by prefecture if needed
    for json_file in root.rglob("*.json"):
        # Skip non-profile json? Keep all, validate will filter
        if prefecture is not None:
            try:
                data = _load_json(json_file)
                if data.get("prefecture") != prefecture:
                    continue
            except Exception:
                # If cannot load, still include to report error
                pass
        results.append(json_file)
    results.sort()
    return results


def _cmd_validate(args: argparse.Namespace) -> int:
    profiles: list[Path] = []
    profiles_dir = getattr(args, "profiles_dir", None)
    if args.profile:
        p = Path(args.profile)
        if not p.exists():
            not_found_report: dict[str, Any] = {
                "status": "error",
                "errors": [f"profile not found: {p}"],
            }
            print(json.dumps(not_found_report, ensure_ascii=False, indent=2))
            return 2
        profiles = [p]
    elif args.all:
        profiles = _find_profiles(args.prefecture, base_dir=profiles_dir)
        if not profiles:
            # If --prefecture given and no profiles, report
            if args.prefecture:
                report_err: dict[str, Any] = {
                    "status": "error",
                    "errors": [f"no profiles found for prefecture {args.prefecture}"],
                    "profile_count": 0,
                }
                print(json.dumps(report_err, ensure_ascii=False, indent=2))
                return 2
    else:
        print(
            json.dumps(
                {"status": "error", "errors": ["specify --profile or --all"]},
                ensure_ascii=False,
            )
        )
        return 2

    results: list[dict[str, Any]] = []
    error_count = 0
    for path in profiles:
        try:
            data = _load_json(path)
        except Exception as exc:
            results.append(
                {
                    "profile": str(path),
                    "status": "error",
                    "errors": [f"failed to load JSON: {exc}"],
                }
            )
            error_count += 1
            continue
        errs = validate_profile(data)
        status = "ok" if not errs else "error"
        if errs:
            error_count += 1
        results.append({"profile": str(path), "status": status, "errors": errs})

    final_report: dict[str, Any] = {
        "status": "ok" if error_count == 0 else "error",
        "profile_count": len(profiles),
        "error_count": error_count,
        "results": results,
    }
    print(json.dumps(final_report, ensure_ascii=False, indent=2))
    return 0 if error_count == 0 else 2


def _resolve_profile_by_municipality(
    municipality: str,
    prefecture: str | None,
    base_dir: str | Path | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    norm_muni = _normalize(municipality)
    norm_pref = _normalize(prefecture) if prefecture else None
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in _find_profiles(None, base_dir=base_dir):
        try:
            data = _load_json(path)
        except Exception:
            continue
        if _normalize(str(data.get("municipality", ""))) != norm_muni:
            continue
        if (
            norm_pref is not None
            and _normalize(str(data.get("prefecture", ""))) != norm_pref
        ):
            continue
        candidates.append((path, data))
    if not candidates:
        return None
    # If multiple and prefecture not specified, require disambiguation
    if len(candidates) > 1 and prefecture is None:
        return None
    # Prefer exact prefecture match if provided
    if norm_pref is not None:
        # filter already done; if multiple with same name/pref, take first sorted
        candidates.sort(key=lambda x: str(x[0]))
        return candidates[0]
    candidates.sort(key=lambda x: str(x[0]))
    return candidates[0]


def _cmd_ingest_command(args: argparse.Namespace) -> int:
    kind: str = args.kind
    limit: int = args.limit
    municipality: str = args.municipality
    prefecture: str | None = args.prefecture
    profiles_dir = getattr(args, "profiles_dir", None)

    resolved = _resolve_profile_by_municipality(
        municipality, prefecture, base_dir=profiles_dir
    )
    if resolved is None:
        # Try to provide helpful error
        print(
            f"municipality not found: {municipality!r} prefecture={prefecture!r}",
            file=sys.stderr,
        )
        return 2
    path, data = resolved
    sources = data.get("sources", {})
    entry = sources.get(kind)
    if entry is None:
        print(f"kind {kind!r} not found in profile {path}", file=sys.stderr)
        return 2

    status = entry.get("status")
    adapter = entry.get("adapter")
    area = data.get("area_code_5", "unknown")
    muni_name = data.get("municipality", municipality)

    # Supported case: g_reiki with ready/needs_review
    if adapter == "g_reiki" and status in {"ready", "needs_review"}:
        base_url = entry.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            print(f"profile {path} g_reiki missing base_url", file=sys.stderr)
            return 2
        # Ensure base_url ends with /
        if not base_url.endswith("/"):
            base_url = base_url + "/"
        db_path = f"/tmp/{area}-reg.db"
        source_name = f"{muni_name}例規集"
        cmd = f'python3 modules/regulations/vendor_greiki.py --base-url {base_url} --db {db_path} --source-name "{source_name}" --limit {limit}'
        if status == "needs_review":
            print("# NEEDS LIVE VERIFICATION")
        print(cmd)
        return 0

    # Unsupported path
    reason_parts: list[str] = []
    if adapter is None:
        reason_parts.append("adapter is null (no supported ingestion method)")
    elif adapter in {"d1_law", "joureikun", "dbsr", "voices"}:
        reason_parts.append(f"adapter {adapter} is not supported by vendor_greiki")
    else:
        reason_parts.append(
            f"adapter {adapter!r} with status {status!r} cannot be ingested via g_reiki"
        )

    reason = "; ".join(reason_parts)
    # Next action guidance
    if adapter in {"d1_law", "joureikun"}:
        action = "requires a dedicated adapter (not yet implemented); manual collection or new vendor module is needed"
    elif adapter is None and status == "needs_review":
        action = "requires manual browser verification; run `python3 -m source_profiles.cli validate --profile {}` then verify entry URL".format(
            path
        )
    elif status == "not_evaluated":
        action = "profile not evaluated; verify and set status/adapter first"
    else:
        action = "check profile status/adapter and update verified_at/evidence before ingestion"

    print(f"cannot generate ingest command: {reason}", file=sys.stderr)
    print(f"next action: {action}", file=sys.stderr)
    print(
        f"profile: {path} kind={kind} status={status} adapter={adapter}",
        file=sys.stderr,
    )
    return 2


def _cmd_verify(args: argparse.Namespace) -> int:
    kind: str = args.kind
    municipality: str = args.municipality
    prefecture: str | None = args.prefecture
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    offline: bool = bool(args.offline)
    profiles_dir = getattr(args, "profiles_dir", None)

    resolved = _resolve_profile_by_municipality(
        municipality, prefecture, base_dir=profiles_dir
    )
    if resolved is None:
        report: dict[str, Any] = {
            "municipality": municipality,
            "kind": kind,
            "adapter": None,
            "result": "failed",
            "reason": f"municipality not found: {municipality!r} prefecture={prefecture!r}",
            "status_before": None,
            "status_after": None,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    path, data = resolved
    sources = data.get("sources", {})
    entry = sources.get(kind) if isinstance(sources, dict) else None
    adapter = entry.get("adapter") if isinstance(entry, dict) else None
    status_before = entry.get("status") if isinstance(entry, dict) else None

    # Only g_reiki regulations is supported for verify
    if adapter != "g_reiki" or kind != "regulations":
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"verify unsupported for adapter {adapter!r} kind {kind!r}",
            "status_before": status_before,
            "status_after": status_before,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    if cache_dir is None:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": "--cache-dir is required",
            "status_before": status_before,
            "status_after": status_before,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        client = HttpClient(
            cache_dir,
            user_agent=REGULATIONS_USER_AGENT,
            offline=offline,
            timeout=90,
        )
    except Exception as exc:
        report = {
            "municipality": municipality,
            "kind": kind,
            "adapter": adapter,
            "result": "failed",
            "reason": f"HttpClient init failed: {exc}",
            "status_before": status_before,
            "status_after": status_before,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    now = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    updated, v_report = verify_profile(data, client=client, now=now, kind=kind)

    # If verified, persist to disk
    if v_report.get("result") == "verified":
        try:
            # Write atomically via temp file
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(path)
        except Exception as exc:
            err_report = {
                "municipality": municipality,
                "kind": kind,
                "adapter": adapter,
                "result": "failed",
                "reason": f"failed to save profile: {exc}",
                "status_before": status_before,
                "status_after": status_before,
            }
            print(json.dumps(err_report, ensure_ascii=False, indent=2))
            return 2

    print(json.dumps(v_report, ensure_ascii=False, indent=2))
    return 0 if v_report.get("result") == "verified" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="source_profiles.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Validate source profiles")
    g = p_validate.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", help="Path to a single profile JSON")
    g.add_argument("--all", action="store_true", help="Validate all profiles")
    p_validate.add_argument("--prefecture", help="Filter by prefecture (with --all)")
    p_validate.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    p_ingest = sub.add_parser(
        "ingest-command", help="Generate ingestion command for a municipality"
    )
    p_ingest.add_argument(
        "--municipality", required=True, help="Municipality name (e.g. 太良町)"
    )
    p_ingest.add_argument("--prefecture", help="Prefecture name (e.g. 佐賀県)")
    p_ingest.add_argument(
        "--kind",
        required=True,
        choices=["regulations", "minutes", "budget", "settlement"],
        help="Source kind",
    )
    p_ingest.add_argument(
        "--limit", type=int, default=3, help="Limit for ingestion (default 3)"
    )
    p_ingest.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    p_verify = sub.add_parser(
        "verify", help="Verify a source entry live via HttpClient"
    )
    p_verify.add_argument(
        "--municipality", required=True, help="Municipality name (e.g. 太良町)"
    )
    p_verify.add_argument("--prefecture", help="Prefecture name (e.g. 佐賀県)")
    p_verify.add_argument(
        "--kind",
        required=True,
        choices=["regulations", "minutes", "budget", "settlement"],
        help="Source kind",
    )
    p_verify.add_argument(
        "--cache-dir", required=True, help="Cache directory for HttpClient"
    )
    p_verify.add_argument(
        "--offline", action="store_true", help="Use cached responses only"
    )
    p_verify.add_argument(
        "--profiles-dir", help="Override municipalities root directory (for testing)"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "ingest-command":
        # validate limit
        if args.limit < 1:
            print("--limit must be at least 1", file=sys.stderr)
            return 2
        return _cmd_ingest_command(args)
    if args.command == "verify":
        return _cmd_verify(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
