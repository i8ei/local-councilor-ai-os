"""Block runner for source_profiles verification across municipalities."""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source_profiles.cli import _cmd_verify
from source_profiles.verify import _PROMOTABLE_PRIOR_STATUSES

BLOCKS: dict[str, list[str]] = {
    "kyushu_okinawa": ["40", "42", "43", "44", "45", "46", "47"],
    "chugoku_shikoku": ["31", "32", "33", "34", "35", "36", "37", "38", "39"],
    "kinki": ["24", "25", "26", "27", "28", "29", "30"],
    "chubu": ["15", "16", "17", "18", "19", "20", "21", "22", "23"],
    "kanto": ["08", "09", "10", "11", "12", "13", "14"],
    "tohoku_hokkaido": ["01", "02", "03", "04", "05", "06", "07"],
}

KINDS: tuple[str, ...] = ("regulations", "minutes", "budget", "settlement")


class ThreadLocalStdout(io.TextIOBase):
    """Stdout wrapper routing output to a thread-local sink if set."""

    def __init__(self, real_stdout: Any) -> None:
        self._real_stdout = real_stdout
        self._local = threading.local()

    def set_sink(self, sink: io.StringIO | None) -> None:
        self._local.sink = sink

    def clear_sink(self) -> None:
        self._local.sink = None

    def write(self, s: str) -> int:
        sink = getattr(self._local, "sink", None)
        if sink is not None:
            return sink.write(s)
        return self._real_stdout.write(s)

    def flush(self) -> None:
        sink = getattr(self._local, "sink", None)
        if sink is not None:
            sink.flush()
        else:
            self._real_stdout.flush()

    def isatty(self) -> bool:
        return getattr(self._real_stdout, "isatty", lambda: False)()


def find_profile_paths(
    pref_codes: list[str], profiles_dir: Path | None = None
) -> list[Path]:
    """Find all municipality profile JSON files for the given prefecture codes."""
    root = (
        profiles_dir
        if profiles_dir is not None
        else REPO_ROOT / "source_profiles" / "municipalities"
    )
    if not root.exists():
        return []
    paths: list[Path] = []
    for pref_code in pref_codes:
        # Check standard hierarchical layout: <root>/<pref_code>-<name>/*.json
        found = sorted(root.glob(f"{pref_code}-*/*.json"))
        if not found:
            # Check flat layout: <root>/<pref_code>*.json
            found = sorted(root.glob(f"{pref_code}*.json"))
        paths.extend(found)
    return paths


def collect_promotable_tasks(
    profile_paths: list[Path],
    already_completed: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Scan profile paths and return list of (municipality, kind) tasks to verify.

    Only entries with status in _PROMOTABLE_PRIOR_STATUSES are included.
    Already completed pairs are skipped.
    """
    tasks: list[dict[str, Any]] = []
    for path in profile_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        municipality = data.get("municipality")
        prefecture = data.get("prefecture")
        if not municipality:
            continue
        sources = data.get("sources", {})
        if not isinstance(sources, dict):
            continue
        for kind in KINDS:
            entry = sources.get(kind)
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if status not in _PROMOTABLE_PRIOR_STATUSES:
                continue
            if (municipality, kind) in already_completed:
                continue
            tasks.append(
                {
                    "path": path,
                    "municipality": municipality,
                    "prefecture": prefecture,
                    "kind": kind,
                    "status_before": status,
                    "adapter": entry.get("adapter"),
                }
            )
    return tasks


def group_tasks_by_municipality(
    tasks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group tasks by profile path so that each municipality is handled sequentially by one worker."""
    groups: dict[Path, list[dict[str, Any]]] = {}
    for task in tasks:
        p = task["path"]
        if p not in groups:
            groups[p] = []
        groups[p].append(task)
    return list(groups.values())


def verify_task(
    task: dict[str, Any],
    cache_dir: str,
    offline: bool,
    profiles_dir: str | None,
    thread_stdout: ThreadLocalStdout,
) -> dict[str, Any]:
    """Execute _cmd_verify for a single task and return the resulting report dict."""
    municipality = task["municipality"]
    prefecture = task["prefecture"]
    kind = task["kind"]
    status_before = task["status_before"]

    buf = io.StringIO()
    thread_stdout.set_sink(buf)
    exit_code = 2
    try:
        ns = argparse.Namespace(
            kind=kind,
            municipality=municipality,
            prefecture=prefecture,
            cache_dir=cache_dir,
            offline=offline,
            profiles_dir=profiles_dir,
        )
        exit_code = _cmd_verify(ns)
    except Exception as exc:
        thread_stdout.clear_sink()
        return {
            "municipality": municipality,
            "prefecture": prefecture,
            "kind": kind,
            "status_before": status_before,
            "status_after": status_before,
            "result": "failed",
            "reason": f"exception in _cmd_verify: {exc}",
        }
    finally:
        thread_stdout.clear_sink()

    raw_output = buf.getvalue().strip()
    try:
        report = json.loads(raw_output)
        if not isinstance(report, dict):
            raise ValueError("Report is not a JSON object")
    except Exception:
        report = {
            "municipality": municipality,
            "prefecture": prefecture,
            "kind": kind,
            "status_before": status_before,
            "status_after": status_before,
            "result": "failed" if exit_code != 0 else "verified",
            "reason": f"invalid json output from verify: {raw_output!r}",
        }

    # Ensure required report fields are present
    if "municipality" not in report or not report["municipality"]:
        report["municipality"] = municipality
    if "prefecture" not in report or not report["prefecture"]:
        report["prefecture"] = prefecture
    if "kind" not in report or not report["kind"]:
        report["kind"] = kind
    if "status_before" not in report:
        report["status_before"] = status_before
    if "status_after" not in report:
        report["status_after"] = report.get("status", status_before)
    if "result" not in report:
        report["result"] = "failed" if exit_code != 0 else "verified"
    if "reason" not in report:
        report["reason"] = ""

    return report


def verify_municipality_worker(
    muni_tasks: list[dict[str, Any]],
    cache_dir: str,
    offline: bool,
    profiles_dir: str | None,
    thread_stdout: ThreadLocalStdout,
    report_path: Path,
    report_lock: threading.Lock,
    progress_cb: Any,
) -> list[dict[str, Any]]:
    """Worker function processing all tasks for a single municipality sequentially."""
    records: list[dict[str, Any]] = []
    for task in muni_tasks:
        rec = verify_task(
            task=task,
            cache_dir=cache_dir,
            offline=offline,
            profiles_dir=profiles_dir,
            thread_stdout=thread_stdout,
        )
        records.append(rec)
        with report_lock:
            with report_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
        if progress_cb is not None:
            progress_cb(rec)
    return records


def print_summary(
    block_or_pref: str,
    new_records: list[dict[str, Any]],
    existing_records: list[dict[str, Any]],
) -> None:
    """Print the final verification summary."""
    all_records = existing_records + new_records
    print(f"\n=== Verification Summary ({block_or_pref}) ===", flush=True)
    print(
        f"Total pairs in report: {len(all_records)} "
        f"(This run: {len(new_records)}, Previous runs: {len(existing_records)})",
        flush=True,
    )

    status_counts = Counter(
        rec.get("status_after") or "unknown" for rec in all_records
    )
    print("\nBreakdown by status_after:", flush=True)
    for st, count in sorted(status_counts.items()):
        print(f"  {st:15s}: {count:4d}", flush=True)

    result_counts = Counter(rec.get("result") or "unknown" for rec in all_records)
    print("\nBreakdown by result:", flush=True)
    for res, count in sorted(result_counts.items()):
        print(f"  {res:15s}: {count:4d}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run source_profiles verify in bulk across regional blocks or prefectures."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--block", choices=list(BLOCKS.keys()), help="Regional block name"
    )
    group.add_argument(
        "--prefecture-code",
        help="Two-digit prefecture code (e.g. 40, 41, 01)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent municipality workers (default: 8)",
    )
    parser.add_argument(
        "--cache-dir",
        default=".tasks/cache/verify",
        help="Cache directory for HttpClient (default: .tasks/cache/verify)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cached responses only",
    )
    parser.add_argument(
        "--report",
        help="Path to JSONL report file (default: tmp/verify-report-<block_or_pref>.jsonl)",
    )
    parser.add_argument(
        "--profiles-dir",
        help="Override municipalities root directory (for testing)",
    )
    return parser


def run_verify_block(
    args: argparse.Namespace,
    thread_stdout: ThreadLocalStdout | None = None,
) -> int:
    real_stdout = sys.stdout
    if thread_stdout is None:
        thread_stdout = ThreadLocalStdout(real_stdout)
    sys.stdout = thread_stdout
    try:
        if args.prefecture_code:
            pref_code = args.prefecture_code.strip().zfill(2)
            pref_codes = [pref_code]
            block_label = f"pref{pref_code}"
        else:
            pref_codes = BLOCKS[args.block]
            block_label = args.block

        cache_path = Path(args.cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)

        if args.report:
            report_path = Path(args.report)
        else:
            report_path = REPO_ROOT / "tmp" / f"verify-report-{block_label}.jsonl"
        report_path.parent.mkdir(parents=True, exist_ok=True)

        already_completed: set[tuple[str, str]] = set()
        existing_records: list[dict[str, Any]] = []
        if report_path.exists():
            with report_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        muni = rec.get("municipality")
                        kind = rec.get("kind")
                        if muni and kind:
                            already_completed.add((muni, kind))
                            existing_records.append(rec)
                    except Exception:
                        pass

        profiles_dir = Path(args.profiles_dir) if args.profiles_dir else None
        profile_paths = find_profile_paths(pref_codes, profiles_dir=profiles_dir)
        pending_tasks = collect_promotable_tasks(
            profile_paths, already_completed=already_completed
        )

        print(
            f"=== Verify Block: {block_label} (Prefectures: {','.join(pref_codes)}) ===",
            flush=True,
        )
        print(f"Report file: {report_path}", flush=True)
        print(
            f"Found {len(profile_paths)} municipality profiles, "
            f"{len(pending_tasks)} pending pairs to verify "
            f"(already completed: {len(existing_records)}, concurrency: {args.concurrency})",
            flush=True,
        )

        if not pending_tasks:
            print("No pending promotable pairs to verify.", flush=True)
            print_summary(block_label, [], existing_records)
            return 0

        muni_groups = group_tasks_by_municipality(pending_tasks)
        total_pairs = len(pending_tasks)
        completed_count = 0
        print_lock = threading.Lock()
        report_lock = threading.Lock()
        new_records: list[dict[str, Any]] = []

        def on_pair_done(record: dict[str, Any]) -> None:
            nonlocal completed_count
            with print_lock:
                completed_count += 1
                muni = record.get("municipality", "-")
                kind = record.get("kind", "-")
                st_before = record.get("status_before", "-")
                st_after = record.get("status_after", "-")
                res = record.get("result", "unknown")
                reason = record.get("reason", "")

                if st_before != st_after:
                    transition = f"{st_before} -> {st_after}"
                else:
                    transition = f"{st_after}"

                short_reason = (
                    f" ({reason[:60]}...)"
                    if len(reason) > 60
                    else (f" ({reason})" if reason and res != "verified" else "")
                )
                print(
                    f"[{completed_count:4d}/{total_pairs:4d}] {muni:8s} | {kind:11s} | {transition:24s} | {res}{short_reason}",
                    flush=True,
                )

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    verify_municipality_worker,
                    muni_tasks=group,
                    cache_dir=str(cache_path),
                    offline=args.offline,
                    profiles_dir=args.profiles_dir,
                    thread_stdout=thread_stdout,
                    report_path=report_path,
                    report_lock=report_lock,
                    progress_cb=on_pair_done,
                )
                for group in muni_groups
            ]
            for future in as_completed(futures):
                try:
                    records = future.result()
                    new_records.extend(records)
                except Exception as exc:
                    with print_lock:
                        print(
                            f"Worker exception: {exc}", file=sys.stderr, flush=True
                        )

        print_summary(block_label, new_records, existing_records)
        return 0
    finally:
        sys.stdout = real_stdout


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_verify_block(args)


if __name__ == "__main__":
    sys.exit(main())
