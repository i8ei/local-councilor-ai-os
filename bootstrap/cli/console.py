"""Console presentation and progress reporting for municipal bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

LABEL_MAP: dict[str, str] = {
    "population_total": "総人口",
    "households_total": "世帯数",
    "population_65_plus_ratio": "高齢化率 (65歳以上)",
    "zaiseiryoku_shisuu": "財政力指数",
    "keijou_shuushi_hiritsu": "経常収支比率",
    "jisshitsu_kousaihi_hiritsu": "実質公債費比率",
    "shourai_futan_hiritsu": "将来負担比率",
    "total_revenue": "歳入決算総額",
    "total_expenditure": "歳出決算総額",
}


def format_indicator_value(indicator: str, val: Any, unit: str) -> str:
    """Format numerical values nicely with appropriate units and rounding."""
    if val is None:
        return "-"
    clean_unit = "%" if unit in {"％", "%"} else unit
    if isinstance(val, (int, float)):
        if "ratio" in indicator or "hiritsu" in indicator or clean_unit == "%":
            return f"{float(val):.1f} %"
        if clean_unit == "index":
            return f"{float(val):.2f}"
        if isinstance(val, int):
            return f"{val:,} {clean_unit}".strip()
        return f"{val:.2f} {clean_unit}".strip()
    return f"{val} {clean_unit}".strip()


class BootstrapConsole:
    """Human-friendly rich terminal output on stderr."""

    def __init__(self, quiet: bool = False, color: bool | None = None) -> None:
        self.quiet = quiet
        if color is None:
            self.use_color = sys.stderr.isatty() and not quiet
        else:
            self.use_color = color and not quiet

    def _c(self, text: str, code: str) -> str:
        if not self.use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._c(text, "1")

    def cyan(self, text: str) -> str:
        return self._c(text, "36")

    def green(self, text: str) -> str:
        return self._c(text, "32")

    def yellow(self, text: str) -> str:
        return self._c(text, "33")

    def magenta(self, text: str) -> str:
        return self._c(text, "35")

    def header(self, municipality_name: str) -> None:
        if self.quiet:
            return
        w = 68
        line = "═" * (w - 2)
        title = "🏛️  地方議員AI運用OS — 自治体データ基盤ブートストラップ"
        sys.stderr.write(f"\n╔{line}╗\n║  {self.bold(title)}  ║\n╚{line}╝\n\n")
        sys.stderr.flush()

    def step(self, num: int, total: int, icon: str, title: str) -> None:
        if self.quiet:
            return
        sys.stderr.write(f"{self.bold(f'[{num}/{total}]')} {icon} {self.bold(title)}\n")
        sys.stderr.flush()

    def item(self, text: str, status: str = "✔") -> None:
        if self.quiet:
            return
        if status == "✔":
            symbol = self.green(status)
        elif status in {"⏳", "..."}:
            symbol = self.cyan(status)
        elif status == "·":
            symbol = self._c(status, "90")
        else:
            symbol = self.yellow(status)
        sys.stderr.write(f"  {symbol} {text}\n")
        sys.stderr.flush()

    def data_row(self, label: str, value: str, note: str = "", is_last: bool = False) -> None:
        if self.quiet:
            return
        prefix = "    └" if is_last else "    ├"
        val_str = self.cyan(value)
        note_str = f" ({note})" if note else ""
        sys.stderr.write(f"{prefix} {label:<16} : {val_str}{note_str}\n")
        sys.stderr.flush()

    def summary(self, report: dict[str, Any]) -> None:
        if self.quiet:
            return
        muni = report.get("municipality", {})
        muni_name = f"{muni.get('prefecture', '')}{muni.get('name', '')}"
        code = muni.get("area_code_5", "")
        lg_code = muni.get("local_government_code_6", "")

        census_count = report.get("census", {}).get("indicator_count", 0)
        fiscal_count = report.get("fiscal", {}).get("indicator_count", 0)
        fiscal_year = report.get("fiscal", {}).get("fiscal_year", "")
        db_path = report.get("database", {}).get("path", "")
        auth_path = report.get("authority_map", {}).get("path", "")

        w = 68
        sep = "═" * w
        sys.stderr.write(f"\n{sep}\n")
        sys.stderr.write(f"{self.bold(self.green(f'🎉 {muni_name} のAI政策仕事場が手元に立ち上がりました！'))}\n")
        sys.stderr.write(f"{sep}\n")
        sys.stderr.write(f"  🏢 自治体コード : {code} (全国地方公共団体コード: {lg_code})\n")
        sys.stderr.write(
            f"  📊 収録指標数   : {census_count + fiscal_count} 件 "
            f"(国勢調査: {census_count}件 / 財政: {fiscal_count}件 [令和{fiscal_year}年度])\n"
        )
        sys.stderr.write(f"  🗄️  データベース : {self.cyan(str(db_path))}\n")
        sys.stderr.write(f"  📋 根拠裁定表   : {self.cyan(str(auth_path))}\n\n")
        sys.stderr.write(f"💡 {self.bold('次の一手（コマンドをコピーして実行）')}:\n")
        profiles_dir = Path("source_profiles/municipalities")
        profile_path = next(profiles_dir.glob(f"**/{code}*.json"), None) if code and profiles_dir.is_dir() else None

        if profile_path:
            sys.stderr.write("  1. 登録済みプロファイルから公式データ（議事録・例規）を一括取得:\n")
            sys.stderr.write(f"     {self.yellow(f'python3 -m source_profiles.cli ingest-command --profile {profile_path}')}\n")
        else:
            sys.stderr.write("  1. 議事録・例規・決算の公式入口を事前診断:\n")
            cmd_str = (
                f"python3 -m bootstrap.cli.preflight "
                f"--prefecture '{muni.get('prefecture', '')}' --municipality '{muni.get('name', '')}'"
            )
            sys.stderr.write(f"     {self.yellow(cmd_str)}\n")

        sys.stderr.write("  2. 見取り図ノート（MOC）をObsidian Vaultに生成:\n")
        sys.stderr.write(
            f"     {self.yellow('python3 -m lcaios dashboard --vault /path/to/your/vault --write-vault')}\n"
        )
        sys.stderr.write(f"{sep}\n\n")
        sys.stderr.flush()
