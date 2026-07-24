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
from bootstrap.municipalities import (
    load_metadata,
    load_registry,
    validate_registry,
)


class NoNetworkClient:
    """Fail if a bundled-registry test reaches HTTP."""

    request_count = 0

    def fetch(self, *_: object, **__: object) -> object:
        raise AssertionError("network fallback was not expected")


class ResolveTests(unittest.TestCase):
    def test_bundled_registry_resolves_without_network(self) -> None:
        client = NoNetworkClient()
        result = resolve_municipality("伊万里市", "佐賀県", client)
        self.assertEqual("412058", result["local_government_code_6"])
        self.assertEqual("bundled municipality registry", result["resolved_from"])
        self.assertEqual("http://www.city.imari.saga.jp/", result["official_home_url"])
        self.assertEqual(0, client.request_count)

    def test_bundled_registry_keeps_real_ambiguity(self) -> None:
        with self.assertRaises(AmbiguousMunicipality) as raised:
            resolve_municipality("府中市", None, FakeRegionClient())
        self.assertEqual(2, len(raised.exception.candidates))

    def test_bundled_registry_accepts_published_name_alias(self) -> None:
        result = resolve_municipality("七ケ宿町", "宮城県", FakeRegionClient())
        self.assertEqual("七ヶ宿町", result["name"])
        self.assertEqual("043028", result["local_government_code_6"])

    def test_registry_contains_all_saga_municipalities(self) -> None:
        saga_rows = [
            row for row in load_registry() if row["prefecture_name"] == "佐賀県"
        ]
        self.assertEqual(20, len(saga_rows))
        self.assertEqual(
            20,
            len({row["official_home_url"] for row in saga_rows}),
        )

    def test_registry_integrity(self) -> None:
        report = validate_registry()
        self.assertEqual(1741, report["record_count"])
        self.assertEqual(47, report["prefecture_count"])
        self.assertEqual(8, load_metadata()["excluded_current_region_count"])

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
