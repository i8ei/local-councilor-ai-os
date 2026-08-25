"""Tiny text helpers shared across adapters and vendor modules.

These exist to keep one implementation each of the trivially duplicated
normalizers (whitespace collapse, era-year conversion, stable ids).
"""

from __future__ import annotations

import hashlib
import re

ERA_BASES: dict[str, int] = {"令和": 2018, "平成": 1988, "昭和": 1925}


def stable_id(prefix: str, value: str) -> str:
    """Build a deterministic identifier: ``prefix_`` + 24 hex chars."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def collapse_ascii(value: str) -> str:
    """Normalize ASCII whitespace while keeping Japanese full-width spaces."""
    return re.sub(r"[ \t\r\v]+", " ", value).strip()


def collapse_fullwidth(value: str) -> str:
    """Collapse ASCII and full-width whitespace to single spaces."""
    return re.sub(r"[ \t\r\v\u3000]+", " ", value).strip()


def era_year(era_name: str, era_number: int | str) -> int:
    """Convert a Japanese era year to a Gregorian year."""
    return ERA_BASES[era_name] + (1 if str(era_number) == "元" else int(era_number))
