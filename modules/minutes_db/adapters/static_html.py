"""Config-driven adapter for minutes published as static HTML or PDF files."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .base import Adapter, FetchResult, polite_fetch

_BLOCK_TAGS = {
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
_SPEAKER_MARK_RE = re.compile(r"^[○◯●◎]\s*(.*?)\s*$")
_STAGE_DIRECTION_RE = re.compile(r"^[〔［\[].+[〕］\]]$")
_ROLE_RE = re.compile(
    r"^(?P<role>"
    r"議長|副議長|委員長|副委員長|町長|市長|村長|区長|"
    r"副町長|副市長|副村長|教育長|教育委員長|代表監査委員|"
    r"総務課長|企画課長|財政課長|住民課長|福祉課長|建設課長|"
    r"農林課長|答弁者"
    r")"
    r"(?:[（(](?P<name>[^）)]+)[）)])?"
    r"(?P<text>.*)$"
)
_PERSON_RE = re.compile(
    r"^(?P<name>.{1,40}?君)(?:[ \t\u3000]+(?P<text>.*)|$)"
)
_GENERIC_SPEAKER_RE = re.compile(
    r"^(?P<name>[^ \t\u3000]{1,30})[ \t\u3000]+(?P<text>.*)$"
)


class _DocumentParser(HTMLParser):
    """Collect links, title, and visible text with block boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._title_depth = 0
        self._ignored_depth = 0
        self._current_href: str | None = None
        self._current_link_text: list[str] = []
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self._text.append("\n")
        if tag == "title":
            self._title_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "a" and self._current_href is not None:
            label = _collapse_inline("".join(self._current_link_text))
            self.links.append((self._current_href, label))
            self._current_href = None
            self._current_link_text = []
        if tag in _BLOCK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._text.append(data)
        if self._title_depth:
            self.title += data
        if self._current_href is not None:
            self._current_link_text.append(data)

    def visible_text(self) -> str:
        lines = [_collapse_inline(line) for line in "".join(self._text).splitlines()]
        return "\n".join(line for line in lines if line)


def _collapse_inline(value: str) -> str:
    """Normalize ASCII spacing while retaining Japanese full-width separators."""
    return re.sub(r"[ \t\r\v]+", " ", value).strip()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _strip_speaker_suffix(name: str) -> str:
    return re.sub(r"(?:議員)?君$", "", name.strip(" \t\u3000"))


def _parse_speaker(value: str) -> tuple[str, str | None, str]:
    """Split a marked line into speaker, role, and initial utterance."""
    role_match = _ROLE_RE.match(value)
    if role_match:
        role = role_match.group("role")
        name = role_match.group("name")
        speaker = _strip_speaker_suffix(name) if name else role
        return speaker, role, role_match.group("text").strip(" \t\u3000")

    person_match = _PERSON_RE.match(value)
    if person_match:
        return (
            _strip_speaker_suffix(person_match.group("name")),
            "議員",
            (person_match.group("text") or "").strip(" \t\u3000"),
        )

    generic_match = _GENERIC_SPEAKER_RE.match(value)
    if generic_match:
        return (
            generic_match.group("name").strip(),
            None,
            generic_match.group("text").strip(),
        )
    return value.strip(), None, ""


def segment_speeches(text: str) -> list[dict[str, Any]]:
    """Segment text by Japanese speaker marks, with lossless fallback chunks."""
    pages = text.split("\f")
    records: list[tuple[str, str]] = []
    for page_number, page in enumerate(pages, start=1):
        for line_number, raw_line in enumerate(page.splitlines(), start=1):
            line = _collapse_inline(raw_line)
            if line:
                locator = (
                    f"page:{page_number}#line:{line_number}"
                    if len(pages) > 1
                    else f"text-line:{line_number}"
                )
                records.append((line, locator))

    has_speaker_marks = any(_SPEAKER_MARK_RE.match(line) for line, _ in records)
    if not has_speaker_marks:
        return _fallback_segments(text)

    speeches: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    preamble: list[str] = []
    preamble_locator = "text-line:1"

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        pending["text"] = "\n".join(pending.pop("_parts")).strip()
        if pending["text"]:
            pending["seq"] = len(speeches) + 1
            speeches.append(pending)
        pending = None

    for line, locator in records:
        marker_match = _SPEAKER_MARK_RE.match(line)
        if marker_match:
            if preamble:
                speeches.append(
                    {
                        "seq": len(speeches) + 1,
                        "speaker": None,
                        "speaker_role": "記録",
                        "text": "\n".join(preamble),
                        "locator": preamble_locator,
                    }
                )
                preamble = []
            flush()
            speaker, role, initial_text = _parse_speaker(marker_match.group(1))
            pending = {
                "speaker": speaker or None,
                "speaker_role": role,
                "locator": locator,
                "_parts": [initial_text] if initial_text else [],
            }
            continue

        if _STAGE_DIRECTION_RE.match(line):
            if pending is not None:
                pending["_parts"].append(line)
            else:
                speeches.append(
                    {
                        "seq": len(speeches) + 1,
                        "speaker": None,
                        "speaker_role": "記録",
                        "text": line,
                        "locator": locator,
                    }
                )
            continue

        if pending is not None:
            pending["_parts"].append(line)
        else:
            if not preamble:
                preamble_locator = locator
            preamble.append(line)

    flush()
    if preamble:
        speeches.append(
            {
                "seq": len(speeches) + 1,
                "speaker": None,
                "speaker_role": "記録",
                "text": "\n".join(preamble),
                "locator": preamble_locator,
            }
        )
    return speeches


def _fallback_segments(text: str) -> list[dict[str, Any]]:
    """Preserve usable chunks when the source has no speaker structure."""
    segments: list[dict[str, Any]] = []
    pages = text.split("\f")
    if len(pages) > 1:
        candidates = [
            (f"page:{number}", _collapse_inline(page.replace("\n", " ")))
            for number, page in enumerate(pages, start=1)
        ]
    else:
        paragraphs = [
            _collapse_inline(part)
            for part in re.split(r"\n\s*\n|\n", text)
            if _collapse_inline(part)
        ]
        candidates = [
            (f"paragraph:{number}", paragraph)
            for number, paragraph in enumerate(paragraphs, start=1)
        ]
    for locator, value in candidates:
        if not value:
            continue
        segments.append(
            {
                "seq": len(segments) + 1,
                "speaker": None,
                "speaker_role": None,
                "text": value,
                "locator": locator,
            }
        )
    return segments


def _infer_date(*values: str) -> str | None:
    combined = " ".join(value for value in values if value)
    western = re.search(r"(?<!\d)(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?", combined)
    if western:
        year, month, day = (int(part) for part in western.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    era = re.search(
        r"(令和|平成|昭和)(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        combined,
    )
    if not era:
        return None
    bases = {"令和": 2018, "平成": 1988, "昭和": 1925}
    era_year = 1 if era.group(2) == "元" else int(era.group(2))
    year = bases[era.group(1)] + era_year
    return f"{year:04d}-{int(era.group(3)):02d}-{int(era.group(4)):02d}"


class StaticHtmlAdapter(Adapter):
    """Discover and normalize static municipal minute documents."""

    adapter_name = "static_html"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.config = self._validate_config(config)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._meetings: dict[str, dict[str, Any]] = {}
        self._discovery_candidates: list[dict[str, Any]] = []

    @property
    def discovery_candidates(self) -> list[dict[str, Any]]:
        """Return the most recent discovery decisions without mutable internals."""

        return [dict(item) for item in self._discovery_candidates]

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        cache_dir: str | Path | None = None,
    ) -> "StaticHtmlAdapter":
        path = Path(config_path)
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Static adapter config is not valid JSON: {path}") from exc
        if not isinstance(config, dict):
            raise ValueError("Static adapter config must be a JSON object")
        return cls(config, cache_dir=cache_dir)

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
        validated = dict(config)
        index_urls = validated.get("index_url")
        if isinstance(index_urls, str):
            index_urls = [index_urls]
        if (
            not isinstance(index_urls, list)
            or not index_urls
            or not all(isinstance(item, str) and item for item in index_urls)
        ):
            raise ValueError("config.index_url must be a URL or a non-empty URL list")
        validated["index_url"] = index_urls
        for key in (
            "link_include_regex",
            "link_exclude_regex",
            "follow_link_regex",
        ):
            value = validated.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"config.{key} must be a string")
            if value:
                try:
                    re.compile(value)
                except re.error as exc:
                    raise ValueError(f"config.{key} is not a valid regex") from exc
        if "pdf" in validated and not isinstance(validated["pdf"], bool):
            raise ValueError("config.pdf must be true or false")
        validated.setdefault("pdf", False)
        return validated

    def detect_capabilities(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_name,
            "meeting_discovery": (
                "configured_index_one_level"
                if self.config.get("follow_link_regex")
                else "configured_index"
            ),
            "formats": ["pdf"] if self.config["pdf"] else ["html"],
            "speaker_segmentation": "heuristic_with_fallback",
            "pdf_text_extractor": shutil.which("pdftotext"),
        }

    def list_meetings(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be zero or greater")
        if limit == 0:
            return []
        include = (
            re.compile(self.config["link_include_regex"])
            if self.config.get("link_include_regex")
            else None
        )
        exclude = (
            re.compile(self.config["link_exclude_regex"])
            if self.config.get("link_exclude_regex")
            else None
        )
        follow = (
            re.compile(self.config["follow_link_regex"])
            if self.config.get("follow_link_regex")
            else None
        )
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        followed: set[str] = set()
        self._discovery_candidates = []

        def parse_links(fetched: FetchResult) -> list[tuple[str, str]]:
            parser = _DocumentParser()
            parser.feed(fetched.text())
            return parser.links

        def normalized_link(
            fetched: FetchResult, href: str
        ) -> tuple[str, Any] | None:
            source_url = urljoin(fetched.final_url, href)
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"}:
                return None
            source_url = parsed._replace(fragment="").geturl()
            return source_url, urlparse(source_url)

        def matches(pattern: re.Pattern[str], source_url: str, label: str) -> bool:
            return bool(
                pattern.search(source_url)
                or pattern.search(unquote(source_url))
                or pattern.search(label)
            )

        def is_excluded(source_url: str, label: str) -> bool:
            return bool(
                exclude
                and matches(exclude, source_url, label)
            )

        def discover_from(
            fetched: FetchResult, links: list[tuple[str, str]]
        ) -> bool:
            for href, label in links:
                normalized = normalized_link(fetched, href)
                if normalized is None:
                    continue
                source_url, parsed = normalized
                is_pdf = parsed.path.lower().endswith(".pdf")
                if include and not matches(include, source_url, label):
                    if is_pdf == self.config["pdf"]:
                        self._discovery_candidates.append(
                            {
                                "source_url": source_url,
                                "label": label,
                                "discovered_from": fetched.final_url,
                                "reason": "excluded_by_regex",
                                "rule": "link_include_regex",
                            }
                        )
                    continue
                if is_excluded(source_url, label):
                    self._discovery_candidates.append(
                        {
                            "source_url": source_url,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "excluded_by_regex",
                            "rule": "link_exclude_regex",
                        }
                    )
                    continue
                if is_pdf != self.config["pdf"]:
                    self._discovery_candidates.append(
                        {
                            "source_url": source_url,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "format_mismatch",
                            "expected_format": (
                                "pdf" if self.config["pdf"] else "html"
                            ),
                        }
                    )
                    continue
                if source_url in seen:
                    self._discovery_candidates.append(
                        {
                            "source_url": source_url,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "duplicate",
                        }
                    )
                    continue
                seen.add(source_url)
                meeting_id = _stable_id("meeting", source_url)
                decoded_filename = Path(unquote(parsed.path)).name
                generic_pdf_label = bool(
                    re.fullmatch(r"\s*[（(]?\s*PDF[^）)]*[）)]?\s*", label, re.I)
                )
                ref = {
                    "meeting_id": meeting_id,
                    "source_url": source_url,
                    "meeting_name": (
                        decoded_filename
                        if not label or generic_pdf_label
                        else label
                    )
                    or source_url,
                    "discovered_from": fetched.final_url,
                    "is_pdf": is_pdf,
                }
                self._meetings[meeting_id] = ref
                results.append(ref)
                self._discovery_candidates.append(
                    {
                        **ref,
                        "label": label,
                        "reason": "selected",
                    }
                )
                if limit is not None and len(results) >= limit:
                    return True
            return False

        for index_url in self.config["index_url"]:
            fetched = polite_fetch(index_url, cache_dir=self.cache_dir)
            links = parse_links(fetched)
            if discover_from(fetched, links):
                return results
            if follow is None:
                continue
            for href, label in links:
                normalized = normalized_link(fetched, href)
                if normalized is None:
                    continue
                follow_url, _ = normalized
                if not matches(follow, follow_url, label):
                    continue
                if is_excluded(follow_url, label) or follow_url in followed:
                    continue
                followed.add(follow_url)
                follow_result = polite_fetch(
                    follow_url, cache_dir=self.cache_dir
                )
                if discover_from(follow_result, parse_links(follow_result)):
                    return results
        return results

    def fetch_meeting(self, meeting_id: str | dict[str, Any]) -> dict[str, Any]:
        ref = self._resolve_ref(meeting_id)
        fetched = polite_fetch(ref["source_url"], cache_dir=self.cache_dir)
        is_pdf = bool(ref.get("is_pdf", self.config["pdf"]))
        if is_pdf:
            text, status, issues = self._extract_pdf(fetched)
            title = ref["meeting_name"]
            media_type = fetched.content_type or "application/pdf"
            transform = {
                "extractor": "pdftotext" if text else None,
                "segmentation": "speaker_marks_or_page_fallback",
            }
        else:
            parser = _DocumentParser()
            parser.feed(fetched.text())
            text = parser.visible_text()
            title = _collapse_inline(parser.title) or ref["meeting_name"]
            status = "extracted" if text else "html_no_text"
            issues = [] if text else ["HTMLから可視テキストを抽出できませんでした。"]
            media_type = fetched.content_type or "text/html"
            transform = {
                "extractor": "stdlib.html.parser.HTMLParser",
                "segmentation": "speaker_marks_or_paragraph_fallback",
            }

        speeches = segment_speeches(text) if text else []
        if text and not speeches:
            status = "text_without_segments"
            issues.append("テキストは取得できましたが、発言単位へ分割できませんでした。")
        date = _infer_date(title, text[:1000])
        council_name = str(
            self.config.get("council_name")
            or self.config.get("municipality")
            or urlparse(fetched.final_url).hostname
            or ""
        )
        meeting = {
            "meeting_id": ref["meeting_id"],
            "council_name": council_name,
            "meeting_name": title,
            "session": self.config.get("session"),
            "date": date,
            "source_url": ref["source_url"],
            "adapter": self.adapter_name,
            "fetched_at": fetched.fetched_at,
        }
        provenance = {
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": media_type,
            "content_sha256": fetched.sha256,
            "adapter": self.adapter_name,
            "transform": transform,
            "status": status,
            "cache_path": str(fetched.cache_path),
            "issues": issues,
        }
        return {"meeting": meeting, "speeches": speeches, "provenance": provenance}

    def _resolve_ref(self, meeting_id: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(meeting_id, dict):
            source_url = str(meeting_id.get("source_url") or "")
            if not source_url:
                raise ValueError("meeting reference has no source_url")
            ref = dict(meeting_id)
            ref.setdefault("meeting_id", _stable_id("meeting", source_url))
            ref.setdefault("meeting_name", source_url)
            ref.setdefault("is_pdf", self.config["pdf"])
            return ref
        if meeting_id in self._meetings:
            return self._meetings[meeting_id]
        parsed = urlparse(meeting_id)
        if parsed.scheme in {"http", "https"}:
            return {
                "meeting_id": _stable_id("meeting", meeting_id),
                "source_url": meeting_id,
                "meeting_name": Path(parsed.path).name or meeting_id,
                "discovered_from": None,
                "is_pdf": parsed.path.lower().endswith(".pdf"),
            }
        raise KeyError(
            f"Unknown meeting_id {meeting_id!r}; call list_meetings() first"
        )

    def _extract_pdf(
        self, fetched: FetchResult
    ) -> tuple[str, str, list[str]]:
        tool = shutil.which("pdftotext")
        if tool is None:
            return (
                "",
                "pdf_cached_pdftotext_unavailable",
                [
                    "PDFはキャッシュ済みですが、pdftotextがPATHにないため"
                    "本文抽出を省略しました。"
                ],
            )

        temporary_path: str | None = None
        input_path = Path(fetched.cache_path)
        if not input_path.exists():
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(fetched.body)
                temporary_path = handle.name
            input_path = Path(temporary_path)
        try:
            completed = subprocess.run(
                [tool, "-layout", str(input_path), "-"],
                check=False,
                capture_output=True,
                timeout=120,
            )
        finally:
            if temporary_path is not None:
                Path(temporary_path).unlink(missing_ok=True)

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            issue = "pdftotextによるPDF本文抽出に失敗しました。"
            if detail:
                issue = f"{issue} {detail[:300]}"
            return "", "pdf_text_extraction_failed", [issue]
        text = completed.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return "", "pdf_no_text", ["PDFから本文テキストを抽出できませんでした。"]
        return text, "extracted", []
