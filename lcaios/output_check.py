"""Detect internal leakage and hidden content in public-bound Markdown.

This scanner never edits or publishes. It reports findings with locations and
reasons so a person decides whether each match is a real problem. It is a
leak detector, not an authority on truth: it cannot confirm that numbers or
citations are correct.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# Absolute paths, vault-internal control directories, and known local roots.
_PATH_PATTERNS = (
    re.compile(r"\.local-councilor-ai-os\b"),
    re.compile(r"\btaracho-archive\b"),
    re.compile(r"Obsidian Vault"),
    re.compile(r"(?<![\w/])(?:/Users|/home)/[^\s)]+"),
    re.compile(r"(?<![\w/])/(?:private|var|tmp)/[^\s)]+"),
    re.compile(r"[A-Za-z]:\\[^\s)]+"),
)
_UNVERIFIED_PATTERN = re.compile(
    r"［要確認］|要裏取り|未確認|仮説|(?<!［)要確認(?!］)"
)
_CLASSIFICATION_PATTERNS = (
    re.compile(r"visibility:\s*(internal|sensitive)", re.IGNORECASE),
    re.compile(r"機密度:\s*内部"),
    re.compile(r"機密度:\s*(内部のみ|要配慮|センシティブ)"),
    re.compile(r"\[!(internal|sensitive)\]", re.IGNORECASE),
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bappId\s*[=:]\s*[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|token)\s*[=:]\s*[A-Za-z0-9._\-]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
)
_WIKILINK_PATTERN = re.compile(r"\[\[[^\]\n]+\]\]")
_MARKDOWN_COMMENT = re.compile(r"%%.+?%%", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Zero-width and bidi-control characters that can hide content or reorder text.
_INVISIBLE_CHARS = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE",
    "\u200e": "LEFT-TO-RIGHT MARK",
    "\u200f": "RIGHT-TO-LEFT MARK",
    "\u202a": "LEFT-TO-RIGHT EMBEDDING",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE",
    "\u2066": "LEFT-TO-RIGHT ISOLATE",
    "\u2067": "RIGHT-TO-LEFT ISOLATE",
    "\u2069": "POP DIRECTIONAL ISOLATE",
}


def _snippet(text: str, start: int, end: int, *, radius: int = 24) -> str:
    fragment = text[max(0, start - radius): min(len(text), end + radius)]
    return re.sub(r"\s+", " ", fragment).strip()


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _finding(
    severity: str,
    code: str,
    message: str,
    *,
    line: int,
    snippet: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "line": line,
        "snippet": snippet,
    }


def scan_text(text: str) -> list[dict[str, Any]]:
    """Return ordered findings for one Markdown body."""

    findings: list[dict[str, Any]] = []

    for match in _WIKILINK_PATTERN.finditer(text):
        findings.append(
            _finding(
                "error",
                "internal_wikilink",
                "Vault内部wikilinkが残っています",
                line=_line_number(text, match.start()),
                snippet=match.group(0),
            )
        )

    for pattern in _PATH_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                _finding(
                    "error",
                    "internal_or_absolute_path",
                    "内部パスまたは絶対パスが含まれています",
                    line=_line_number(text, match.start()),
                    snippet=_snippet(text, match.start(), match.end()),
                )
            )

    for pattern in _CLASSIFICATION_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                _finding(
                    "error",
                    "internal_classification_marker",
                    "内部・要配慮区分の標識が含まれています",
                    line=_line_number(text, match.start()),
                    snippet=_snippet(text, match.start(), match.end()),
                )
            )

    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                _finding(
                    "error",
                    "possible_secret",
                    "秘密値らしい文字列が含まれています",
                    line=_line_number(text, match.start()),
                    snippet="（秘匿のため省略）",
                )
            )

    for match in _UNVERIFIED_PATTERN.finditer(text):
        findings.append(
            _finding(
                "error",
                "unverified_marker",
                f"未検証の標識「{match.group(0)}」が残っています",
                line=_line_number(text, match.start()),
                snippet=_snippet(text, match.start(), match.end()),
            )
        )

    for pattern in (_MARKDOWN_COMMENT, _HTML_COMMENT):
        for match in pattern.finditer(text):
            findings.append(
                _finding(
                    "warning",
                    "hidden_comment",
                    "公開稿に隠しコメントが含まれています",
                    line=_line_number(text, match.start()),
                    snippet=_snippet(text, match.start(), match.end()),
                )
            )

    for index, character in enumerate(text):
        name = _INVISIBLE_CHARS.get(character)
        if name is not None:
            findings.append(
                _finding(
                    "warning",
                    "invisible_character",
                    f"不可視・制御文字が含まれています: {name}",
                    line=_line_number(text, index),
                    snippet=f"U+{ord(character):04X}",
                )
            )
        elif (
            unicodedata.category(character) == "Cc"
            and character not in "\t\n\r"
        ):
            findings.append(
                _finding(
                    "warning",
                    "control_character",
                    "制御文字が含まれています",
                    line=_line_number(text, index),
                    snippet=f"U+{ord(character):04X}",
                )
            )

    return _ordered(findings)


def _ordered(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[int, str, str], dict[str, Any]] = {}
    for item in findings:
        key = (item["line"], item["code"], item["snippet"])
        unique.setdefault(key, item)
    return sorted(
        unique.values(),
        key=lambda item: (
            item["line"],
            SEVERITY_ORDER.get(item["severity"], 99),
            item["code"],
            item["snippet"],
        ),
    )


def check_output_file(path: str | Path) -> dict[str, Any]:
    """Scan one Markdown file and return a read-only report."""

    file_path = Path(path).expanduser()
    text = file_path.read_text(encoding="utf-8")
    findings = scan_text(text)
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in findings:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return {
        "schema_version": 1,
        "file": str(file_path),
        "findings": findings,
        "counts": counts,
        "read_only": True,
    }
