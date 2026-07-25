"""Read-only SQLite verification and schema-compatibility decisions."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

SUPPORTED_MAJOR = 1
KNOWN_BOOTSTRAP_MINOR = 0
REQUIRED_BOOTSTRAP_TABLES = ("municipality", "indicator", "build_metadata")
_FTS_TOKENIZER_RE = re.compile(
    r"\btokenize\s*=\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s,)]+))",
    re.IGNORECASE,
)


class FtsSchemaStatus(TypedDict):
    """Outcome of ensuring an optional FTS schema."""

    tokenizer: str
    rebuilt: bool
    previous_tokenizer: str | None


def _supports_fts5_tokenizer(
    connection: sqlite3.Connection,
    tokenizer: str,
) -> bool:
    table_name = f"__lcaios_fts_probe_{tokenizer}_{id(connection):x}"
    created = False
    try:
        connection.execute(
            f'CREATE VIRTUAL TABLE temp."{table_name}" '
            f"USING fts5(value, tokenize='{tokenizer}')"
        )
        created = True
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        if created:
            connection.execute(f'DROP TABLE temp."{table_name}"')


def supports_fts5(connection: sqlite3.Connection) -> bool:
    """Return whether the connected SQLite build provides FTS5."""

    return _supports_fts5_tokenizer(connection, "unicode61")


def supports_fts5_trigram(connection: sqlite3.Connection) -> bool:
    """Return whether the connected SQLite build supports the FTS5 trigram tokenizer."""

    return _supports_fts5_tokenizer(connection, "trigram")


def fts5_table_tokenizer(
    connection: sqlite3.Connection,
    table_name: str,
) -> str | None:
    """Return the tokenizer named in an existing FTS table's CREATE statement."""

    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    match = _FTS_TOKENIZER_RE.search(row[0])
    if match is None:
        return None
    specification = next(
        value for value in match.groups() if value is not None
    )
    return specification.split()[0].casefold()


def sqlite_read_only_uri(path: str | Path) -> str:
    """Return a read-only file URI that never creates the database.

    The path is resolved to an absolute ``file://`` URI so that
    drive-lettered Windows paths become the canonical ``file:///C:/...``
    form. A bare ``file:C:/...`` would be treated as a relative path by
    SQLite and could resolve against the current drive's working directory.
    """

    absolute = Path(path).expanduser().resolve(strict=False)
    return f"{absolute.as_uri()}?mode=ro"


def parse_schema_version(value: Any) -> tuple[int, int] | None:
    """Parse a ``major`` or ``major.minor`` version string."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    return major, minor


def evaluate_schema_compatibility(
    value: Any,
    *,
    supported_major: int = SUPPORTED_MAJOR,
    known_minor: int = KNOWN_BOOTSTRAP_MINOR,
) -> dict[str, Any]:
    """Decide whether a stored schema version can be read by this build."""

    parsed = parse_schema_version(value)
    if parsed is None:
        return {
            "state": "unknown",
            "compatible": False,
            "reason": "schema_versionを解釈できません",
            "schema_version": None if value is None else str(value),
        }
    major, minor = parsed
    normalized = f"{major}.{minor}"
    if major != supported_major:
        return {
            "state": "incompatible_major",
            "compatible": False,
            "reason": (
                f"未対応のmajor schema {major}。原典から再構築が必要です"
            ),
            "schema_version": normalized,
        }
    if minor > known_minor:
        return {
            "state": "compatible_newer_minor",
            "compatible": True,
            "reason": (
                "既知より新しいminor schema。追加列は無視して読み取ります"
            ),
            "schema_version": normalized,
        }
    return {
        "state": "compatible",
        "compatible": True,
        "reason": "対応schema",
        "schema_version": normalized,
    }


def verify_bootstrap_database(path: str | Path) -> dict[str, Any]:
    """Verify a Tier 1 database without modifying it."""

    database_path = Path(path).expanduser()
    checks: list[dict[str, Any]] = []
    if not database_path.is_file():
        return {
            "database": str(database_path),
            "ok": False,
            "checks": [
                {
                    "name": "database_exists",
                    "status": "failed",
                    "detail": "databaseが存在しません",
                }
            ],
            "schema": {"state": "unknown", "compatible": False},
        }

    schema: dict[str, Any] = {"state": "unknown", "compatible": False}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            sqlite_read_only_uri(database_path), uri=True
        )
        connection.execute("PRAGMA query_only = ON")
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        integrity_ok = integrity == ["ok"]
        checks.append(
            {
                "name": "sqlite_integrity",
                "status": "passed" if integrity_ok else "failed",
                "detail": "; ".join(integrity),
            }
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = [name for name in REQUIRED_BOOTSTRAP_TABLES if name not in tables]
        checks.append(
            {
                "name": "required_tables",
                "status": "passed" if not missing else "failed",
                "detail": ", ".join(missing),
            }
        )
        stored_schema = None
        if "build_metadata" in tables:
            row = connection.execute(
                "SELECT value FROM build_metadata WHERE key = 'schema_version'"
            ).fetchone()
            stored_schema = row[0] if row else None
        schema = evaluate_schema_compatibility(stored_schema)
        checks.append(
            {
                "name": "schema_compatibility",
                "status": "passed" if schema["compatible"] else "failed",
                "detail": f"{schema['schema_version']} ({schema['state']})",
            }
        )
    except sqlite3.Error as error:
        checks.append(
            {
                "name": "sqlite_open",
                "status": "failed",
                "detail": str(error),
            }
        )
    finally:
        if connection is not None:
            connection.close()

    ok = bool(checks) and all(item["status"] == "passed" for item in checks)
    return {
        "database": str(database_path),
        "ok": ok,
        "checks": checks,
        "schema": schema,
    }
