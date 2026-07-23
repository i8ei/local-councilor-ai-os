"""Synthetic tests for the kaigiroku.net adapter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(MODULE_ROOT))

from adapters.kaigiroku_net import (  # noqa: E402
    API_ENDPOINTS,
    KaigirokuNetAdapter,
    KaigirokuNetError,
    resolve_tenant,
    unwrap_jsonp,
)


def fixture(name: str) -> Any:
    return unwrap_jsonp((FIXTURES / name).read_bytes())


class FixtureAdapter(KaigirokuNetAdapter):
    def __init__(self) -> None:
        super().__init__("https://ssp.kaigiroku.net/tenant/example/")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = {
            API_ENDPOINTS["councils"]: fixture("kaigiroku_councils.jsonp"),
            API_ENDPOINTS["view_years"]: fixture("kaigiroku_years.jsonp"),
            API_ENDPOINTS["minute_index"]: fixture("kaigiroku_index.jsonp"),
            API_ENDPOINTS["minute_index_list"]: fixture(
                "kaigiroku_index_list.jsonp"
            ),
            API_ENDPOINTS["minutes"]: fixture("kaigiroku_minute.jsonp"),
        }

    def _api(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, Any]:
        self.calls.append((endpoint, params or {}))
        response = SimpleNamespace(
            fetched_at="2026-07-23T00:00:00+00:00",
            final_url=f"https://example.invalid/api/{endpoint}",
            content_type="application/javascript",
            sha256="0" * 64,
            cache_path=Path("/tmp/synthetic-cache"),
            from_cache=True,
        )
        return self.responses[endpoint], response


class JsonpTests(unittest.TestCase):
    def test_unwrap_jsonp_and_plain_json(self) -> None:
        self.assertEqual(unwrap_jsonp('cb({"ok":true});'), {"ok": True})
        self.assertEqual(unwrap_jsonp('{"ok":true}'), {"ok": True})

    def test_unwrap_cp932_jsonp(self) -> None:
        raw = 'cb({"name":"架空町議会"});'.encode("cp932")
        self.assertEqual(unwrap_jsonp(raw)["name"], "架空町議会")

    def test_rejects_javascript_expression(self) -> None:
        with self.assertRaises(KaigirokuNetError):
            unwrap_jsonp('cb(alert("not JSON"));')


class TenantTests(unittest.TestCase):
    def test_resolves_explicit_tenant_path(self) -> None:
        self.assertEqual(
            resolve_tenant("https://ssp.kaigiroku.net/tenant/example/"),
            ("example", "example"),
        )

    def test_explicit_tenant_id_query_wins(self) -> None:
        self.assertEqual(
            resolve_tenant(
                "https://ssp.kaigiroku.net/tenant/example/?tenant_id=official-id"
            ),
            ("example", "official-id"),
        )

    def test_rejects_non_tenant_url(self) -> None:
        with self.assertRaises(ValueError):
            resolve_tenant("https://example.invalid/tenant/example/")


class AdapterPipelineTests(unittest.TestCase):
    def test_lists_and_fetches_one_normalized_meeting(self) -> None:
        adapter = FixtureAdapter()
        meetings = adapter.list_meetings(limit=1)

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["council_name"], "架空町議会")
        self.assertEqual(meetings[0]["date"], "2026-06-03")
        self.assertEqual(
            [call[0] for call in adapter.calls],
            [
                API_ENDPOINTS["councils"],
                API_ENDPOINTS["view_years"],
                API_ENDPOINTS["minute_index"],
                API_ENDPOINTS["minute_index_list"],
            ],
        )

        normalized = adapter.fetch_meeting(meetings[0])
        self.assertEqual(normalized["adapter"], "kaigiroku_net")
        self.assertEqual(normalized["fetched_at"], "2026-07-23T00:00:00+00:00")
        self.assertEqual(len(normalized["speeches"]), 2)
        self.assertEqual(normalized["speeches"][1]["speaker"], "○架空花子君")
        self.assertEqual(normalized["speeches"][1]["locator"], "2")
        self.assertEqual(
            normalized["provenance"]["content_hash"], f"sha256:{'0' * 64}"
        )

    def test_zero_limit_makes_no_requests(self) -> None:
        adapter = FixtureAdapter()
        self.assertEqual(adapter.list_meetings(limit=0), [])
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
