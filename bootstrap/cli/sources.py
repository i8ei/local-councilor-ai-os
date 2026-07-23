"""Inspect the machine-readable official source registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


DEFAULT_REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "data-contracts"
    / "source_registry.v1.json"
)


class RegistryError(RuntimeError):
    """Raised when the source registry cannot be used safely."""


def load_registry(path: str | Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    """Load and minimally validate a JSON source registry."""

    registry_path = Path(path)
    try:
        value = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"source registryを読み取れません: {error}") from error
    if not isinstance(value.get("sources"), dict):
        raise RegistryError("source registryにsources objectがありません")
    for source_id, source in value["sources"].items():
        if not isinstance(source, dict):
            raise RegistryError(f"source定義がobjectではありません: {source_id}")
        for required in (
            "title",
            "publisher",
            "source_type",
            "access",
            "use_boundary",
            "persistence",
        ):
            if required not in source:
                raise RegistryError(f"{source_id}に{required}がありません")
    return value


def _summary(source_id: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": source["title"],
        "publisher": source["publisher"],
        "source_type": source["source_type"],
        "access_mode": source["access"]["mode"],
        "default_persistence": source["persistence"]["default"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m bootstrap.cli.sources",
        description="公式参照先と、都度参照・cache・SQLite・snapshotの境界を表示",
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="参照先の概要を一覧表示")
    show = subparsers.add_parser("show", help="参照先の完全な定義を表示")
    show.add_argument("source_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_registry(args.registry)
        sources = registry["sources"]
        if args.command == "list":
            result = {
                "schema_version": registry.get("schema_version"),
                "sources": [
                    _summary(source_id, source)
                    for source_id, source in sorted(sources.items())
                ],
            }
        else:
            if args.source_id not in sources:
                raise RegistryError(f"未登録のsource_idです: {args.source_id}")
            result = {
                "schema_version": registry.get("schema_version"),
                "source_id": args.source_id,
                **sources[args.source_id],
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RegistryError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
