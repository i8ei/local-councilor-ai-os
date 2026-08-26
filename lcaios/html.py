"""Shared anchor/context extraction from untrusted HTML.

One stdlib ``html.parser`` implementation so bootstrap, modules, and
source_profiles do not drift apart. Scripts are never executed;
``script_count`` only records how many were seen.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_WS_RE = re.compile(r"\s+")


def _collapse(value: str) -> str:
    return _WS_RE.sub(" ", value.replace("\u3000", " ")).strip()


class LinkParser(HTMLParser):
    """Collect ``(href, collapsed label)`` pairs from anchor tags.

    Also records every whitespace-collapsed text node in ``visible_text``,
    the number of script tags in ``script_count``, and — with
    ``capture_context=True`` — the page's title/H1 text via :meth:`context`.
    """

    def __init__(self, *, capture_context: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.visible_text: list[str] = []
        self.script_count = 0
        self._capture_context = capture_context
        self._context: list[str] = []
        self._in_context_tag = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag_name = tag.lower()
        if tag_name == "script":
            self.script_count += 1
        elif tag_name in {"title", "h1"}:
            self._in_context_tag = True
        elif tag_name == "a" and self._href is None:
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._anchor = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_context_tag:
            self._context.append(text)
        self.visible_text.append(text)
        if self._href is not None:
            self._anchor.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"title", "h1"}:
            self._in_context_tag = False
        if tag_name == "a" and self._href is not None:
            self.links.append((self._href, _collapse(" ".join(self._anchor))))
            self._href = None
            self._anchor = []

    def context(self) -> str:
        """Whitespace-collapsed title + H1 evidence for the page."""
        return _collapse(" ".join(self._context))
