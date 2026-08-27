import tempfile
import unittest
from pathlib import Path

from tools.vault_indexer import (
    build_index,
    extract_wikilinks,
    find_backlinks,
    find_links,
    list_map,
    parse_frontmatter,
    search_notes,
)


class VaultIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(self.tmp_dir.name)
        self.db_path = self.vault_dir / ".vault_index.db"

        # Create sample markdown files
        (self.vault_dir / "01_政策").mkdir(parents=True)
        (self.vault_dir / "02_質問").mkdir(parents=True)

        note1 = """---
description: 空き家対策の推進と地域包括ケア
tags: [政策, 福祉, 空き家]
lifecycle: active
---

# 空き家対策基本方針

地域の[[空き家]]問題を解決するため、[[02_質問/R7_9月一般質問草案]]と連携して取り組む。
"""
        (self.vault_dir / "01_政策" / "空き家対策基本方針.md").write_text(note1, encoding="utf-8")

        note2 = """---
description: 9月定例会での一般質問原稿
tags:
  - 議会/一般質問
---

# R7_9月一般質問草案

[[空き家対策基本方針]]に基づき、町長へ空き家バンクの改修補助について問う。
"""
        (self.vault_dir / "02_質問" / "R7_9月一般質問草案.md").write_text(note2, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_parse_frontmatter(self) -> None:
        content = """---
description: テスト説明
tags: [tag1, tag2]
---
# 本文
"""
        meta, body = parse_frontmatter(content)
        self.assertEqual(meta["description"], "テスト説明")
        self.assertEqual(meta["tags"], ["tag1", "tag2"])
        self.assertIn("# 本文", body)

    def test_extract_wikilinks(self) -> None:
        content = "参照: [[ノートA]], [[ノートB|別名]], [[ノートC#見出し]]"
        links = extract_wikilinks(content)
        self.assertEqual(links, ["ノートA", "ノートB", "ノートC"])

    def test_build_and_search(self) -> None:
        count = build_index(self.vault_dir, self.db_path, verbose=False)
        self.assertEqual(count, 2)
        self.assertTrue(self.db_path.exists())

        # Search by keyword
        results = search_notes(self.db_path, "空き家バンク")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "R7_9月一般質問草案")

        # Search by tag / keyword
        results2 = search_notes(self.db_path, "福祉")
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0]["title"], "空き家対策基本方針")

    def test_links_and_backlinks(self) -> None:
        build_index(self.vault_dir, self.db_path, verbose=False)

        # Backlinks to 空き家対策基本方針
        bl = find_backlinks(self.db_path, "空き家対策基本方針")
        self.assertEqual(bl, ["02_質問/R7_9月一般質問草案.md"])

        # Links from 空き家対策基本方針
        links = find_links(self.db_path, "01_政策/空き家対策基本方針.md")
        self.assertIn("空き家", links)
        self.assertIn("02_質問/R7_9月一般質問草案", links)

    def test_list_map(self) -> None:
        build_index(self.vault_dir, self.db_path, verbose=False)
        rows = list_map(self.db_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "01_政策/空き家対策基本方針.md")
        self.assertIn("空き家対策の推進", rows[0][2])


if __name__ == "__main__":
    unittest.main()
