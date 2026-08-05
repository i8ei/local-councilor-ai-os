#!/usr/bin/env python3
"""Report advisory completeness diagnostics for a minutes SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict

from lcaios.database import sqlite_read_only_uri

# A council head normally answers general questions. The chair (議長) is
# deliberately excluded because chair-only procedural documents are the failure
# shape this diagnostic is intended to expose.
DEFAULT_PRESIDING_OFFICER_TITLES = ("知事", "市長", "区長", "町長", "村長")

# Flag when this share or more of the corpus has no council-head speech.
DEFAULT_PRESIDING_OFFICER_ABSENCE_SHARE_THRESHOLD = 0.5

# A year's document count below this fraction of the positive-year median is low.
DEFAULT_LOW_YEAR_COUNT_RATIO = 0.6

# Candidate-link coverage below this ratio merits inspection. A little variation
# is allowed because an index page can also link to ancillary documents.
DEFAULT_MINIMUM_SESSION_DOCUMENT_COVERAGE_RATIO = 0.8

# Every registered document is expected to contain at least one parsed segment.
DEFAULT_ZERO_SEGMENT_DOCUMENT_THRESHOLD = 0


class CoverageOptions(TypedDict):
    presiding_officer_titles: list[str]
    presiding_officer_absence_share_threshold: float
    low_year_count_ratio: float
    minimum_session_document_coverage_ratio: float
    zero_segment_document_threshold: int


def validate_coverage_config(value: Any = None) -> CoverageOptions:
    """Validate and fill the optional ``coverage`` adapter configuration."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("config.coverage must be a JSON object")

    supported = {
        "presiding_officer_titles",
        "presiding_officer_absence_share_threshold",
        "low_year_count_ratio",
        "minimum_session_document_coverage_ratio",
        "zero_segment_document_threshold",
    }
    unknown = sorted(set(value) - supported)
    if unknown:
        raise ValueError(
            "config.coverage contains unsupported keys: " + ", ".join(unknown)
        )

    titles = value.get(
        "presiding_officer_titles",
        list(DEFAULT_PRESIDING_OFFICER_TITLES),
    )
    if (
        not isinstance(titles, list)
        or not titles
        or not all(isinstance(item, str) and item.strip() for item in titles)
    ):
        raise ValueError(
            "config.coverage.presiding_officer_titles must be a non-empty string list"
        )

    def ratio(name: str, default: float) -> float:
        raw = value.get(name, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"config.coverage.{name} must be a number")
        result = float(raw)
        if not 0 <= result <= 1:
            raise ValueError(f"config.coverage.{name} must be between 0 and 1")
        return result

    zero_threshold = value.get(
        "zero_segment_document_threshold",
        DEFAULT_ZERO_SEGMENT_DOCUMENT_THRESHOLD,
    )
    if (
        isinstance(zero_threshold, bool)
        or not isinstance(zero_threshold, int)
        or zero_threshold < 0
    ):
        raise ValueError(
            "config.coverage.zero_segment_document_threshold "
            "must be a non-negative integer"
        )

    return {
        "presiding_officer_titles": list(dict.fromkeys(item.strip() for item in titles)),
        "presiding_officer_absence_share_threshold": ratio(
            "presiding_officer_absence_share_threshold",
            DEFAULT_PRESIDING_OFFICER_ABSENCE_SHARE_THRESHOLD,
        ),
        "low_year_count_ratio": ratio(
            "low_year_count_ratio",
            DEFAULT_LOW_YEAR_COUNT_RATIO,
        ),
        "minimum_session_document_coverage_ratio": ratio(
            "minimum_session_document_coverage_ratio",
            DEFAULT_MINIMUM_SESSION_DOCUMENT_COVERAGE_RATIO,
        ),
        "zero_segment_document_threshold": zero_threshold,
    }


def _presiding_officer_presence(
    connection: sqlite3.Connection,
    *,
    titles: Sequence[str],
    absence_share_threshold: float,
) -> dict[str, Any]:
    meeting_count = int(
        connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    )
    placeholders = ", ".join("?" for _ in titles)
    present_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM meetings AS m
            WHERE EXISTS (
                SELECT 1
                FROM speeches AS s
                WHERE s.meeting_id = m.meeting_id
                  AND (
                    TRIM(COALESCE(s.speaker_role, '')) IN ({placeholders})
                    OR TRIM(COALESCE(s.speaker, '')) IN ({placeholders})
                  )
            )
            """,
            (*titles, *titles),
        ).fetchone()[0]
    )
    absent_count = meeting_count - present_count
    absent_share = absent_count / meeting_count if meeting_count else None
    return {
        "titles": list(titles),
        "meeting_count": meeting_count,
        "meetings_with_presiding_officer_speeches": present_count,
        "meetings_without_presiding_officer_speeches": absent_count,
        "zero_presence_share": absent_share,
        "absence_share_threshold": absence_share_threshold,
        "flagged": bool(
            absent_share is not None
            and absent_share > 0
            and absent_share >= absence_share_threshold
        ),
    }


def _document_counts_by_year(
    connection: sqlite3.Connection,
    *,
    low_year_count_ratio: float,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT CAST(SUBSTR(date, 1, 4) AS INTEGER) AS year, COUNT(*)
        FROM meetings
        WHERE date IS NOT NULL
          AND SUBSTR(date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
        GROUP BY year
        ORDER BY year
        """
    ).fetchall()
    observed = {int(year): int(count) for year, count in rows}
    median_count = (
        float(statistics.median(observed.values())) if observed else None
    )
    if observed:
        years = range(min(observed), max(observed) + 1)
    else:
        years = range(0)
    counts = []
    for year in years:
        count = observed.get(year, 0)
        flagged = bool(
            median_count is not None
            and median_count > 0
            and count < median_count * low_year_count_ratio
        )
        counts.append({"year": year, "documents": count, "flagged": flagged})

    dated_count = sum(observed.values())
    total_count = int(
        connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    )
    return {
        "counts": counts,
        "median_documents": median_count,
        "low_year_count_ratio": low_year_count_ratio,
        "undated_or_invalid_date_documents": total_count - dated_count,
        "flagged_years": [item["year"] for item in counts if item["flagged"]],
        "flagged": any(item["flagged"] for item in counts),
    }


def _session_document_coverage(
    connection: sqlite3.Connection,
    candidate_sessions: Sequence[Mapping[str, Any]],
    *,
    minimum_coverage_ratio: float,
) -> dict[str, Any]:
    ingested_by_source = {
        str(source): int(count)
        for source, count in connection.execute(
            """
            SELECT discovered_from, COUNT(DISTINCT meeting_id)
            FROM provenance
            WHERE discovered_from IS NOT NULL AND discovered_from != ''
            GROUP BY discovered_from
            """
        ).fetchall()
    }
    sessions = []
    for candidate in candidate_sessions:
        session_key = str(candidate["session_key"])
        candidate_count = int(candidate["candidate_document_links"])
        if candidate_count < 1:
            continue
        ingested_count = ingested_by_source.get(session_key, 0)
        coverage_ratio = ingested_count / candidate_count
        sessions.append(
            {
                "session_key": session_key,
                "candidate_document_links": candidate_count,
                "ingested_documents": ingested_count,
                "coverage_ratio": coverage_ratio,
                "flagged": coverage_ratio < minimum_coverage_ratio,
            }
        )
    sessions.sort(key=lambda item: item["session_key"])
    return {
        "minimum_coverage_ratio": minimum_coverage_ratio,
        "sessions": sessions,
        "flagged_sessions": [
            item["session_key"] for item in sessions if item["flagged"]
        ],
        "flagged": any(item["flagged"] for item in sessions),
    }


def _zero_segment_documents(
    connection: sqlite3.Connection,
    *,
    threshold: int,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            m.meeting_id, m.meeting_name, m.date, m.source_url,
            COUNT(s.speech_id) AS segment_count
        FROM meetings AS m
        LEFT JOIN speeches AS s ON s.meeting_id = m.meeting_id
        GROUP BY m.meeting_id
        HAVING COUNT(s.speech_id) = 0
        ORDER BY COALESCE(m.date, ''), m.meeting_name, m.meeting_id
        """
    ).fetchall()
    documents = [
        {
            "meeting_id": str(row[0]),
            "meeting_name": str(row[1]),
            "date": row[2],
            "source_url": str(row[3]),
        }
        for row in rows
    ]
    return {
        "count": len(documents),
        "threshold": threshold,
        "documents": documents,
        "flagged": len(documents) > threshold,
    }


def diagnose_coverage(
    connection: sqlite3.Connection,
    *,
    options: CoverageOptions | None = None,
    candidate_sessions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute non-fatal completeness diagnostics from an existing database."""
    resolved = options or validate_coverage_config()
    presence = _presiding_officer_presence(
        connection,
        titles=resolved["presiding_officer_titles"],
        absence_share_threshold=resolved[
            "presiding_officer_absence_share_threshold"
        ],
    )
    yearly = _document_counts_by_year(
        connection,
        low_year_count_ratio=resolved["low_year_count_ratio"],
    )
    zero_segments = _zero_segment_documents(
        connection,
        threshold=resolved["zero_segment_document_threshold"],
    )
    diagnostics: dict[str, Any] = {
        "status": "advisory",
        "presiding_officer_presence": presence,
        "document_counts_by_year": yearly,
        "zero_segment_documents": zero_segments,
    }
    flagged = [presence["flagged"], yearly["flagged"], zero_segments["flagged"]]
    if candidate_sessions is not None:
        session_coverage = _session_document_coverage(
            connection,
            candidate_sessions,
            minimum_coverage_ratio=resolved[
                "minimum_session_document_coverage_ratio"
            ],
        )
        diagnostics["session_document_coverage"] = session_coverage
        flagged.append(session_coverage["flagged"])
    diagnostics["attention_required"] = any(flagged)
    return diagnostics


def _options_from_cli(args: argparse.Namespace) -> CoverageOptions:
    config: Any = None
    if args.config:
        path = Path(args.config)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Adapter config is not valid JSON: {path}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Adapter config must be a JSON object")
        config = raw.get("coverage")
    resolved = validate_coverage_config(config)
    overrides: dict[str, Any] = dict(resolved)
    if args.presiding_officer_title:
        overrides["presiding_officer_titles"] = list(
            dict.fromkeys(args.presiding_officer_title)
        )
    for name in (
        "presiding_officer_absence_share_threshold",
        "low_year_count_ratio",
        "minimum_session_document_coverage_ratio",
        "zero_segment_document_threshold",
    ):
        override = getattr(args, name)
        if override is not None:
            overrides[name] = override
    return validate_coverage_config(overrides)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Minutes SQLite database")
    parser.add_argument(
        "--config",
        help="Static adapter JSON config containing optional coverage settings",
    )
    parser.add_argument(
        "--presiding-officer-title",
        action="append",
        help="首長側の役職名。複数指定可（指定時は既定値・設定を置換）",
    )
    parser.add_argument(
        "--presiding-officer-absence-share-threshold",
        type=float,
        help="首長側発言がない文書割合の注意閾値（0〜1）",
    )
    parser.add_argument(
        "--low-year-count-ratio",
        type=float,
        help="年別中央値に対する少数年の注意閾値（0〜1）",
    )
    parser.add_argument(
        "--minimum-session-document-coverage-ratio",
        type=float,
        help="候補リンク数に対する取込文書数の注意閾値（0〜1）",
    )
    parser.add_argument(
        "--zero-segment-document-threshold",
        type=int,
        help="許容する発言0件文書数（0以上）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        options = _options_from_cli(args)
        database = Path(args.db).expanduser()
        if not database.is_file():
            raise ValueError(f"Database does not exist: {database}")
        with closing(
            sqlite3.connect(sqlite_read_only_uri(database), uri=True)
        ) as connection:
            diagnostics = diagnose_coverage(connection, options=options)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
