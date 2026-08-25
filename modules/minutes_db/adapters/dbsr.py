"""Adapter for dbsr.jp minutes (Kanzaki / Kamimine / Miyaki).

Fetches only the observed ``index.php`` entry point via the shared
``HttpClient`` (robots.txt, throttling, cache, ``fetched_at``).  No tenant
names, IDs, or URLs are guessed — links are discovered from the index
document itself, and off-host drift is treated as a safe stop.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .base import (  # type: ignore[import-untyped]
    Adapter,
    CacheTier,
    FetchResult,
)

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
_PERSON_RE = re.compile(r"^(?P<name>.{1,40}?君)(?:[ \t\u3000]+(?P<text>.*)|$)")
_GENERIC_SPEAKER_RE = re.compile(
    r"^(?P<name>[^ \t\u3000]{1,30})[ \t\u3000]+(?P<text>.*)$"
)
_MINUTES_HINT_RE = re.compile(r"(会議録|議事録|定例会|臨時会|本会議)")
_EXCLUDE_HINT_RE = re.compile(
    r"(summary|agenda|schedule|概要|要約|次第|議案|資料|日程|予定)", re.I
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
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

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._title_depth:
            self.title += data
        if self._current_href is not None:
            self._current_link_text.append(data)


def _collapse_inline(value: str) -> str:
    return re.sub(r"[ \t\r\v]+", " ", value).strip()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _strip_speaker_suffix(name: str) -> str:
    return re.sub(r"(?:議員)?君$", "", name.strip(" \t\u3000"))


def _parse_speaker(value: str) -> tuple[str, str | None, str]:
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
    """Segment text by Japanese speaker marks, with fallback."""
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
    western = re.search(
        r"(?<!\d)(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?", combined
    )
    if western:
        try:
            year, month, day = (int(part) for part in western.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
        except (ValueError, TypeError):
            return None
    era = re.search(
        r"(令和|平成|昭和)(元|\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        combined,
    )
    if not era:
        return None
    bases = {"令和": 2018, "平成": 1988, "昭和": 1925}
    try:
        era_year = 1 if era.group(2) == "元" else int(era.group(2))
        year = bases[era.group(1)] + era_year
        return f"{year:04d}-{int(era.group(3)):02d}-{int(era.group(4)):02d}"
    except (ValueError, TypeError, KeyError):
        return None


def _validate_index_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"dbsr index_url must be http(s): {url}")
    host = (parsed.hostname or "").lower()
    if not (host == "dbsr.jp" or host.endswith(".dbsr.jp")):
        raise ValueError(f"dbsr index_url must be on *.dbsr.jp: {url}")
    if "/index.php" not in parsed.path:
        raise ValueError(f"dbsr index_url must contain /index.php: {url}")
    return url


def _visible_text_from_fetch(fetched: FetchResult) -> tuple[str, str]:
    """Extract visible text and title from an HTML fetch."""
    text = fetched.text()
    parser = _DocumentParser()
    # feed text for links/title; also build visible text via simple strip
    parser.feed(text)
    title = _collapse_inline(parser.title)

    # Build visible text with block boundaries similar to static_html
    # Re-parse with block-aware parser
    class _TextParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._ignored_depth = 0
            self._parts: list[str] = []
            self.title = ""
            self._title_depth = 0

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
                self._parts.append("\n")
            if tag == "title":
                self._title_depth += 1

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
            if tag in _BLOCK_TAGS:
                self._parts.append("\n")

        def handle_data(self, data: str) -> None:
            if self._ignored_depth:
                return
            self._parts.append(data)
            if self._title_depth:
                self.title += data

        def visible(self) -> str:
            lines = [
                _collapse_inline(line) for line in "".join(self._parts).splitlines()
            ]
            return "\n".join(line for line in lines if line)

    tp = _TextParser()
    tp.feed(text)
    visible = tp.visible()
    title = _collapse_inline(tp.title) or title
    return visible, title


class DbsrAdapter(Adapter):
    """Discover and normalize dbsr.jp minute documents."""

    adapter_name = "dbsr"

    def __init__(
        self,
        index_url: str | list[str],
        *,
        client: Any,
        council_name: str | None = None,
    ) -> None:
        if isinstance(index_url, str):
            urls = [index_url]
        else:
            urls = list(index_url)
        if not urls:
            raise ValueError("index_url must not be empty")
        self.index_urls: list[str] = [_validate_index_url(u) for u in urls]
        self.client = client
        self._explicit_council_name = council_name
        self._meetings: dict[str, dict[str, Any]] = {}
        self._discovery_candidates: list[dict[str, Any]] = []
        # Fallback council label derived from the observed index document title,
        # populated during list_meetings. No per-municipality data is baked in.
        self._index_title: str | None = None

    @property
    def discovery_candidates(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._discovery_candidates]

    @property
    def coverage_candidate_sessions(self) -> list[dict[str, Any]] | None:
        return None

    def detect_capabilities(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_name,
            "meeting_discovery": "observed_index_only",
            "formats": ["html", "pdf"],
            "speaker_segmentation": "heuristic_with_fallback",
            "robots": "enforced_via_HttpClient",
        }

    def list_meetings(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be zero or greater")
        if limit == 0:
            return []
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        self._discovery_candidates = []
        self._meetings = {}

        for index_url in self.index_urls:
            fetched = self.client.fetch(index_url, tier=CacheTier.INDEX)
            # Safe stop on host drift via redirect
            fetched_host = (urlparse(fetched.final_url).hostname or "").lower()
            index_host = (urlparse(index_url).hostname or "").lower()
            if fetched_host != index_host:
                raise ValueError(
                    f"dbsr host drift detected: {index_url} -> {fetched.final_url}"
                )
            parser = _DocumentParser()
            parser.feed(fetched.text())
            links = parser.links
            if self._index_title is None:
                self._index_title = _collapse_inline(parser.title) or None

            for href, label in links:
                resolved = urljoin(fetched.final_url, href)
                parsed = urlparse(resolved)
                if parsed.scheme not in {"http", "https"}:
                    continue
                # strip fragment
                resolved = parsed._replace(fragment="").geturl()
                decoded_resolved = unquote(resolved)
                target_host = (parsed.hostname or "").lower()

                # Host drift: only same host is allowed
                if target_host != fetched_host:
                    self._discovery_candidates.append(
                        {
                            "source_url": resolved,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "host_drift",
                        }
                    )
                    continue

                is_pdf = parsed.path.lower().endswith(".pdf")

                # Exclude hints
                if _EXCLUDE_HINT_RE.search(label) or _EXCLUDE_HINT_RE.search(
                    decoded_resolved
                ):
                    self._discovery_candidates.append(
                        {
                            "source_url": resolved,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "excluded_by_regex",
                            "rule": "exclude_hint",
                        }
                    )
                    continue

                # Must look like a minutes document
                hint_text = f"{label} {decoded_resolved}"
                if not _MINUTES_HINT_RE.search(hint_text):
                    # Allow numeric index.php detail pages as minutes even without label hint
                    # e.g. /index.php/1234 where label may be generic
                    if not re.search(r"/index\.php/\d+", parsed.path):
                        self._discovery_candidates.append(
                            {
                                "source_url": resolved,
                                "label": label,
                                "discovered_from": fetched.final_url,
                                "reason": "excluded_by_regex",
                                "rule": "minutes_hint",
                            }
                        )
                        continue

                if resolved in seen:
                    self._discovery_candidates.append(
                        {
                            "source_url": resolved,
                            "label": label,
                            "discovered_from": fetched.final_url,
                            "reason": "duplicate",
                        }
                    )
                    continue

                seen.add(resolved)
                meeting_id = _stable_id("meeting", resolved)
                decoded_filename = Path(unquote(parsed.path)).name or resolved
                meeting_name = label or decoded_filename
                # Generic PDF size label fallback
                if re.fullmatch(r"\s*[（(]?\s*PDF[^）)]*[）)]?\s*", label, re.I):
                    meeting_name = decoded_filename

                ref: dict[str, Any] = {
                    "meeting_id": meeting_id,
                    "source_url": resolved,
                    "meeting_name": meeting_name,
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
                    return results

        return results

    def fetch_meeting(self, meeting_id: str | dict[str, Any]) -> dict[str, Any]:
        ref = self._resolve_ref(meeting_id)
        fetched = self.client.fetch(ref["source_url"], tier=CacheTier.DOCUMENT)
        # Host drift check on fetch as well
        fetched_host = (urlparse(fetched.final_url).hostname or "").lower()
        ref_host = (urlparse(ref["source_url"]).hostname or "").lower()
        if fetched_host != ref_host:
            raise ValueError(
                f"dbsr host drift on fetch: {ref['source_url']} -> {fetched.final_url}"
            )
        is_pdf = bool(ref.get("is_pdf", False))
        # Also detect pdf via content-type if not already flagged
        if not is_pdf and fetched.content_type == "application/pdf":
            is_pdf = True

        if is_pdf:
            # Cache PDF but mark unavailable when pdftotext absent (like static adapter)
            # Keep simple: no text extraction, status reflects caching
            text = ""
            status = "pdf_cached_pdftotext_unavailable"
            issues_pdf = ["PDFはキャッシュ済みですが、本文抽出は未対応です。"]
            title = ref.get("meeting_name", "")
            media_type = fetched.content_type or "application/pdf"
            transform = {
                "extractor": None,
                "segmentation": "none",
            }
            speeches = []
            issues = issues_pdf
        else:
            visible, title = _visible_text_from_fetch(fetched)
            text = visible
            title = title or ref.get("meeting_name", "")
            if text:
                status = "extracted"
                issues = []
            else:
                status = "html_no_text"
                issues = ["HTMLから可視テキストを抽出できませんでした。"]
            media_type = fetched.content_type or "text/html"
            transform = {
                "extractor": "stdlib.html.parser.HTMLParser",
                "segmentation": "speaker_marks_or_paragraph_fallback",
            }
            speeches = segment_speeches(text) if text else []
            if text and not speeches:
                status = "text_without_segments"
                issues.append(
                    "テキストは取得できましたが、発言単位へ分割できませんでした。"
                )

        date = _infer_date(title, text[:1000] if text else "")
        council_name = self._explicit_council_name
        if not council_name:
            # Prefer the observed index document title; fall back to the host.
            # No per-municipality names are baked into the adapter.
            discovered = str(ref.get("discovered_from") or self.index_urls[0])
            host = (urlparse(discovered).hostname or "").lower()
            council_name = self._index_title or host

        meeting: dict[str, Any] = {
            "meeting_id": ref["meeting_id"],
            "council_name": council_name,
            "meeting_name": title,
            "session": None,
            "date": date,
            "source_url": ref["source_url"],
            "adapter": self.adapter_name,
            "fetched_at": fetched.fetched_at,
        }
        provenance: dict[str, Any] = {
            "adapter": self.adapter_name,
            "index_url": self.index_urls[0]
            if len(self.index_urls) == 1
            else self.index_urls,
            "discovered_from": ref.get("discovered_from"),
            "resolved_url": fetched.final_url,
            "resolved_url_at_fetch": fetched.final_url,
            "fetched_at": fetched.fetched_at,
            "media_type": media_type,
            "content_type": media_type,
            "content_sha256": fetched.sha256,
            "content_hash": f"sha256:{fetched.sha256}",
            "cache_path": str(fetched.cache_path),
            "from_cache": fetched.from_cache,
            "status": status,
            "transform": transform,
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
            ref.setdefault("is_pdf", source_url.lower().endswith(".pdf"))
            return ref
        if meeting_id in self._meetings:
            return self._meetings[meeting_id]
        parsed = urlparse(meeting_id)
        if parsed.scheme in {"http", "https"}:
            host = (parsed.hostname or "").lower()
            if host == "dbsr.jp" or host.endswith(".dbsr.jp"):
                if "/index.php" not in parsed.path and not parsed.path.lower().endswith(
                    ".pdf"
                ):
                    raise KeyError(
                        f"Unknown meeting_id {meeting_id!r}; call list_meetings() first"
                    )
                return {
                    "meeting_id": _stable_id("meeting", meeting_id),
                    "source_url": meeting_id,
                    "meeting_name": Path(parsed.path).name or meeting_id,
                    "discovered_from": None,
                    "is_pdf": parsed.path.lower().endswith(".pdf"),
                }
        raise KeyError(f"Unknown meeting_id {meeting_id!r}; call list_meetings() first")


__all__ = ["DbsrAdapter", "segment_speeches"]
