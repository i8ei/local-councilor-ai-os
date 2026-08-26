"""Helpers shared by the budget_review and settlement_review modules.

Both reviews implement the same CSV-contract shape (normalize cells,
require provenance, verify totals); these are the pieces that are truly
identical, extracted so they cannot drift.
"""

from __future__ import annotations

import hashlib
import json


def sha24(value: str) -> str:
    """24-hex-char sha256 digest of the UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def none_if_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped != "" else None


def integer(value: str | None, field: str) -> int | None:
    cleaned = none_if_empty(value)
    if cleaned is None:
        return None
    normalized = cleaned.replace(",", "").replace("△", "-").replace("−", "-")
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an integer-compatible value: {value!r}"
        ) from exc


def json_locator(value: str | None) -> str:
    cleaned = none_if_empty(value)
    if cleaned is None:
        raise ValueError("source_locator is required")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = {"locator": cleaned}
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


# Aliases matching the names used inside the review modules.
_integer = integer
_json_locator = json_locator
_none_if_empty = none_if_empty
