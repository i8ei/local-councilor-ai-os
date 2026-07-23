"""Tests for common-vintage e-Stat semantic selection."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bootstrap.cli.census import FALLBACK_TABLES, discover_tables, fetch_census
from bootstrap.cli.http import FetchError
from bootstrap.cli.tests.fixtures import FakeEStatClient


class FailingEStatClient:
    offline = False
    request_count = 0

    def fetch(self, *_args: object, **_kwargs: object) -> object:
        raise FetchError("synthetic discovery failure")


class CensusTests(unittest.TestCase):
    @patch.dict(os.environ, {"ESTAT_APPID": "test-secret"}, clear=False)
    def test_dynamic_tables_share_one_survey_date(self) -> None:
        result = fetch_census(
            {"area_code_5": "01101"},
            FakeEStatClient(),
        )
        self.assertFalse(result["selection"]["used_fallback"])
        self.assertEqual(3, len(result["records"]))
        self.assertEqual(
            {"2020-10-01"},
            {record["as_of"] for record in result["records"]},
        )
        serialized = repr(result)
        self.assertNotIn("test-secret", serialized)
        ratio = next(
            record
            for record in result["records"]
            if record["indicator"] == "population_65_plus_ratio"
        )
        self.assertEqual("％", ratio["unit"])
        self.assertEqual(31.2, ratio["value"])

    @patch.dict(os.environ, {"ESTAT_APPID": "test-secret"}, clear=False)
    def test_discovery_failure_records_latestness_warning(self) -> None:
        selection = discover_tables(FailingEStatClient())
        self.assertTrue(selection.used_fallback)
        self.assertEqual(FALLBACK_TABLES, selection.tables)
        self.assertIn("latest-ness lost", selection.warning or "")


if __name__ == "__main__":
    unittest.main()
