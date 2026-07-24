"""Tests for Tier 0 exact matching and ambiguity."""

from __future__ import annotations

import contextlib
import importlib
import io
import unittest
from unittest.mock import patch

from bootstrap.cli.resolve import (
    AmbiguousMunicipality,
    local_government_code,
    resolve_municipality,
)
from bootstrap.cli.tests.fixtures import FakeRegionClient


class ResolveTests(unittest.TestCase):
    def test_local_government_code_modulus_edge_cases(self) -> None:
        cases = {
            "41209": "412091",
            "41204": "412040",
            "41203": "412031",
        }
        for area_code, expected in cases.items():
            with self.subTest(area_code=area_code):
                self.assertEqual(expected, local_government_code(area_code))

    def test_ambiguous_name_requires_prefecture(self) -> None:
        with self.assertRaises(AmbiguousMunicipality) as raised:
            resolve_municipality("同名市", None, FakeRegionClient())
        self.assertEqual(2, len(raised.exception.candidates))

    def test_prefecture_hint_resolves_exact_current_municipality(self) -> None:
        result = resolve_municipality("同名市", "北県", FakeRegionClient())
        self.assertEqual("01101", result["area_code_5"])
        self.assertEqual(
            local_government_code("01101"),
            result["local_government_code_6"],
        )
        self.assertEqual("北県", result["prefecture"])
        self.assertEqual(1, result["candidate_count"])

    def test_cli_returns_exit_code_two_for_ambiguity(self) -> None:
        cli_main = importlib.import_module("bootstrap.cli.main")
        error = AmbiguousMunicipality(
            "同名市",
            [
                {
                    "name": "同名市",
                    "prefecture": "北県",
                    "area_code_5": "01101",
                },
                {
                    "name": "同名市",
                    "prefecture": "南県",
                    "area_code_5": "02101",
                },
            ],
        )
        output = io.StringIO()
        with patch("bootstrap.cli.main.run", side_effect=error):
            with contextlib.redirect_stdout(output):
                exit_code = cli_main.main(["同名市"])
        self.assertEqual(2, exit_code)
        self.assertIn('"status": "ambiguous"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
