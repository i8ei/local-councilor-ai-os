#!/usr/bin/env python3
"""Create small passing and failing settlement databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def _provenance(page: int) -> dict[str, Any]:
    return {
        "raw_value": f"例の原表行{page}",
        "unit": "円",
        "as_of": "例年度",
        "definition": "例として作成した合成データ",
        "source_name": "例決算書",
        "source_url": "https://example.invalid/settlement.pdf",
        "source_locator": json.dumps(
            {"pdf_page": page, "table": "例表"},
            ensure_ascii=False,
        ),
        "fetched_at": "2099-01-01T00:00:00Z",
        "verification_state": "verified",
        "fetch_cache_key": "example-cache-key",
        "robots_decision": "not_applicable",
        "request_time": "2099-01-01T00:00:00Z",
        "print_page": f"例{page}",
        "pdf_page": page,
    }


def _insert_summary(
    connection: sqlite3.Connection,
    side: str,
    values: tuple[int, int, int, int],
    page: int,
) -> None:
    budget, actual, third, fourth = values
    provenance = _provenance(page)
    connection.execute(
        """
        INSERT INTO settlement_summary (
            fiscal_year, account_name, side, kan_code, kan_name,
            budget_current_amount, collected_amount,
            uncollectible_amount, outstanding_amount,
            spent_amount, carryover_amount, unused_amount,
            raw_value, unit, as_of, definition, source_name, source_url,
            source_locator, fetched_at, verification_state,
            fetch_cache_key, robots_decision, request_time,
            print_page, pdf_page
        ) VALUES (
            :fiscal_year, :account_name, :side, :kan_code, :kan_name,
            :budget, :collected,
            :uncollectible, :outstanding,
            :spent, :carryover, :unused,
            :raw_value, :unit, :as_of, :definition, :source_name, :source_url,
            :source_locator, :fetched_at, :verification_state,
            :fetch_cache_key, :robots_decision, :request_time,
            :print_page, :pdf_page
        )
        """,
        {
            "fiscal_year": 2099,
            "account_name": "例会計",
            "side": side,
            "kan_code": "例款",
            "kan_name": "例款名",
            "budget": budget,
            "collected": actual if side == "revenue" else None,
            "uncollectible": third if side == "revenue" else None,
            "outstanding": fourth if side == "revenue" else None,
            "spent": actual if side == "expenditure" else None,
            "carryover": third if side == "expenditure" else None,
            "unused": fourth if side == "expenditure" else None,
            **provenance,
        },
    )


def _insert_revenue(
    connection: sqlite3.Connection,
    ko_code: str,
    values: tuple[int, int, int, int],
    page: int,
) -> None:
    provenance = _provenance(page)
    connection.execute(
        """
        INSERT INTO settlement_revenue (
            fiscal_year, account_name, kan_code, kan_name, ko_code, ko_name,
            budget_current_amount, collected_amount,
            uncollectible_amount, outstanding_amount,
            raw_value, unit, as_of, definition, source_name, source_url,
            source_locator, fetched_at, verification_state,
            fetch_cache_key, robots_decision, request_time,
            print_page, pdf_page
        ) VALUES (
            2099, '例会計', '例款', '例款名', :ko_code, :ko_name,
            :budget, :actual, :third, :fourth,
            :raw_value, :unit, :as_of, :definition, :source_name, :source_url,
            :source_locator, :fetched_at, :verification_state,
            :fetch_cache_key, :robots_decision, :request_time,
            :print_page, :pdf_page
        )
        """,
        {
            "ko_code": ko_code,
            "ko_name": f"{ko_code}名",
            "budget": values[0],
            "actual": values[1],
            "third": values[2],
            "fourth": values[3],
            **provenance,
        },
    )


def _insert_expenditure(
    connection: sqlite3.Connection,
    moku_code: str,
    setsu_code: str,
    item_values: tuple[int, int, int, int],
    section_values: tuple[int, int, int, int],
    page: int,
) -> None:
    provenance = _provenance(page)
    connection.execute(
        """
        INSERT INTO settlement_expenditure (
            fiscal_year, account_name, kan_code, kan_name,
            ko_code, ko_name, moku_code, moku_name, setsu_code, setsu_name,
            item_budget_current_amount, item_spent_amount,
            item_carryover_amount, item_unused_amount,
            section_budget_current_amount, section_spent_amount,
            section_carryover_amount, section_unused_amount,
            raw_value, unit, as_of, definition, source_name, source_url,
            source_locator, fetched_at, verification_state,
            fetch_cache_key, robots_decision, request_time,
            print_page, pdf_page
        ) VALUES (
            2099, '例会計', '例款', '例款名',
            '例項', '例項名', :moku_code, :moku_name, :setsu_code, :setsu_name,
            :item_budget, :item_actual, :item_third, :item_fourth,
            :section_budget, :section_actual, :section_third, :section_fourth,
            :raw_value, :unit, :as_of, :definition, :source_name, :source_url,
            :source_locator, :fetched_at, :verification_state,
            :fetch_cache_key, :robots_decision, :request_time,
            :print_page, :pdf_page
        )
        """,
        {
            "moku_code": moku_code,
            "moku_name": f"{moku_code}名",
            "setsu_code": setsu_code,
            "setsu_name": f"{setsu_code}名",
            "item_budget": item_values[0],
            "item_actual": item_values[1],
            "item_third": item_values[2],
            "item_fourth": item_values[3],
            "section_budget": section_values[0],
            "section_actual": section_values[1],
            "section_third": section_values[2],
            "section_fourth": section_values[3],
            **provenance,
        },
    )


def _build(path: Path, failing: bool) -> None:
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _insert_summary(
            connection,
            "revenue",
            (100, 81 if failing else 80, 5, 15),
            1,
        )
        _insert_summary(connection, "expenditure", (100, 70, 10, 20), 2)
        _insert_revenue(connection, "例項1", (60, 50, 2, 8), 3)
        _insert_revenue(connection, "例項2", (40, 30, 3, 7), 4)
        _insert_expenditure(
            connection,
            "例目1",
            "例節1",
            (60, 40, 5, 15),
            (30, 25, 5, 0),
            5,
        )
        _insert_expenditure(
            connection,
            "例目1",
            "例節2",
            (60, 40, 5, 15),
            (30, 16 if failing else 15, 0, 15),
            6,
        )
        _insert_expenditure(
            connection,
            "例目2",
            "例節1",
            (40, 30, 5, 5),
            (40, 30, 5, 5),
            7,
        )


def main() -> int:
    _build(TEST_DIR / "passing.db", failing=False)
    _build(TEST_DIR / "failing.db", failing=True)
    print(f"created: {TEST_DIR / 'passing.db'}")
    print(f"created: {TEST_DIR / 'failing.db'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
