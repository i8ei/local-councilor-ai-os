"""Synthetic tests for the D1-Law OpenSearch regulations adapter."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from lcaios.http import FetchResult
from lcaios.tests.http_fakes import FakeHttpClient
from modules.regulations import vendor_d1law_opensearch

INDEX_URL = "https://ops-jg.d1-law.com/opensearch/?jctcd=8A7FF95853"
INIT_URL = "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A7FF95853"
SEARCH_URL_001 = "https://ops-jg.d1-law.com/opensearch/SrMjF01/search?typeSearch=SrMj_Genko&mokujicd=001:00:00"
DOC_1_HOUCD = "H417901010001"
DOC_1_URL = f"https://ops-jg.d1-law.com/opensearch/SrJbF01/init?jctcd=8A7FF95853&houcd={DOC_1_HOUCD}&fromJsp=SrMj"

INIT_HTML = """<!DOCTYPE HTML>
<html><head><title>春日部市例規集</title></head>
<body>
<div id="mokujiSearch">
<ul class="treeview">
<li id="treeGenko">
<a title="第１ 例規" href="javascript:void(0)" onclick="mkjG('001:00:00');">第１ 例規</a>
</li>
</ul>
</div>
</body></html>"""

SEARCH_HTML_001 = f"""<!DOCTYPE HTML>
<html><head><title>検索結果</title></head>
<body>
<table>
<tr>
<td>
<a href="javascript:void(0)" onClick="doViewJobunFromJsp('8A7FF95853', '{DOC_1_HOUCD}', null, null, '1', '1', null, 'SrMj'); return false;">
春日部市役所の位置を定める条例
</a>
（平成17年10月１日条例第１号）
</td>
</tr>
</table>
</body></html>"""

DOC_1_HTML = """<!DOCTYPE HTML>
<html><head><title>春日部市役所の位置を定める条例 春日部市例規集</title></head>
<body>
<div id="honbunArea">
<div class="contents-lineheight-2">春日部市役所の位置を定める条例</div>
<div class="contents-lineheight-2">（平成17年10月１日条例第１号）</div>
<div class="contents-lineheight-2">春日部市役所の位置を次のとおり定める。</div>
<div class="contents-lineheight-2">第１条 この条例は、市役所の位置を定める。</div>
<div class="contents-lineheight-2">埼玉県春日部市中央七丁目２番地１</div>
<div class="contents-lineheight-2">第２条 この条例は、公布の日から施行する。</div>
</div>
</body></html>"""


def _make_result(url: str, body: str, cache_dir: Path) -> FetchResult:
    raw = body.encode("utf-8")
    cp = cache_dir / "cache.html"
    cp.write_bytes(raw)
    return FetchResult(
        url=url,
        final_url=url,
        body=raw,
        fetched_at="2026-08-27T12:00:00Z",
        content_type="text/html; charset=utf-8",
        encoding="utf-8",
        cache_path=cp,
        sha256=hashlib.sha256(raw).hexdigest(),
        from_cache=False,
    )


class D1LawOpenSearchTests(unittest.TestCase):
    def test_normalize_index_url(self) -> None:
        self.assertEqual(
            "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A7FF95853",
            vendor_d1law_opensearch.normalize_index_url(
                "https://ops-jg.d1-law.com/opensearch/?jctcd=8A7FF95853"
            ),
        )
        self.assertEqual(
            "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A7FF95853",
            vendor_d1law_opensearch.normalize_index_url(
                "https://ops-jg.d1-law.com/opensearch/SrMjF01/init?jctcd=8A7FF95853"
            ),
        )
        with self.assertRaises(ValueError):
            vendor_d1law_opensearch.normalize_index_url(
                "https://ops-jg.d1-law.com/opensearch/"
            )

    def test_discover_and_fetch_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            client = FakeHttpClient(
                {
                    INIT_URL: _make_result(INIT_URL, INIT_HTML, tmp_path),
                    SEARCH_URL_001: _make_result(
                        SEARCH_URL_001, SEARCH_HTML_001, tmp_path
                    ),
                    DOC_1_URL: _make_result(DOC_1_URL, DOC_1_HTML, tmp_path),
                }
            )
            refs = vendor_d1law_opensearch.discover_documents(
                INDEX_URL, client=client, limit=5
            )
            self.assertEqual(1, len(refs))
            ref = refs[0]
            self.assertEqual("春日部市役所の位置を定める条例", ref["title"])
            self.assertEqual(DOC_1_HOUCD, ref["houcd"])

            doc_payload = vendor_d1law_opensearch.fetch_document(
                ref, index_url=INDEX_URL, client=client
            )
            self.assertEqual(
                "春日部市役所の位置を定める条例", doc_payload["document"]["title"]
            )
            self.assertEqual("2005-10-01", doc_payload["document"]["promulgated_on"])
            self.assertGreaterEqual(len(doc_payload["articles"]), 1)

    def test_ingest_d1law_opensearch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_path = tmp_path / "test.db"
            client_responses = {
                INIT_URL: _make_result(INIT_URL, INIT_HTML, tmp_path),
                SEARCH_URL_001: _make_result(
                    SEARCH_URL_001, SEARCH_HTML_001, tmp_path
                ),
                DOC_1_URL: _make_result(DOC_1_URL, DOC_1_HTML, tmp_path),
            }
            # Verify SQLite schema using store_document
            with closing(sqlite3.connect(db_path)) as conn, conn:
                vendor_d1law_opensearch.ensure_schema(conn)
                client = FakeHttpClient(client_responses)
                refs = vendor_d1law_opensearch.discover_documents(
                    INDEX_URL, client=client
                )
                payload = vendor_d1law_opensearch.fetch_document(
                    refs[0], index_url=INDEX_URL, client=client
                )
                vendor_d1law_opensearch.store_document(conn, payload)
                conn.commit()

                cur = conn.execute("SELECT COUNT(*) FROM regulation_documents")
                self.assertEqual(1, cur.fetchone()[0])
                cur2 = conn.execute("SELECT COUNT(*) FROM regulation_articles")
                self.assertGreaterEqual(cur2.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
