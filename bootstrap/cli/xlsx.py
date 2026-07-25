"""Minimal stdlib XLSX reader with label-based header discovery."""

from __future__ import annotations

import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class XlsxError(RuntimeError):
    """Raised when a workbook cannot be interpreted safely."""


@dataclass(frozen=True)
class Cell:
    """One non-empty worksheet cell."""

    ref: str
    row: int
    column: int
    value: str


@dataclass(frozen=True)
class HeaderMatch:
    """Resolved column location and whether an alias fallback was used."""

    key: str
    column: int
    cell: Cell
    matched_label: str
    fallback_used: bool


@dataclass(frozen=True)
class Worksheet:
    """A worksheet represented as sparse cells."""

    name: str
    path: str
    cells: tuple[Cell, ...]

    def rows(self) -> dict[int, dict[int, Cell]]:
        """Return cells indexed by row and column."""

        result: dict[int, dict[int, Cell]] = {}
        for cell in self.cells:
            result.setdefault(cell.row, {})[cell.column] = cell
        return result


def normalize_label(value: str) -> str:
    """Normalize display differences without changing semantic characters."""

    return re.sub(
        r"[\s\u3000]+", "", unicodedata.normalize("NFC", str(value or ""))
    ).strip()


def column_number(cell_ref: str) -> int:
    """Convert an A1 column reference to a one-based integer."""

    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        raise XlsxError(f"不正なセル参照です: {cell_ref}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.iter(f"{{{XLSX_NS}}}t"))
        for item in root.findall(f"{{{XLSX_NS}}}si")
    ]


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except KeyError as error:
        raise XlsxError(f"XLSXの必須部品がありません: {error}") from error
    targets = {
        relation.get("Id", ""): relation.get("Target", "")
        for relation in relations.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{XLSX_NS}}}sheet"):
        relation_id = sheet.get(f"{{{OFFICE_REL_NS}}}id", "")
        target = targets.get(relation_id, "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        sheets.append((sheet.get("name", ""), path))
    if not sheets:
        raise XlsxError("ワークシートを発見できません")
    return sheets


def _read_cells(
    archive: zipfile.ZipFile, path: str, strings: list[str]
) -> tuple[Cell, ...]:
    try:
        root = ET.fromstring(archive.read(path))
    except KeyError as error:
        raise XlsxError(f"ワークシート部品がありません: {path}") from error
    cells: list[Cell] = []
    for node in root.findall(f".//{{{XLSX_NS}}}c"):
        ref = node.get("r", "")
        row_match = re.search(r"\d+$", ref)
        if not ref or not row_match:
            continue
        cell_type = node.get("t")
        if cell_type == "inlineStr":
            value = "".join(
                item.text or "" for item in node.iter(f"{{{XLSX_NS}}}t")
            )
        else:
            value_node = node.find(f"{{{XLSX_NS}}}v")
            value = "" if value_node is None else value_node.text or ""
            if cell_type == "s" and value:
                try:
                    value = strings[int(value)]
                except (IndexError, ValueError) as error:
                    raise XlsxError(f"共有文字列参照が不正です: {ref}") from error
            elif cell_type == "b":
                value = "TRUE" if value == "1" else "FALSE"
        if value != "":
            cells.append(
                Cell(
                    ref=ref,
                    row=int(row_match.group(0)),
                    column=column_number(ref),
                    value=value,
                )
            )
    return tuple(cells)


def read_workbook(path: str | Path) -> list[Worksheet]:
    """Read all worksheets from an XLSX file."""

    try:
        with zipfile.ZipFile(path) as archive:
            strings = _shared_strings(archive)
            return [
                Worksheet(name, sheet_path, _read_cells(archive, sheet_path, strings))
                for name, sheet_path in _sheet_paths(archive)
            ]
    except zipfile.BadZipFile as error:
        raise XlsxError(f"有効なXLSXではありません: {path}") from error


def _label_matches(cell_value: str, wanted: str) -> bool:
    cell_label = normalize_label(cell_value)
    expected = normalize_label(wanted)
    # Government workbooks often append units, footnotes, or phonetic guides
    # to the visible header text in the same cell.
    return cell_label.startswith(expected)


def find_header_columns(
    cells: Iterable[Cell],
    labels: dict[str, tuple[str, ...]],
    *,
    max_row: int = 100,
) -> dict[str, HeaderMatch]:
    """Resolve columns by header labels, using aliases only as recorded fallbacks."""

    candidates = [cell for cell in cells if cell.row <= max_row]
    matches: dict[str, HeaderMatch] = {}
    for key, aliases in labels.items():
        found: list[tuple[int, int, Cell, str]] = []
        for alias_index, alias in enumerate(aliases):
            for cell in candidates:
                if _label_matches(cell.value, alias):
                    found.append((alias_index, cell.row, cell, alias))
        if not found:
            raise XlsxError(
                f"見出し「{aliases[0]}」を先頭{max_row}行から発見できません"
            )
        alias_index = min(item[0] for item in found)
        winning = [item for item in found if item[0] == alias_index]
        columns = {item[2].column for item in winning}
        if len(columns) > 1:
            first_by_column: dict[int, Cell] = {}
            for _, _, candidate, _ in winning:
                current = first_by_column.get(candidate.column)
                if current is None or candidate.row < current.row:
                    first_by_column[candidate.column] = candidate
            details = ", ".join(
                f"{cell.ref}「{cell.value}」"
                for _, cell in sorted(first_by_column.items())
            )
            raise XlsxError(
                f"見出しキー「{key}」が複数列に一致しました: "
                f"{details}"
            )
        _, _, cell, alias = min(winning, key=lambda item: item[1])
        matches[key] = HeaderMatch(
            key=key,
            column=cell.column,
            cell=cell,
            matched_label=alias,
            fallback_used=alias_index > 0,
        )
    return matches
