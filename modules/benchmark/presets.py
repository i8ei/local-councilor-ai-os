"""Opinionated indicator bundles for safe municipal comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DerivedIndicator:
    """Describe a deterministic value computed from sourced indicators."""

    key: str
    label: str
    input_keys: tuple[str, str]
    formula: str
    definition: str
    unit_note: str


@dataclass(frozen=True)
class Preset:
    """Declare the indicators and comparison conditions for one bundle."""

    name: str
    label: str
    indicator_keys: tuple[str, ...]
    same_as_of_rule: str
    unit_notes: dict[str, str]
    caveats: tuple[str, ...]
    derived_indicators: tuple[DerivedIndicator, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.indicator_keys:
            raise ValueError("preset name and indicator_keys are required")
        if len(set(self.indicator_keys)) != len(self.indicator_keys):
            raise ValueError(f"duplicate indicator key in preset: {self.name}")
        missing_notes = set(self.indicator_keys) - set(self.unit_notes)
        if missing_notes:
            raise ValueError(
                f"unit notes missing in preset {self.name}: {sorted(missing_notes)}"
            )

    def to_meta(self) -> dict[str, Any]:
        """Return the public, value-free preset declaration."""

        derived_keys = [item.key for item in self.derived_indicators]
        notes = dict(self.unit_notes)
        notes.update(
            {item.key: item.unit_note for item in self.derived_indicators}
        )
        return {
            "name": self.name,
            "label": self.label,
            "indicator_keys": list(self.indicator_keys),
            "derived_indicator_keys": derived_keys,
            "rule": {
                "type": "same_as_of",
                "required": True,
                "basis": self.same_as_of_rule,
                "comparison": "exact_match",
            },
            "unit_notes": notes,
            "caveats": list(self.caveats),
        }


PRESETS: dict[str, Preset] = {
    "zaisei_kenzensei": Preset(
        name="zaisei_kenzensei",
        label="財政健全性",
        indicator_keys=(
            "zaiseiryoku_shisuu",
            "keijou_shuushi_hiritsu",
            "jisshitsu_kousaihi_hiritsu",
            "shourai_futan_hiritsu",
        ),
        same_as_of_rule="fiscal_year",
        unit_notes={
            "zaiseiryoku_shisuu": "指数。直近3か年平均であり、単年度値ではない。",
            "keijou_shuushi_hiritsu": "％。経常一般財源等に対する割合。",
            "jisshitsu_kousaihi_hiritsu": "％。標準財政規模に対する割合。",
            "shourai_futan_hiritsu": "％。原表の「-」は非該当・算定なし等を表し、0にしない。",
        },
        caveats=(
            "4指標を同一財政年度で確認し、単一指標だけで順位づけしない。",
            "将来負担比率の「-」は欠測のまま扱い、ゼロへ置換しない。",
            "会計範囲、算定方法、団体区分が異なる場合は定義と原典を確認する。",
        ),
    ),
    "kessan_gaiyou": Preset(
        name="kessan_gaiyou",
        label="決算概況",
        indicator_keys=("total_revenue", "total_expenditure"),
        same_as_of_rule="fiscal_year",
        unit_notes={
            "total_revenue": "原則として千円。普通会計の歳入決算総額。",
            "total_expenditure": "原則として千円。普通会計の歳出決算総額。",
        },
        caveats=(
            "歳入・歳出は同一財政年度かつ同一単位の場合だけ差引を計算する。",
            "歳入歳出差引は単純な派生値であり、実質収支や財政余力を示す値ではない。",
            "一人当たり比較を行う場合は、人口の定義と基準日を明示して別途計算する。",
        ),
        derived_indicators=(
            DerivedIndicator(
                key="revenue_expenditure_balance",
                label="歳入歳出差引",
                input_keys=("total_revenue", "total_expenditure"),
                formula="total_revenue - total_expenditure",
                definition=(
                    "派生値: 普通会計の歳入決算総額から歳出決算総額を差し引いた値。"
                    "実質収支ではない。"
                ),
                unit_note="歳入総額・歳出総額と同じ単位。出典値ではなく単純差引。",
            ),
        ),
    ),
    "jinkou_kouzou": Preset(
        name="jinkou_kouzou",
        label="人口構造",
        indicator_keys=(
            "population_total",
            "households_total",
            "population_65_plus_ratio",
        ),
        same_as_of_rule="census_date",
        unit_notes={
            "population_total": "人。国勢調査時点の常住人口。",
            "households_total": "世帯。国勢調査の世帯の種類「総数」。",
            "population_65_plus_ratio": "％。公式公表された65歳以上人口構成比。",
        },
        caveats=(
            "3指標を同一の国勢調査基準日で比較し、異なる調査年を混ぜない。",
            "65歳以上人口構成比は年齢不詳の扱いを含む原表定義を確認し、自前計算で置き換えない。",
            "一人当たり・一世帯当たり比較では、分母の定義と基準日を明示する。",
        ),
    ),
}


def get_preset(name: str) -> Preset:
    """Resolve a built-in preset by name."""

    try:
        return PRESETS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(PRESETS))
        raise ValueError(f"unknown preset {name!r}; choose from: {choices}") from exc
