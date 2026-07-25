"""Tests for shared SQLite helpers."""

from __future__ import annotations

import sqlite3
import unittest

from lcaios.database import (
    fts5_table_tokenizer,
    supports_fts5,
    supports_fts5_trigram,
)


class FtsDatabaseHelpersTests(unittest.TestCase):
    def test_fts5_probe_matches_direct_sqlite_capability(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE direct_fts_probe USING fts5(value)"
                )
            except sqlite3.OperationalError:
                expected = False
            else:
                expected = True

            self.assertEqual(expected, supports_fts5(connection))
        finally:
            connection.close()

    def test_trigram_probe_matches_direct_sqlite_capability(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE direct_probe "
                    "USING fts5(value, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                expected = False
            else:
                expected = True

            self.assertEqual(expected, supports_fts5_trigram(connection))
        finally:
            connection.close()

    def test_reads_tokenizer_from_existing_fts_table(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE unicode_search "
                "USING fts5(value, tokenize='unicode61')"
            )
            self.assertEqual(
                "unicode61",
                fts5_table_tokenizer(connection, "unicode_search"),
            )

            if supports_fts5_trigram(connection):
                connection.execute(
                    "CREATE VIRTUAL TABLE trigram_search "
                    "USING fts5(value, tokenize='trigram')"
                )
                self.assertEqual(
                    "trigram",
                    fts5_table_tokenizer(connection, "trigram_search"),
                )
        finally:
            connection.close()

    def test_missing_fts_table_has_no_recorded_tokenizer(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            self.assertIsNone(
                fts5_table_tokenizer(connection, "missing_search")
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
