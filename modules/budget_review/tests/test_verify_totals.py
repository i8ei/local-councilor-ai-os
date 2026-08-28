"""Tests for budget hierarchy reconciliation fallback symmetry."""

from __future__ import annotations

import io
import sqlite3
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout

from modules.budget_review import verify_totals

SCHEMA = (verify_totals.MODULE_DIR / "schema.sql").read_text(encoding="utf-8")

COMMON = {
    "fiscal_year": 2099,
    "account_name": "一般会計",
    "budget_stage": "initial",
    "proposal_no": None,
    "raw_value": "fixture raw",
    "unit": "千円",
    "as_of": "2099年度補正予算",
    "definition": "fixture definition",
    "source_name": "例予算書",
    "source_url": "https://example.invalid/budget.pdf",
    "source_locator": '{"page":1}',
    "fetched_at": "2099-01-01T00:00:00Z",
    "verification_state": "verified",
    "print_page": "1",
    "pdf_page": 1,
}


@contextmanager
def make_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    try:
        yield connection
    finally:
        connection.close()


def insert(connection: sqlite3.Connection, **overrides: object) -> None:
    values = {**COMMON, **overrides}
    columns = ",".join(values)
    placeholders = ",".join(f":{key}" for key in values)
    connection.execute(
        f"INSERT INTO budget_line ({columns}) VALUES ({placeholders})", values
    )


def run_hierarchy(connection: sqlite3.Connection) -> int:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return verify_totals._check_hierarchy(connection)


class ChildFallbackTests(unittest.TestCase):
    def test_post_supplement_only_reconciles(self) -> None:
        with make_connection() as connection:
            insert(
                connection, side="revenue", grain="kan", kan_code="1",
                kan_name="町税", post_supplement_amount=100,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="1", ko_name="町民税", post_supplement_amount=60,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="2", ko_name="固定資産税", post_supplement_amount=40,
            )
            self.assertEqual(0, run_hierarchy(connection))

    def test_genuine_mismatch_child_overruns(self) -> None:
        with make_connection() as connection:
            insert(
                connection, side="revenue", grain="kan", kan_code="1",
                kan_name="町税", post_supplement_amount=100,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="1", ko_name="a", post_supplement_amount=70,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="2", ko_name="b", post_supplement_amount=40,
            )
            self.assertEqual(1, run_hierarchy(connection))

    def test_genuine_mismatch_parent_overruns(self) -> None:
        with make_connection() as connection:
            insert(
                connection, side="revenue", grain="kan", kan_code="1",
                kan_name="町税", post_supplement_amount=150,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="1", ko_name="a", post_supplement_amount=60,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="2", ko_name="b", post_supplement_amount=40,
            )
            self.assertEqual(1, run_hierarchy(connection))

    def test_both_columns_null_behaves_as_before(self) -> None:
        # current_year_amount and post_supplement_amount are both NULL
        # (supplement_amount satisfies the schema CHECK): parent is skipped.
        with make_connection() as connection:
            insert(
                connection, side="revenue", grain="kan", kan_code="1",
                kan_name="町税", supplement_amount=100,
            )
            insert(
                connection, side="revenue", grain="ko", kan_code="1",
                ko_code="1", ko_name="a", supplement_amount=100,
            )
            self.assertIsNone(
                verify_totals._child_sum(
                    connection,
                    connection.execute(
                        "SELECT * FROM budget_line WHERE grain='kan'"
                    ).fetchone(),
                    "ko",
                    "current_year_amount",
                )
            )
            self.assertEqual(0, run_hierarchy(connection))


if __name__ == "__main__":
    unittest.main()
