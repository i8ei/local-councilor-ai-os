"""Tests for source_profiles CLI (synthetic + real profile checks)."""

from __future__ import annotations  # noqa: I001

import importlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_cli = importlib.import_module("source_profiles.cli")
main = _cli.main  # type: ignore[attr-defined]
_schema = importlib.import_module("source_profiles.schema")
validate_profile = _schema.validate_profile  # type: ignore[attr-defined]


def _synthetic_greiki_profile(
    status: str, municipality: str = "太良町", area: str = "41441"
) -> dict:
    # Use real registry data for 41441 to keep validation happy if checked
    return {
        "schema_version": 1,
        "area_code_5": area,
        "prefecture": "佐賀県",
        "municipality": municipality,
        "official_home_url": "http://www.town.tara.lg.jp/",
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
                "status": status,
                "adapter": "g_reiki",
                "base_url": "https://www1.g-reiki.net/town.tara/",
                "verified_at": "2020-01-01T00:00:00Z" if status == "ready" else None,
                "verified_by": "tester" if status == "ready" else None,
                "evidence": [
                    {
                        "url": "https://www1.g-reiki.net/town.tara/reiki_menu.html",
                        "observed_on": "http://www.town.tara.lg.jp/",
                        "sha256": "a" * 64 if status == "ready" else None,
                        "fetched_at": "2020-01-01T00:00:00Z"
                        if status == "ready"
                        else None,
                    }
                ]
                if status == "ready"
                else [
                    {
                        "url": "https://www1.g-reiki.net/town.tara/reiki_menu.html",
                        "observed_on": "http://www.town.tara.lg.jp/",
                    }
                ],
                "notes": "synthetic",
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


def _synthetic_d1_profile() -> dict:
    return {
        "schema_version": 1,
        "area_code_5": "41346",
        "prefecture": "佐賀県",
        "municipality": "みやき町",
        "official_home_url": "http://www.town.miyaki.lg.jp/",
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
                "status": "unsupported",
                "adapter": "d1_law",
                "index_url": "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E",
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {
                        "url": "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A9170542E",
                        "observed_on": "https://www.town.miyaki.lg.jp/sitemap.html",
                    }
                ],
                "notes": "synthetic",
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


def _synthetic_joureikun_profile() -> dict:
    return {
        "schema_version": 1,
        "area_code_5": "41423",
        "prefecture": "佐賀県",
        "municipality": "大町町",
        "official_home_url": "https://www.town.omachi.lg.jp/",
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
                "status": "unsupported",
                "adapter": "joureikun",
                "index_url": "https://www.town.omachi.lg.jp/joureikun/index.html",
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {
                        "url": "https://www.town.omachi.lg.jp/joureikun/index.html",
                        "observed_on": "https://www.town.omachi.lg.jp/",
                    }
                ],
                "notes": "synthetic",
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


def _synthetic_dbsr_minutes_profile(status: str = "needs_review") -> dict:
    return {
        "schema_version": 1,
        "area_code_5": "41210",
        "prefecture": "佐賀県",
        "municipality": "神埼市",
        "official_home_url": "http://www.city.kanzaki.saga.jp/",
        "sources": {
            "minutes": {
                "status": status,
                "adapter": "dbsr",
                "index_url": "https://www.city.kanzaki.saga.dbsr.jp/index.php/",
                "verified_at": None,
                "verified_by": None,
                "evidence": [
                    {
                        "url": "https://www.city.kanzaki.saga.dbsr.jp/index.php/",
                        "observed_on": "http://www.city.kanzaki.saga.jp/",
                    }
                ],
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


class CliTests(unittest.TestCase):
    def test_validate_all_saga_passes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["validate", "--all", "--prefecture", "佐賀県"])
        self.assertEqual(0, code)
        report = json.loads(buf.getvalue())
        self.assertEqual("ok", report["status"])
        self.assertEqual(20, report["profile_count"])
        self.assertEqual(0, report["error_count"])
        for item in report["results"]:
            self.assertEqual("ok", item["status"], msg=str(item))

    def test_validate_single_profile(self) -> None:
        profile = Path("source_profiles/municipalities/41-saga/41441-tara.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["validate", "--profile", str(profile)])
        self.assertEqual(0, code)
        report = json.loads(buf.getvalue())
        self.assertEqual("ok", report["status"])
        self.assertEqual(1, report["profile_count"])

    def test_ingest_g_reiki_needs_review_has_comment(self) -> None:
        # Use synthetic profile in temp dir to avoid coupling to real file state
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = _synthetic_greiki_profile("needs_review")
            # Clean up evidence for needs_review (no sha needed)
            (tmp_path / "41441-tara.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "ingest-command",
                        "--municipality",
                        "太良町",
                        "--prefecture",
                        "佐賀県",
                        "--kind",
                        "regulations",
                        "--limit",
                        "3",
                        "--profiles-dir",
                        str(tmp_path),
                    ]
                )
            self.assertEqual(0, code)
            text = out.getvalue()
            self.assertIn("# NEEDS LIVE VERIFICATION", text)
            self.assertIn("modules/regulations/vendor_greiki.py", text)
            self.assertIn("--base-url https://www1.g-reiki.net/town.tara/", text)
            self.assertIn('--source-name "太良町例規集"', text)
            self.assertIn("--limit 3", text)
            self.assertIn("--db /tmp/41441-reg.db", text)

    def test_ingest_g_reiki_ready_has_no_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = _synthetic_greiki_profile("ready")
            (tmp_path / "41441-tara.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "ingest-command",
                        "--municipality",
                        "太良町",
                        "--prefecture",
                        "佐賀県",
                        "--kind",
                        "regulations",
                        "--limit",
                        "3",
                        "--profiles-dir",
                        str(tmp_path),
                    ]
                )
            self.assertEqual(0, code)
            text = out.getvalue()
            self.assertNotIn("# NEEDS LIVE VERIFICATION", text)
            self.assertIn("modules/regulations/vendor_greiki.py", text)

    def test_ingest_d1_law_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = _synthetic_d1_profile()
            (tmp_path / "41346-miyaki.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "ingest-command",
                        "--municipality",
                        "みやき町",
                        "--prefecture",
                        "佐賀県",
                        "--kind",
                        "regulations",
                        "--profiles-dir",
                        str(tmp_path),
                    ]
                )
            self.assertEqual(2, code)
            self.assertEqual("", out.getvalue().strip())
            err_text = err.getvalue()
            self.assertIn("d1_law", err_text)
            self.assertIn("next action", err_text.lower())

    def test_ingest_joureikun_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = _synthetic_joureikun_profile()
            (tmp_path / "41423-omachi.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        "ingest-command",
                        "--municipality",
                        "大町町",
                        "--prefecture",
                        "佐賀県",
                        "--kind",
                        "regulations",
                        "--profiles-dir",
                        str(tmp_path),
                    ]
                )
            self.assertEqual(2, code)
            self.assertIn("joureikun", err.getvalue())

    def test_ingest_via_env_var_profiles_dir(self) -> None:
        # Verify env var override works as alternative to --profiles-dir
        import os

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = _synthetic_greiki_profile("needs_review")
            (tmp_path / "41441-tara.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            old = os.environ.get("SOURCE_PROFILES_DIR")
            os.environ["SOURCE_PROFILES_DIR"] = str(tmp_path)
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(
                        [
                            "ingest-command",
                            "--municipality",
                            "太良町",
                            "--kind",
                            "regulations",
                            "--limit",
                            "2",
                        ]
                    )
            finally:
                if old is None:
                    os.environ.pop("SOURCE_PROFILES_DIR", None)
                else:
                    os.environ["SOURCE_PROFILES_DIR"] = old
            self.assertEqual(0, code)
            self.assertIn("--limit 2", out.getvalue())

    def test_real_profiles_all_validate_via_schema(self) -> None:
        # Directly exercise validate_profile on the 20 real JSON files (registry check included)
        for path in sorted(
            Path("source_profiles/municipalities/41-saga").glob("*.json")
        ):
            data = json.loads(path.read_text(encoding="utf-8"))
            errs = validate_profile(data)
            self.assertEqual([], errs, msg=f"{path} failed: {errs}")

    def test_validate_reports_error_for_broken_profile(self) -> None:
        # Use synthetic invalid profile via temp file
        bad = {
            "schema_version": 1,
            "area_code_5": "41441",
            "prefecture": "佐賀県",
            "municipality": "太良町",
            "official_home_url": "http://www.town.tara.lg.jp/",
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
                    "status": "ready",
                    "adapter": "g_reiki",
                    "base_url": "https://www1.g-reiki.net/town.tara/",
                    "verified_at": None,  # breaks ready
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
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(bad, tmp, ensure_ascii=False)
            tmp_path = tmp.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["validate", "--profile", tmp_path])
            self.assertEqual(2, code)
            report = json.loads(buf.getvalue())
            self.assertEqual("error", report["status"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_verify_persists_blocked_and_returns_0(self) -> None:
        import copy
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            cache_dir.mkdir()
            profile = _synthetic_dbsr_minutes_profile("needs_review")
            profile_path = tmp_path / "41210-kanzaki.json"
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated = copy.deepcopy(profile)
            updated["sources"]["minutes"]["status"] = "blocked"
            updated["sources"]["minutes"]["verified_at"] = "2020-01-01T00:00:00Z"
            updated["sources"]["minutes"]["verified_by"] = "verify --live"
            updated["sources"]["minutes"]["evidence"] = [
                {
                    "url": "https://www.city.kanzaki.saga.dbsr.jp/index.php/",
                    "observed_on": "https://www.city.kanzaki.saga.dbsr.jp/index.php/",
                    "sha256": "b" * 64,
                    "fetched_at": "2020-01-01T00:00:00Z",
                }
            ]
            updated["sources"]["minutes"]["notes"] = (
                "minutes bodies are robots-restricted (robots.txt disallows meeting detail/document paths); observed Saga dbsr tenants block bodies, so ingestion requires the councilor/user to obtain municipality permission (out of scope for automated ingestion)"
            )
            report = {
                "municipality": "神埼市",
                "kind": "minutes",
                "adapter": "dbsr",
                "result": "blocked",
                "reason": "RobotsDeniedError: robots.txt disallows (minutes bodies are robots-restricted)",
                "status_before": "needs_review",
                "status_after": "blocked",
            }
            with mock.patch.object(
                _cli, "HttpClient", lambda *a, **kw: mock.MagicMock()
            ):  # type: ignore[arg-type]
                with mock.patch.object(
                    _cli, "verify_profile", return_value=(updated, report)
                ):  # type: ignore[arg-type]
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = main(
                            [
                                "verify",
                                "--municipality",
                                "神埼市",
                                "--prefecture",
                                "佐賀県",
                                "--kind",
                                "minutes",
                                "--cache-dir",
                                str(cache_dir),
                                "--profiles-dir",
                                str(tmp_path),
                            ]
                        )
                    self.assertEqual(0, code)
                    out_report = json.loads(buf.getvalue())
                    self.assertEqual("blocked", out_report["result"])
                    on_disk = json.loads(profile_path.read_text(encoding="utf-8"))
                    self.assertEqual("blocked", on_disk["sources"]["minutes"]["status"])
                    self.assertEqual(
                        "2020-01-01T00:00:00Z",
                        on_disk["sources"]["minutes"]["verified_at"],
                    )

    def test_verify_persists_verified_and_returns_0(self) -> None:
        import copy
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            cache_dir.mkdir()
            profile = _synthetic_greiki_profile("needs_review")
            profile_path = tmp_path / "41441-tara.json"
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated = copy.deepcopy(profile)
            updated["sources"]["regulations"]["status"] = "ready"
            updated["sources"]["regulations"]["verified_at"] = "2020-01-01T00:00:00Z"
            updated["sources"]["regulations"]["verified_by"] = "verify --live"
            updated["sources"]["regulations"]["evidence"] = [
                {
                    "url": "https://www1.g-reiki.net/town.tara/reiki_menu.html",
                    "observed_on": "https://www1.g-reiki.net/town.tara/",
                    "sha256": "c" * 64,
                    "fetched_at": "2020-01-01T00:00:00Z",
                }
            ]
            report = {
                "municipality": "太良町",
                "kind": "regulations",
                "adapter": "g_reiki",
                "result": "verified",
                "reason": "ok",
                "status_before": "needs_review",
                "status_after": "ready",
            }
            with mock.patch.object(
                _cli, "HttpClient", lambda *a, **kw: mock.MagicMock()
            ):  # type: ignore[arg-type]
                with mock.patch.object(
                    _cli, "verify_profile", return_value=(updated, report)
                ):  # type: ignore[arg-type]
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = main(
                            [
                                "verify",
                                "--municipality",
                                "太良町",
                                "--prefecture",
                                "佐賀県",
                                "--kind",
                                "regulations",
                                "--cache-dir",
                                str(cache_dir),
                                "--profiles-dir",
                                str(tmp_path),
                            ]
                        )
                    self.assertEqual(0, code)
                    out_report = json.loads(buf.getvalue())
                    self.assertEqual("verified", out_report["result"])
                    on_disk = json.loads(profile_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        "ready", on_disk["sources"]["regulations"]["status"]
                    )

    def test_verify_failed_does_not_persist(self) -> None:
        import copy
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            cache_dir.mkdir()
            profile = _synthetic_dbsr_minutes_profile("needs_review")
            profile_path = tmp_path / "41210-kanzaki.json"
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            updated = copy.deepcopy(profile)
            report = {
                "municipality": "神埼市",
                "kind": "minutes",
                "adapter": "dbsr",
                "result": "failed",
                "reason": "FetchError: HTTP 500",
                "status_before": "needs_review",
                "status_after": "needs_review",
            }
            with mock.patch.object(
                _cli, "HttpClient", lambda *a, **kw: mock.MagicMock()
            ):  # type: ignore[arg-type]
                with mock.patch.object(
                    _cli, "verify_profile", return_value=(updated, report)
                ):  # type: ignore[arg-type]
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = main(
                            [
                                "verify",
                                "--municipality",
                                "神埼市",
                                "--prefecture",
                                "佐賀県",
                                "--kind",
                                "minutes",
                                "--cache-dir",
                                str(cache_dir),
                                "--profiles-dir",
                                str(tmp_path),
                            ]
                        )
                    self.assertEqual(2, code)
                    out_report = json.loads(buf.getvalue())
                    self.assertEqual("failed", out_report["result"])
                    on_disk = json.loads(profile_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        "needs_review", on_disk["sources"]["minutes"]["status"]
                    )


if __name__ == "__main__":
    unittest.main()
