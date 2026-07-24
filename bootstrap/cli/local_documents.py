"""Diagnose municipal budget and settlement document indexes without downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from .http import FetchError, HttpClient


KEYWORDS = {
    "budget": ("予算書", "当初予算", "補正予算", "予算概要", "予算説明"),
    "settlement": (
        "決算書",
        "決算概要",
        "決算状況",
        "決算説明",
        "決算カード",
        "主要な施策",
        "主要施策",
    ),
}
PAGE_KEYWORDS = {
    "budget": ("予算",),
    "settlement": ("決算",),
}
DOCUMENT_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".csv", ".zip")
OPTIONS = {
    "later": "候補だけ記録し、文書取得とSQLite化は後日に回す",
    "sample": "候補から1〜3件を選び、PDF品質と抽出・検算経路だけ試す",
    "targeted": "年度・会計・資料種別を限定して取り込む",
    "full": "必要範囲を確認後に全件取り込む",
}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._title_text: list[str] = []
        self._h1_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag_name = tag.lower()
        if tag_name == "title":
            self._in_title = True
        elif tag_name == "h1":
            self._in_h1 = True
        if tag_name != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_text.append(data)
        if self._in_h1:
            self._h1_text.append(data)
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "a" and self._href is not None:
            self.links.append(
                {
                    "href": self._href,
                    "text": re.sub(r"\s+", " ", "".join(self._text)).strip(),
                }
            )
            self._href = None
            self._text = []
        if tag_name == "title":
            self._in_title = False
        elif tag_name == "h1":
            self._in_h1 = False

    def page_context(self) -> str:
        """Return normalized title and primary heading text."""

        return re.sub(
            r"\s+",
            " ",
            " ".join((*self._title_text, *self._h1_text)),
        ).strip()


def diagnose_index(
    *,
    index_url: str,
    final_url: str,
    html: str,
    fetched_at: str,
    content_hash: str,
) -> dict[str, Any]:
    """Return candidate documents and confirmation options from one HTML page."""

    parser = _LinkParser()
    parser.feed(html)
    page_evidence = parser.page_context()
    page_kinds = {
        kind
        for kind, keywords in PAGE_KEYWORDS.items()
        if any(keyword in page_evidence for keyword in keywords)
    }
    candidates: list[dict[str, Any]] = []
    candidate_by_url: dict[str, dict[str, Any]] = {}
    for link in parser.links:
        resolved_url = urllib.parse.urljoin(final_url, link["href"])
        parts = urllib.parse.urlsplit(resolved_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue
        evidence = f"{link['text']} {urllib.parse.unquote(parts.path)}"
        direct_kinds = {
            kind
            for kind, keywords in KEYWORDS.items()
            if any(keyword in evidence for keyword in keywords)
        }
        kinds = [
            kind
            for kind in KEYWORDS
            if kind in direct_kinds or kind in page_kinds
        ]
        extension = Path(parts.path).suffix.lower()
        if not kinds or extension not in DOCUMENT_EXTENSIONS:
            continue
        canonical = urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc, parts.path, parts.query, "")
        )
        existing = candidate_by_url.get(canonical)
        if existing:
            fallback_title = Path(parts.path).name
            if link["text"] and existing["title"] == fallback_title:
                existing["title"] = link["text"]
            existing["kinds"] = [
                kind
                for kind in KEYWORDS
                if kind in set(existing["kinds"]) | set(kinds)
            ]
            if direct_kinds:
                existing["reason"] = (
                    "official_index_keyword_and_document_extension"
                )
            continue
        candidate = {
            "title": link["text"] or Path(parts.path).name,
            "url": canonical,
            "kinds": kinds,
            "format": extension.removeprefix("."),
            "reason": (
                "official_index_keyword_and_document_extension"
                if direct_kinds
                else "official_index_context_and_document_extension"
            ),
        }
        candidate_by_url[canonical] = candidate
        candidates.append(candidate)

    return {
        "schema_version": 1,
        "status": "needs_confirmation",
        "index": {
            "requested_url": index_url,
            "final_url": final_url,
            "fetched_at": fetched_at,
            "sha256": content_hash,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "documents_downloaded": 0,
        "database_created": False,
        "next_options": [
            {"id": option_id, "description": description}
            for option_id, description in OPTIONS.items()
        ],
        "advice": [
            "候補の年度、会計、資料種別、PDF品質を確認してから取得範囲を決める",
            "反復検索・年度比較・合計検算をする資料は原本保存後にSQLite化する",
            "一度だけ読む資料は都度参照でもよいが、対外利用した原典はsnapshotを残す",
        ],
    }


def _sample_filename(candidate: dict[str, Any], position: int) -> str:
    path = urllib.parse.unquote(
        urllib.parse.urlsplit(str(candidate["url"])).path
    )
    filename = Path(path).name
    filename = re.sub(r"[^0-9A-Za-z._-]+", "_", filename).strip("._")
    extension = f".{candidate['format']}" if candidate.get("format") else ""
    return filename or f"document-{position}{extension}"


def _pdf_text_quality(path: Path) -> dict[str, Any]:
    executable = shutil.which("pdftotext")
    if executable is None:
        return {
            "status": "unavailable",
            "tool": "pdftotext",
            "detail": "PATH上にpdftotextがないためテキスト層は未確認",
        }
    result = subprocess.run(
        [executable, "-f", "1", "-l", "3", str(path), "-"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return {
            "status": "failed",
            "tool": "pdftotext",
            "detail": result.stderr.strip()[:500],
        }
    characters = len(result.stdout.strip())
    return {
        "status": "extracted" if characters else "empty",
        "tool": "pdftotext",
        "pages_checked": "1-3",
        "character_count": characters,
    }


def _available_destination(directory: Path, filename: str) -> Path:
    """Choose a new path without overwriting an earlier sample."""

    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def sample_documents(
    *,
    diagnosis: dict[str, Any],
    client: HttpClient,
    output_directory: Path,
    candidate_numbers: list[int],
) -> dict[str, Any]:
    """Download selected official candidates and report extraction quality."""

    candidates = diagnosis["candidates"]
    if not candidates:
        raise ValueError("取得候補がありません")
    if not candidate_numbers:
        raise ValueError("候補を1件以上指定してください")
    if len(candidate_numbers) > 3:
        raise ValueError("sampleで取得できる候補は最大3件です")
    if len(set(candidate_numbers)) != len(candidate_numbers):
        raise ValueError("同じ候補番号を重複して指定できません")
    if any(number < 1 or number > len(candidates) for number in candidate_numbers):
        raise ValueError(
            f"候補番号は1から{len(candidates)}の範囲で指定してください"
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, Any]] = []
    for number in candidate_numbers:
        candidate = candidates[number - 1]
        fetched = client.fetch(
            str(candidate["url"]),
            cache_key=f"local-document:{candidate['url']}",
        )
        filename = _sample_filename(candidate, number)
        destination = _available_destination(output_directory, filename)
        destination.write_bytes(fetched.body)
        expected_format = str(candidate.get("format") or "")
        is_pdf = expected_format == "pdf"
        pdf_signature = fetched.body.startswith(b"%PDF-") if is_pdf else None
        document = {
            "candidate_number": number,
            "title": candidate["title"],
            "kinds": candidate["kinds"],
            "source_url": fetched.final_url,
            "path": str(destination.resolve(strict=False)),
            "format": expected_format,
            "content_type": fetched.content_type,
            "size_bytes": len(fetched.body),
            "sha256": hashlib.sha256(fetched.body).hexdigest(),
            "fetched_at": fetched.fetched_at,
            "from_cache": fetched.from_cache,
            "format_check": {
                "status": (
                    "passed"
                    if not is_pdf or pdf_signature
                    else "failed"
                ),
                "pdf_signature": pdf_signature,
            },
        }
        if is_pdf:
            document["text_layer_check"] = _pdf_text_quality(destination)
        documents.append(document)

    quality_passed = all(
        document["format_check"]["status"] == "passed"
        for document in documents
    )
    return {
        **diagnosis,
        "status": "sampled" if quality_passed else "quality_failed",
        "documents_downloaded": len(documents),
        "sample_directory": str(output_directory.resolve(strict=False)),
        "samples": documents,
        "database_created": False,
        "retrieval": client.retrieval_report(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m bootstrap.cli.local_documents",
        description="自治体公式索引だけを取得し、予算・決算候補と取込選択肢を表示",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    diagnose = subparsers.add_parser(
        "diagnose",
        help="索引HTMLだけを読み、文書は取得せず確認待ちで止める",
    )
    diagnose.add_argument("--index-url", required=True)
    diagnose.add_argument("--cache-dir", default="bootstrap/.cache/local-documents")
    diagnose.add_argument("--offline", action="store_true")
    sample = subparsers.add_parser(
        "sample",
        help="公式索引の候補を最大3件取得し、原本hashとPDF品質を確認する",
    )
    sample.add_argument("--index-url", required=True)
    sample.add_argument("--output-dir", required=True, type=Path)
    sample.add_argument(
        "--candidate",
        action="append",
        type=int,
        help="1始まりの候補番号。最大3回指定可",
    )
    sample.add_argument(
        "--limit",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="--candidate省略時に先頭から取得する件数",
    )
    sample.add_argument("--cache-dir", default="bootstrap/.cache/local-documents")
    sample.add_argument("--offline", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = HttpClient(args.cache_dir, offline=args.offline)
        fetched = client.fetch(args.index_url)
        if "html" not in fetched.content_type:
            raise FetchError(
                f"索引がHTMLではありません: {fetched.content_type}"
            )
        result = diagnose_index(
            index_url=args.index_url,
            final_url=fetched.final_url,
            html=fetched.text(),
            fetched_at=fetched.fetched_at,
            content_hash=f"sha256:{fetched.sha256}",
        )
        if args.command == "sample":
            numbers = args.candidate or list(
                range(1, min(args.limit, result["candidate_count"]) + 1)
            )
            result = sample_documents(
                diagnosis=result,
                client=client,
                output_directory=args.output_dir,
                candidate_numbers=numbers,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["status"] == "quality_failed" else 0
    except (FetchError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
