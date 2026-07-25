"""Synthetic tests for the kaigiroku.net adapter."""

from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import urlencode, urljoin

from lcaios.http import CacheTier
from lcaios.tests.http_fakes import FakeHttpClient, make_fetch_result
from modules.minutes_db.adapters.kaigiroku_net import (
    API_ENDPOINTS,
    API_ROOT,
    CALLBACK_NAME,
    KaigirokuNetAdapter,
    KaigirokuNetError,
    resolve_tenant,
    unwrap_jsonp,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def api_url(endpoint: str, **params: str) -> str:
    query = urlencode(
        {
            "tenant_id": "example",
            **params,
            "callback": CALLBACK_NAME,
        }
    )
    return f"{urljoin(API_ROOT, endpoint)}?{query}"


def fixture_client() -> FakeHttpClient:
    urls_and_fixtures = {
        api_url(API_ENDPOINTS["councils"]): "kaigiroku_councils.jsonp",
        api_url(
            API_ENDPOINTS["view_years"], council_id="c-1"
        ): "kaigiroku_years.jsonp",
        api_url(
            API_ENDPOINTS["minute_index"],
            council_id="c-1",
            year="2026",
        ): "kaigiroku_index.jsonp",
        api_url(
            API_ENDPOINTS["minute_index_list"],
            council_id="c-1",
            year="2026",
            schedule_id="s-1",
        ): "kaigiroku_index_list.jsonp",
        api_url(
            API_ENDPOINTS["minutes"],
            council_id="c-1",
            schedule_id="s-1",
            minute_id="m-1",
        ): "kaigiroku_minute.jsonp",
    }
    return FakeHttpClient(
        {
            url: make_fetch_result(
                url,
                (FIXTURES / fixture_name).read_text(encoding="utf-8"),
                content_type="application/javascript",
            )
            for url, fixture_name in urls_and_fixtures.items()
        }
    )


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
        client = fixture_client()
        adapter = KaigirokuNetAdapter(
            "https://ssp.kaigiroku.net/tenant/example/",
            client=client,
        )
        meetings = adapter.list_meetings(limit=1)

        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]["council_name"], "架空町議会")
        self.assertEqual(meetings[0]["date"], "2026-06-03")
        self.assertEqual(
            [url for url, _ in client.calls],
            [
                api_url(API_ENDPOINTS["councils"]),
                api_url(API_ENDPOINTS["view_years"], council_id="c-1"),
                api_url(
                    API_ENDPOINTS["minute_index"],
                    council_id="c-1",
                    year="2026",
                ),
                api_url(
                    API_ENDPOINTS["minute_index_list"],
                    council_id="c-1",
                    year="2026",
                    schedule_id="s-1",
                ),
            ],
        )
        self.assertTrue(
            all(tier is CacheTier.INDEX for _, tier in client.calls)
        )

        normalized = adapter.fetch_meeting(meetings[0])
        self.assertEqual(normalized["adapter"], "kaigiroku_net")
        self.assertEqual(normalized["fetched_at"], "2000-01-01T00:00:00Z")
        self.assertEqual(len(normalized["speeches"]), 2)
        self.assertEqual(normalized["speeches"][1]["speaker"], "○架空花子君")
        self.assertEqual(normalized["speeches"][1]["locator"], "2")
        self.assertEqual(client.calls[-1][1], CacheTier.DOCUMENT)

    def test_zero_limit_makes_no_requests(self) -> None:
        client = FakeHttpClient({})
        adapter = KaigirokuNetAdapter(
            "https://ssp.kaigiroku.net/tenant/example/",
            client=client,
        )
        self.assertEqual(adapter.list_meetings(limit=0), [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
