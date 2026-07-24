"""Tests for public-output leakage detection and CLI gating."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from lcaios.cli import main
from lcaios.output_check import scan_text


class OutputCheckTests(unittest.TestCase):
    def test_clean_public_markdown_has_no_findings(self) -> None:
        findings = scan_text(
            """
            # 人口について

            2020年10月1日時点の人口は、公的統計による値です。
            出典: [政府統計の総合窓口](https://www.e-stat.go.jp/)
            金額は1,234,567円です。
            """
        )
        self.assertEqual([], findings)

    def test_detects_internal_paths_markers_comments_and_invisible_text(self) -> None:
        text = (
            "# 公開稿\n"
            "関連: [[内部ノート]]\n"
            "作業場所: /Users/example/Documents/Obsidian Vault/内部.md\n"
            "状態: ［要確認］\n"
            "%% 公開しないメモ %%\n"
            "<!-- hidden -->\n"
            "見えない\u202e文字\n"
            "visibility: internal\n"
        )
        findings = scan_text(text)
        codes = {item["code"] for item in findings}
        self.assertEqual(
            {
                "internal_wikilink",
                "internal_or_absolute_path",
                "unverified_marker",
                "hidden_comment",
                "invisible_character",
                "internal_classification_marker",
            },
            codes,
        )
        unverified = [
            item for item in findings if item["code"] == "unverified_marker"
        ]
        self.assertEqual(1, len(unverified))

    def test_secret_value_is_not_echoed_in_finding(self) -> None:
        secret = "abcdefghijklmnop123456"
        findings = scan_text(f"api_key={secret}\n")
        self.assertEqual("possible_secret", findings[0]["code"])
        self.assertNotIn(secret, repr(findings))

    def test_public_url_with_private_path_is_not_an_absolute_path(self) -> None:
        findings = scan_text(
            "[公開資料](https://example.jp/private/document.pdf)\n"
        )
        self.assertEqual([], findings)

    def test_cli_blocks_errors_and_returns_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "draft.md"
            path.write_text("関連: [[内部ノート]]\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "verify",
                        "output",
                        "--file",
                        str(path),
                        "--format",
                        "json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(2, exit_code)
            self.assertEqual("blocked", payload["status"])
            self.assertEqual(1, payload["counts"]["error"])
            self.assertTrue(payload["read_only"])

    def test_cli_warning_threshold_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "draft.md"
            path.write_text("<!-- hidden -->\n", encoding="utf-8")
            with redirect_stdout(StringIO()):
                default_exit = main(
                    ["verify", "output", "--file", str(path)]
                )
            with redirect_stdout(StringIO()):
                strict_exit = main(
                    [
                        "verify",
                        "output",
                        "--file",
                        str(path),
                        "--fail-on",
                        "warning",
                    ]
                )
            self.assertEqual(0, default_exit)
            self.assertEqual(2, strict_exit)


if __name__ == "__main__":
    unittest.main()

