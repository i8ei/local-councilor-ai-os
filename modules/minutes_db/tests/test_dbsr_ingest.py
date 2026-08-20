"""Synthetic wiring tests for dbsr ingest dispatch (no live I/O)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from lcaios.tests.http_fakes import FakeHttpClient
from modules.minutes_db import ingest
from modules.minutes_db.adapters.dbsr import DbsrAdapter


class ParserChoiceTests(unittest.TestCase):
    def test_parser_accepts_dbsr_choice(self) -> None:
        parser = ingest.build_parser()
        args = parser.parse_args(
            [
                "--adapter",
                "dbsr",
                "--url",
                "https://www.city.kanzaki.saga.dbsr.jp/index.php/",
            ]
        )
        self.assertEqual("dbsr", args.adapter)
        self.assertIn("dbsr", parser._actions[1].choices)  # type: ignore[attr-defined]

    def test_parser_choices_contain_all_three(self) -> None:
        parser = ingest.build_parser()
        # find --adapter action
        adapter_action = next(
            a for a in parser._actions if "--adapter" in a.option_strings
        )
        self.assertEqual(
            {"kaigiroku_net", "static", "dbsr"}, set(adapter_action.choices)
        )  # type: ignore[arg-type]

    def test_parser_rejects_unknown_adapter(self) -> None:
        parser = ingest.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--adapter", "unknown"])


class MakeAdapterTests(unittest.TestCase):
    def test_make_adapter_returns_dbsr_adapter(self) -> None:
        client = FakeHttpClient({})
        args = SimpleNamespace(
            adapter="dbsr",
            url="https://www.city.kanzaki.saga.dbsr.jp/index.php/",
            config=None,
        )
        adapter = ingest._make_adapter(args, client)  # type: ignore[arg-type]
        self.assertIsInstance(adapter, DbsrAdapter)
        self.assertIs(adapter.client, client)
        self.assertEqual(
            ["https://www.city.kanzaki.saga.dbsr.jp/index.php/"], adapter.index_urls
        )

    def test_make_adapter_requires_url_for_dbsr(self) -> None:
        client = FakeHttpClient({})
        for missing in (None, ""):
            with self.subTest(missing=missing):
                args = SimpleNamespace(adapter="dbsr", url=missing, config=None)
                with self.assertRaises(ValueError) as ctx:
                    ingest._make_adapter(args, client)  # type: ignore[arg-type]
                self.assertIn("--url", str(ctx.exception))
                self.assertIn("dbsr", str(ctx.exception))

    def test_make_adapter_still_handles_kaigiroku_and_static(self) -> None:
        client = FakeHttpClient({})
        # kaigiroku_net still requires --url
        with self.assertRaises(ValueError):
            ingest._make_adapter(
                SimpleNamespace(adapter="kaigiroku_net", url=None, config=None), client
            )  # type: ignore[arg-type]
        # static still requires --config
        with self.assertRaises(ValueError):
            ingest._make_adapter(
                SimpleNamespace(adapter="static", url=None, config=None), client
            )  # type: ignore[arg-type]

    def test_ingest_module_imports_dbsr_adapter(self) -> None:
        # Verify ingest.py imports DbsrAdapter (done_when requirement)
        import inspect

        source = inspect.getsource(ingest)
        self.assertIn("DbsrAdapter", source)
        self.assertIn("from .adapters.dbsr import DbsrAdapter", source)


if __name__ == "__main__":
    unittest.main()
