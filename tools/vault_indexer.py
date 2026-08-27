#!/usr/bin/env python3
"""tools/vault_indexer.py — Reference local memory indexer for Markdown vaults.

A standalone, zero-dependency SQLite FTS5 indexer for Obsidian / Markdown vaults.
Inspired by personal councilor memory systems (kura).

Commands:
  build                 Index all Markdown notes into a local SQLite FTS5 database
  search <query>        Search notes by keyword with FTS5 ranking
  links <note>          List outgoing [[wikilinks]] from a note
  backlinks <note>      List incoming links pointing to a note
  map                   List all notes with descriptions and tags
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any

# Excluded directories from indexing
DEFAULT_EXCLUDE_DIRS = {".git", ".obsidian", ".trash", "Templates", "templates"}

# Regular expression to extract wikilinks: [[target]] or [[target|alias]] or [[target#heading]]
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+?)(?:[#|][^\]]*)?\]\]")

# Frontmatter extraction pattern
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def normalize_text(text: str) -> str:
    """Normalize macOS NFD text to standard Unicode NFC."""
    return unicodedata.normalize("NFC", text)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML-like frontmatter key-values and remaining body."""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end() :]
    metadata: dict[str, Any] = {}

    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            # Simple list or string handling
            if val.startswith("[") and val.endswith("]"):
                val_list = [item.strip().strip("\"'") for item in val[1:-1].split(",") if item.strip()]
                metadata[key] = val_list
            elif val.startswith("- "):
                metadata.setdefault(key, []).append(val[2:].strip())
            else:
                metadata[key] = val.strip("\"'")
        elif line.startswith("- ") and "tags" in metadata and isinstance(metadata["tags"], list):
            metadata["tags"].append(line[2:].strip())

    return metadata, body


def extract_wikilinks(content: str) -> list[str]:
    """Extract all outgoing [[wikilinks]] from markdown content."""
    normalized = normalize_text(content)
    raw_links = WIKILINK_PATTERN.findall(normalized)
    seen: set[str] = set()
    unique_links: list[str] = []
    for link in raw_links:
        clean = link.strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique_links.append(clean)
    return unique_links


def get_fts5_tokenizer(conn: sqlite3.Connection) -> str:
    """Detect available FTS5 tokenizer (trigram preferred for CJK, fallback to unicode61)."""
    for tok in ["trigram", "unicode61"]:
        try:
            conn.execute(f"CREATE VIRTUAL TABLE _test_fts USING fts5(content, tokenize='{tok}')")
            conn.execute("DROP TABLE _test_fts")
            return tok
        except sqlite3.OperationalError:
            continue
    return "unicode61"


def init_db(conn: sqlite3.Connection) -> None:
    """Create schema for vault notes, links, and FTS5 search index."""
    tokenizer = get_fts5_tokenizer(conn)
    with conn:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rel_path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                lifecycle TEXT DEFAULT 'active',
                last_updated TEXT DEFAULT '',
                body TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS links (
                source_path TEXT NOT NULL,
                target_title TEXT NOT NULL,
                PRIMARY KEY (source_path, target_title)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                title,
                description,
                tags,
                body,
                content='notes',
                content_rowid='note_id',
                tokenize='{tokenizer}'
            );

            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, title, description, tags, body)
                VALUES (new.note_id, new.title, new.description, new.tags, new.body);
            END;

            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, title, description, tags, body)
                VALUES('delete', old.note_id, old.title, old.description, old.tags, old.body);
            END;

            CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, title, description, tags, body)
                VALUES('delete', old.note_id, old.title, old.description, old.tags, old.body);
                INSERT INTO notes_fts(rowid, title, description, tags, body)
                VALUES (new.note_id, new.title, new.description, new.tags, new.body);
            END;
        """)


def build_index(vault_dir: Path, db_path: Path, verbose: bool = True) -> int:
    """Scan all markdown files in vault_dir and build the SQLite FTS5 index."""
    vault_dir = vault_dir.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Recreate DB cleanly
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    init_db(conn)

    notes_indexed = 0
    links_indexed = 0

    with conn:
        for md_file in sorted(vault_dir.rglob("*.md")):
            # Check exclusions
            rel_parts = md_file.relative_to(vault_dir).parts
            if any(part in DEFAULT_EXCLUDE_DIRS or part.startswith(".") for part in rel_parts):
                continue

            rel_path = normalize_text(str(md_file.relative_to(vault_dir)))
            title = normalize_text(md_file.stem)

            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            metadata, body = parse_frontmatter(content)
            description = normalize_text(str(metadata.get("description", "")))
            lifecycle = str(metadata.get("lifecycle", "active"))
            last_updated = str(metadata.get("last_updated", metadata.get("date", "")))

            raw_tags = metadata.get("tags", [])
            if isinstance(raw_tags, list):
                tags = " ".join(normalize_text(str(t)) for t in raw_tags)
            else:
                tags = normalize_text(str(raw_tags))

            conn.execute(
                """
                INSERT INTO notes (rel_path, title, description, tags, lifecycle, last_updated, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rel_path, title, description, tags, lifecycle, last_updated, normalize_text(body)),
            )
            notes_indexed += 1

            # Extract and store wikilinks
            for target in extract_wikilinks(content):
                conn.execute(
                    "INSERT OR IGNORE INTO links (source_path, target_title) VALUES (?, ?)",
                    (rel_path, target),
                )
                links_indexed += 1

    conn.close()
    if verbose:
        print(f"✅ Indexed {notes_indexed} notes and {links_indexed} links into {db_path}")
    return notes_indexed


def search_notes(db_path: Path, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search notes using FTS5 match query with LIKE fallback for short terms."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    c = conn.cursor()

    normalized_query = normalize_text(query)
    clean_terms = [t for t in normalized_query.split() if t]
    if not clean_terms:
        conn.close()
        return []

    results: list[dict[str, Any]] = []

    # If all terms are >= 3 characters (or non-trigram), use FTS5 MATCH
    has_short_term = any(len(t) < 3 for t in clean_terms)

    if not has_short_term:
        fts_match = " AND ".join(f'"{t}"' for t in clean_terms)
        sql = """
            SELECT n.rel_path, n.title, n.description, n.tags, n.lifecycle,
                   snippet(notes_fts, 3, '==', '==', '...', 24) as snip
            FROM notes_fts f
            JOIN notes n ON f.rowid = n.note_id
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            c.execute(sql, (fts_match, limit))
            for row in c.fetchall():
                results.append({
                    "rel_path": row[0],
                    "title": row[1],
                    "description": row[2],
                    "tags": row[3],
                    "lifecycle": row[4],
                    "snippet": row[5],
                })
        except sqlite3.OperationalError:
            pass

    # If no FTS results (or short terms like 2-char CJK words), fallback to LIKE
    if not results:
        like_conditions = " AND ".join("(title LIKE ? OR description LIKE ? OR tags LIKE ? OR body LIKE ?)" for _ in clean_terms)
        params: list[str] = []
        for t in clean_terms:
            like_pat = f"%{t}%"
            params.extend([like_pat, like_pat, like_pat, like_pat])
        params.append(str(limit))

        sql_like = f"""
            SELECT rel_path, title, description, tags, lifecycle, substr(body, 1, 100)
            FROM notes
            WHERE {like_conditions}
            LIMIT ?
        """
        try:
            c.execute(sql_like, params)
            for row in c.fetchall():
                results.append({
                    "rel_path": row[0],
                    "title": row[1],
                    "description": row[2],
                    "tags": row[3],
                    "lifecycle": row[4],
                    "snippet": row[5].replace('\n', ' '),
                })
        except sqlite3.OperationalError:
            pass

    conn.close()
    return results


def find_backlinks(db_path: Path, note_title: str) -> list[str]:
    """Find all notes that contain a wikilink pointing to note_title."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    c = conn.cursor()
    norm_title = normalize_text(note_title)
    c.execute(
        "SELECT source_path FROM links WHERE target_title = ? OR target_title = ? ORDER BY source_path",
        (norm_title, Path(norm_title).stem),
    )
    backlinks = [r[0] for r in c.fetchall()]
    conn.close()
    return backlinks


def find_links(db_path: Path, note_path: str) -> list[str]:
    """Find all outgoing wikilinks from a note."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    c = conn.cursor()
    norm_path = normalize_text(note_path)
    c.execute(
        "SELECT target_title FROM links WHERE source_path = ? OR source_path LIKE ? ORDER BY target_title",
        (norm_path, f"%{norm_path}%"),
    )
    links = [r[0] for r in c.fetchall()]
    conn.close()
    return links


def list_map(db_path: Path) -> list[tuple[str, str, str]]:
    """List all notes with their relative path, lifecycle, and description."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    c = conn.cursor()
    c.execute("SELECT rel_path, lifecycle, description FROM notes ORDER BY rel_path")
    rows = c.fetchall()
    conn.close()
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local memory indexer and search for Markdown vaults (inspired by kura)."
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path.cwd(),
        help="Path to the Markdown vault directory (default: current working directory)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite index database (default: <vault>/.vault_index.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # build
    subparsers.add_parser("build", help="Build or rebuild the FTS5 vault index")

    # search
    search_p = subparsers.add_parser("search", help="Search vault notes with FTS5 ranking")
    search_p.add_argument("query", help="Keywords to search for")
    search_p.add_argument("-k", "--limit", type=int, default=10, help="Max results (default: 10)")

    # backlinks
    bl_p = subparsers.add_parser("backlinks", help="Find notes that link to a specific note")
    bl_p.add_argument("title", help="Title or note name to look for")

    # links
    l_p = subparsers.add_parser("links", help="Find outgoing links from a note")
    l_p.add_argument("path", help="Relative path or name of the source note")

    # map
    subparsers.add_parser("map", help="List all indexed notes and descriptions")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault_dir = args.vault.resolve()
    db_path = args.db.resolve() if args.db else vault_dir / ".vault_index.db"

    if args.command == "build":
        build_index(vault_dir, db_path)
        return 0

    if not db_path.exists():
        print(f"Error: Index database not found at {db_path}. Please run 'build' first.", file=sys.stderr)
        return 1

    if args.command == "search":
        results = search_notes(db_path, args.query, limit=args.limit)
        if not results:
            print(f"No notes found matching: {args.query}")
            return 0
        print(f"Found {len(results)} match(es) for: '{args.query}'\n")
        for idx, r in enumerate(results, 1):
            desc = f" — {r['description']}" if r["description"] else ""
            print(f"{idx}. [{r['title']}]({r['rel_path']}){desc}")
            if r["snippet"]:
                print(f"   Excerpt: {r['snippet']}")
        return 0

    if args.command == "backlinks":
        bl = find_backlinks(db_path, args.title)
        print(f"Backlinks pointing to [[{args.title}]]: {len(bl)} note(s)")
        for p in bl:
            print(f"- {p}")
        return 0

    if args.command == "links":
        links = find_links(db_path, args.path)
        print(f"Outgoing links from {args.path}: {len(links)} link(s)")
        for target_link in links:
            print(f"- [[{target_link}]]")
        return 0

    if args.command == "map":
        rows = list_map(db_path)
        for rel_path, lifecycle, desc in rows:
            print(f"{rel_path}\t[{lifecycle}]\t{desc}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
