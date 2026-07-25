"""Tests for the bundled municipality observatory catalog."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bootstrap.observatory.catalog import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_SNAPSHOT_PATH,
    ObservatoryError,
    load_catalog,
    lookup,
)


class ObservatoryCatalogTests(unittest.TestCase):
    def test_bundled_snapshot_covers_registry_and_resolves_saga(self) -> None:
        catalog = load_catalog()

        self.assertEqual(1741, len(catalog["records"]))
        self.assertEqual(
            1741,
            catalog["manifest"]["snapshot"]["record_count"],
        )
        tara = lookup("41441", catalog=catalog)
        self.assertIsNotNone(tara)
        assert tara is not None
        self.assertEqual("佐賀県", tara["prefecture_name"])
        self.assertEqual("太良町", tara["municipality_name"])
        self.assertIn(
            tara["navigation_mode"],
            {"static", "javascript_candidate", "unknown"},
        )

    def test_snapshot_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            snapshot_path = root / "municipalities.jsonl"
            manifest = json.loads(
                DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            snapshot_path.write_bytes(
                DEFAULT_SNAPSHOT_PATH.read_bytes() + b"{}\n"
            )

            with self.assertRaisesRegex(
                ObservatoryError,
                "SHA-256 mismatch",
            ):
                load_catalog(
                    manifest_path=manifest_path,
                    snapshot_path=snapshot_path,
                )


if __name__ == "__main__":
    unittest.main()
