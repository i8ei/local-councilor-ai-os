"""Unit tests for scout vendor probe heuristics."""

import unittest
from unittest.mock import MagicMock
from pathlib import Path

from tools.scout_profiles import probe_vendor_regulations, probe_vendor_minutes


class ScoutProbeTests(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()

    def test_greiki_probe_success_with_muni_name_verification(self):
        mock_res = MagicMock()
        mock_res.text.return_value = "<html><head><title>糸島市例規集</title></head><body><h1>糸島市例規類集</h1></body></html>"
        mock_res.sha256 = "dummy_sha"
        mock_res.fetched_at = "2026-08-27T00:00:00Z"
        self.mock_client.fetch.return_value = mock_res

        result = probe_vendor_regulations("糸島市", "itoshima", self.mock_client)
        self.assertIsNotNone(result)
        self.assertEqual(result["adapter"], "g_reiki")
        self.assertEqual(result["base_url"], "https://www1.g-reiki.net/itoshima/")
        self.assertEqual(result["status"], "needs_review")

    def test_greiki_probe_rejects_mismatched_muni_name(self):
        # 200 OK returned, but title is for another municipality or generic 404
        mock_res = MagicMock()
        mock_res.text.return_value = "<html><head><title>ぎょうせい 例規Net トップ</title></head></html>"
        mock_res.sha256 = "dummy_sha"
        mock_res.fetched_at = "2026-08-27T00:00:00Z"
        self.mock_client.fetch.return_value = mock_res

        result = probe_vendor_regulations("糸島市", "itoshima", self.mock_client)
        self.assertIsNone(result)

    def test_kaigiroku_net_probe_success(self):
        mock_res = MagicMock()
        mock_res.text.return_value = "<html><head><title>筑後市議会 会議録検索</title></head></html>"
        mock_res.sha256 = "dummy_sha"
        mock_res.fetched_at = "2026-08-27T00:00:00Z"
        self.mock_client.fetch.return_value = mock_res

        result = probe_vendor_minutes("筑後市", "chikugo", self.mock_client)
        self.assertIsNotNone(result)
        self.assertEqual(result["adapter"], "kaigiroku_net")
        self.assertIn("ssp.kaigiroku.net", result["tenant_url"])


if __name__ == "__main__":
    unittest.main()
