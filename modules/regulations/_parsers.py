"""Shared HTML link parsers for g-reiki / D1-Law reiki / joureikun vendors."""

from __future__ import annotations

import re
from html.parser import HTMLParser


def _collapse(value: str) -> str:
    return re.sub(r"[ \t\r\v　]+", " ", value).strip()


class FrameLinkParser(HTMLParser):
    """Collect anchor hrefs and frame srcs: ``links`` is (url, text, kind)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "base" and attributes.get("href") and self.base_href is None:
            self.base_href = attributes["href"]
        if tag == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_text = []
        src = attributes.get("src")
        if tag in {"frame", "iframe"} and src:
            self.links.append((src, "", tag))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(
                (self._href, _collapse("".join(self._link_text)), "a")
            )
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)


class LinkParser(HTMLParser):
    """Collect actual HTML links (no frames): ``links`` is (url, text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag == "base" and attributes.get("href") and self.base_href is None:
            self.base_href = attributes["href"]
        if tag == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, _collapse("".join(self._link_text))))
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)
