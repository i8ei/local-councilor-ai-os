"""Unit tests for tools/run_verify_block.py."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source_profiles.verify import _PROMOTABLE_PRIOR_STATUSES
from tools.run_verify_block import (
    KINDS,
    ThreadLocalStdout,
    build_parser,
    collect_promotable_tasks,
    find_profile_paths,
    group_tasks_by_municipality,
    print_summary,
    run_verify_block,
    verify_task,
)


def _make_synthetic_profile(
    municipality: str,
    prefecture: str,
    statuses: dict[str, str],
    area_code: str = "00000",
) -> dict:
    sources = {}
    for kind in KINDS:
        st = statuses.get(kind, "not_evaluated")
        sources[kind] = {
            "status": st,
            "adapter": "g_reiki" if kind == "regulations" else "static",
            "verified_at": "2026-01-01T00:00:00Z" if st == "ready" else None,
            "verified_by": "tester" if st == "ready" else None,
            "evidence": [],
            "notes": None,
        }
    return {
        "schema_version": 1,
        "area_code_5": area_code,
        "prefecture": prefecture,
        "municipality": municipality,
        "official_home_url": "https://example.com/",
        "sources": sources,
    }


class RunVerifyBlockTests(unittest.TestCase):
    def test_parser_block_and_prefecture_options(self):
        parser = build_parser()

        # Block choice
        args = parser.parse_args(["--block", "kyushu_okinawa"])
        self.assertEqual(args.block, "kyushu_okinawa")
        self.assertIsNone(args.prefecture_code)
        self.assertEqual(args.concurrency, 8)
        self.assertFalse(args.offline)

        # Prefecture code
        args = parser.parse_args(["--prefecture-code", "40", "--concurrency", "4", "--offline"])
        self.assertEqual(args.prefecture_code, "40")
        self.assertIsNone(args.block)
        self.assertEqual(args.concurrency, 4)
        self.assertTrue(args.offline)

        # Mutually exclusive error when both provided
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args(["--block", "kanto", "--prefecture-code", "13"])

        # Error when neither provided
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args([])

    def test_find_profile_paths_nested_and_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            # Nested layout: 40-fukuoka/40100.json
            pref_dir = tmp_root / "40-fukuoka"
            pref_dir.mkdir()
            (pref_dir / "40100.json").write_text("{}", encoding="utf-8")
            (pref_dir / "40101.json").write_text("{}", encoding="utf-8")

            # Flat layout: 41001.json
            (tmp_root / "41001.json").write_text("{}", encoding="utf-8")

            nested_found = find_profile_paths(["40"], profiles_dir=tmp_root)
            self.assertEqual(len(nested_found), 2)

            flat_found = find_profile_paths(["41"], profiles_dir=tmp_root)
            self.assertEqual(len(flat_found), 1)

            none_found = find_profile_paths(["99"], profiles_dir=tmp_root)
            self.assertEqual(len(none_found), 0)

    def test_collect_promotable_tasks_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            profile_data = _make_synthetic_profile(
                municipality="テスト町",
                prefecture="福岡県",
                statuses={
                    "regulations": "needs_review",   # promotable
                    "minutes": "not_evaluated",      # promotable
                    "budget": "ready",               # non-promotable (must skip)
                    "settlement": "unsupported",     # non-promotable (must skip)
                },
            )
            p_path = tmp_root / "40001-test.json"
            p_path.write_text(json.dumps(profile_data), encoding="utf-8")

            # Without already_completed
            tasks = collect_promotable_tasks([p_path], already_completed=set())
            self.assertEqual(len(tasks), 2)
            kinds = {t["kind"] for t in tasks}
            self.assertEqual(kinds, {"regulations", "minutes"})

            # With regulations already completed
            tasks2 = collect_promotable_tasks([p_path], already_completed={("テスト町", "regulations")})
            self.assertEqual(len(tasks2), 1)
            self.assertEqual(tasks2[0]["kind"], "minutes")

    def test_saga_verified_entries_are_never_promotable(self):
        """Hard requirement: entries already carrying a verified verdict in
        41-saga (`ready`, `document_confirmed`) are skipped by
        collect_promotable_tasks -- a machine run must never re-grant them."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        saga_dir = repo_root / "source_profiles" / "municipalities" / "41-saga"
        if not saga_dir.exists():
            self.skipTest("41-saga directory not found")

        saga_files = sorted(saga_dir.glob("*.json"))
        self.assertGreater(len(saga_files), 0)

        # Count verified entries in saga on disk. The exact number moves as
        # municipalities are re-verified, so assert a floor (a mass silent
        # downgrade would trip it) rather than a brittle constant.
        verified_count = 0
        for f in saga_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            for _k, entry in data.get("sources", {}).items():
                if entry.get("status") in ("ready", "document_confirmed"):
                    verified_count += 1
        self.assertGreaterEqual(
            verified_count, 31, "41-saga lost verified entries; investigate before re-running"
        )

        # Collect promotable tasks
        tasks = collect_promotable_tasks(saga_files, already_completed=set())
        for task in tasks:
            self.assertIn(task["status_before"], _PROMOTABLE_PRIOR_STATUSES)
            for protected in (
                "ready",
                "document_confirmed",
                "blocked",
                "unsupported",
                "not_found",
            ):
                self.assertNotEqual(task["status_before"], protected)

    def test_group_tasks_by_municipality(self):
        p1 = Path("/tmp/muni1.json")
        p2 = Path("/tmp/muni2.json")
        tasks = [
            {"path": p1, "municipality": "M1", "kind": "regulations"},
            {"path": p1, "municipality": "M1", "kind": "minutes"},
            {"path": p2, "municipality": "M2", "kind": "budget"},
        ]
        groups = group_tasks_by_municipality(tasks)
        self.assertEqual(len(groups), 2)
        group_m1 = [g for g in groups if g[0]["municipality"] == "M1"][0]
        self.assertEqual(len(group_m1), 2)

    def test_thread_local_stdout_isolation(self):
        real_buf = io.StringIO()
        tls = ThreadLocalStdout(real_buf)

        def worker_sink():
            local_buf = io.StringIO()
            tls.set_sink(local_buf)
            print("worker message", file=tls)
            tls.clear_sink()
            return local_buf.getvalue()

        # Write to real buffer
        print("main message", file=tls)
        worker_out = worker_sink()
        print("second main message", file=tls)

        self.assertEqual(worker_out.strip(), "worker message")
        self.assertIn("main message", real_buf.getvalue())
        self.assertIn("second main message", real_buf.getvalue())
        self.assertNotIn("worker message", real_buf.getvalue())

    def test_verify_task_success(self):
        task = {
            "path": Path("/dummy/40001.json"),
            "municipality": "福岡市",
            "prefecture": "福岡県",
            "kind": "regulations",
            "status_before": "needs_review",
            "adapter": "g_reiki",
        }
        tls = ThreadLocalStdout(io.StringIO())

        mock_v_report = {
            "municipality": "福岡市",
            "kind": "regulations",
            "adapter": "g_reiki",
            "result": "verified",
            "reason": "ok",
            "status_before": "needs_review",
            "status_after": "ready",
        }

        def fake_cmd_verify(ns):
            print(json.dumps(mock_v_report))
            return 0

        with mock.patch("sys.stdout", tls):
            with mock.patch("tools.run_verify_block._cmd_verify", side_effect=fake_cmd_verify):
                report = verify_task(
                    task,
                    cache_dir="/tmp/cache",
                    offline=True,
                    profiles_dir=None,
                    thread_stdout=tls,
                )

        self.assertEqual(report["result"], "verified")
        self.assertEqual(report["status_after"], "ready")
        self.assertEqual(report["status_before"], "needs_review")
        self.assertEqual(report["prefecture"], "福岡県")

    def test_verify_task_exception_handling(self):
        task = {
            "path": Path("/dummy/40001.json"),
            "municipality": "福岡市",
            "prefecture": "福岡県",
            "kind": "regulations",
            "status_before": "needs_review",
            "adapter": "g_reiki",
        }
        tls = ThreadLocalStdout(io.StringIO())

        with mock.patch("tools.run_verify_block._cmd_verify", side_effect=RuntimeError("connection dropped")):
            report = verify_task(
                task,
                cache_dir="/tmp/cache",
                offline=True,
                profiles_dir=None,
                thread_stdout=tls,
            )

        self.assertEqual(report["result"], "failed")
        self.assertIn("connection dropped", report["reason"])
        self.assertEqual(report["status_after"], "needs_review")

    def test_run_verify_block_full_flow_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            profiles_dir = tmp_root / "profiles"
            pref_dir = profiles_dir / "40-fukuoka"
            pref_dir.mkdir(parents=True)
            cache_dir = tmp_root / "cache"
            report_file = tmp_root / "report.jsonl"

            # Create 2 municipality profiles
            # m1 has 2 promotable (reg: needs_review, min: needs_review) and 2 non-promotable (bud: ready, set: unsupported)
            m1 = _make_synthetic_profile(
                municipality="自治体A",
                prefecture="福岡県",
                statuses={
                    "regulations": "needs_review",
                    "minutes": "needs_review",
                    "budget": "ready",
                    "settlement": "unsupported",
                },
                area_code="40001",
            )
            # m2 has 1 promotable (bud: needs_review) and 3 non-promotable (reg: ready, min: blocked, set: ready)
            m2 = _make_synthetic_profile(
                municipality="自治体B",
                prefecture="福岡県",
                statuses={
                    "regulations": "ready",
                    "minutes": "blocked",
                    "budget": "needs_review",
                    "settlement": "ready",
                },
                area_code="40002",
            )
            (pref_dir / "40001-a.json").write_text(json.dumps(m1), encoding="utf-8")
            (pref_dir / "40002-b.json").write_text(json.dumps(m2), encoding="utf-8")

            # Total promotable pairs = 3 (m1: reg, min; m2: bud)
            def fake_cmd_verify(ns):
                v_report = {
                    "municipality": ns.municipality,
                    "kind": ns.kind,
                    "result": "verified",
                    "reason": "ok",
                    "status_before": "needs_review",
                    "status_after": "ready",
                }
                print(json.dumps(v_report))
                return 0

            with mock.patch("tools.run_verify_block._cmd_verify", side_effect=fake_cmd_verify):
                args = argparse.Namespace(
                    block=None,
                    prefecture_code="40",
                    concurrency=2,
                    cache_dir=str(cache_dir),
                    offline=True,
                    report=str(report_file),
                    profiles_dir=str(profiles_dir),
                )
                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    exit_code = run_verify_block(args)

                self.assertEqual(exit_code, 0)
                self.assertTrue(report_file.exists())

                lines = [json.loads(line) for line in report_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assertEqual(len(lines), 3)
                pairs_recorded = {(rec["municipality"], rec["kind"]) for rec in lines}
                self.assertEqual(
                    pairs_recorded,
                    {("自治体A", "regulations"), ("自治体A", "minutes"), ("自治体B", "budget")},
                )

                # Now test RESUME: running again with the existing report file should verify 0 new pairs
                call_count = 0
                def counting_verify(ns):
                    nonlocal call_count
                    call_count += 1
                    return 0

                with mock.patch("tools.run_verify_block._cmd_verify", side_effect=counting_verify):
                    buf2 = io.StringIO()
                    with mock.patch("sys.stdout", buf2):
                        exit_code2 = run_verify_block(args)

                    self.assertEqual(exit_code2, 0)
                    self.assertEqual(call_count, 0, "Resumed run should not call _cmd_verify on already recorded pairs")
                    self.assertIn("No pending promotable pairs to verify", buf2.getvalue())

    def test_print_summary(self):
        new_records = [
            {"municipality": "市A", "kind": "regulations", "status_after": "ready", "result": "verified"},
            {"municipality": "市B", "kind": "minutes", "status_after": "blocked", "result": "blocked"},
        ]
        existing_records = [
            {"municipality": "市C", "kind": "budget", "status_after": "ready", "result": "verified"},
        ]
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            print_summary("test_block", new_records, existing_records)

        out = buf.getvalue()
        self.assertIn("=== Verification Summary (test_block) ===", out)
        self.assertIn("Total pairs in report: 3", out)
        self.assertIn("ready", out)
        self.assertIn("blocked", out)


if __name__ == "__main__":
    unittest.main()
