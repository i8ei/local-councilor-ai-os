# 自治体比較

全国統一の公式統計・財政資料から作られた `bootstrap` の `municipality.db` を束ね、比較用SQLiteを作る最小モジュールです。順位づけではなく、定義、時点、団体区分が比較可能な範囲を明示して政策上の問いを深めるために使います。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 自治体と指標値を、4点セットと来歴つきで格納する |
| `build_from_bootstrap.py` | 複数の `municipality.db` から比較DBを作る |
| `compare.py` | 単一指標またはプリセットを指定して比較JSONを出す |
| `presets.py` | 比較可能な指標の組み合わせと停止条件を宣言する |

## 使い方

まず各自治体で `bootstrap` を実行して `municipality.db` を作ります。

```bash
python3 -m bootstrap.cli 'A町' --out-dir bootstrap/output/A町 --cross-check
python3 -m bootstrap.cli 'B町' --prefecture '〇〇県' --out-dir bootstrap/output/B町 --cross-check
```

次に比較DBを作ります。

```bash
python3 modules/benchmark/build_from_bootstrap.py \
  bootstrap/output/A町/municipality.db \
  bootstrap/output/B町/municipality.db \
  --db benchmark.db
```

またはディレクトリを渡すと、配下の `municipality.db` を探索します。

```bash
python3 modules/benchmark/build_from_bootstrap.py bootstrap/output --db benchmark.db
```

比較します。

```bash
python3 modules/benchmark/compare.py zaiseiryoku_shisuu --db benchmark.db --limit 20
```

出力には `value / as_of / definition / source_name / source_url` が含まれます。対外利用時は値だけを抜き出さず、必ず4点セットで表示してください。

## 比較プリセット

プリセットは「数字をまとめて表示するショートカット」ではなく、比較してよい指標の組み合わせと条件をコード化したものです。Authority Router の `municipal_comparison`（自治体間比較）用途に対応し、異なる年度・調査日・定義を混ぜた比較や、単一の数字だけによる順位づけを避けます。

利用できる組み込みプリセットは次の3つです。

| 名前 | 内容 | 必須条件 |
|---|---|---|
| `zaisei_kenzensei` | 財政力指数、経常収支比率、実質公債費比率、将来負担比率 | 同一財政年度 |
| `kessan_gaiyou` | 歳入総額、歳出総額、派生値「歳入歳出差引」 | 同一財政年度・同一単位 |
| `jinkou_kouzou` | 総人口、総世帯数、65歳以上人口構成比 | 同一国勢調査基準日 |

```bash
python3 modules/benchmark/compare.py \
  --preset zaisei_kenzensei \
  --db benchmark.db
```

`rows` は A町、B町のような各自治体について、各指標を `value / as_of / definition / source` の4点セットで返します。欠測は `null` のままで、完全な行より後に並びます。`same_as_of_check.status` が `mismatch` の場合も行や値は削除されませんが、`bundle_comparison_allowed` は `false` になります。`--as-of 2024年度` のように時点を明示した場合、その時点にない値は別年度で補わず `null` にします。

`kessan_gaiyou` の `revenue_expenditure_balance` は `derived: true`、`source: null`、計算式つきで返します。歳入・歳出の年度または単位が違う場合は計算せず `null` とし、理由を `derivation.reason` に示します。この単純差引は実質収支ではありません。

各プリセットの `preset_meta` には、対象キー、同一時点ルール、単位上の注意、定義上の注意を含めます。特に次を自動で補正しません。

- 将来負担比率の `-` をゼロにする
- 異なる年度・国勢調査基準日の値を同じ比較として扱う
- 人口基準を指定せず一人当たり値を作る
- いずれか一指標だけで自治体を順位づけする

### カスタムプリセット

`presets.py` で `Preset` を定義し、`PRESETS` に登録します。少なくとも名前、表示名、元データの指標キー、`fiscal_year` または `census_date` などの同一時点ルール、各指標の単位注記、注意事項を指定します。

```python
PRESETS["custom_example"] = Preset(
    name="custom_example",
    label="カスタム比較",
    indicator_keys=("indicator_a", "indicator_b"),
    same_as_of_rule="fiscal_year",
    unit_notes={
        "indicator_a": "単位と分母を記載する。",
        "indicator_b": "単位と会計範囲を記載する。",
    },
    caveats=("同一年度・同一定義でのみ比較する。",),
)
```

派生値が必要な場合だけ `DerivedIndicator` を追加します。派生値は、入力が同じ `as_of`・同じ単位で揃う場合に限って計算されます。

## 限界

- このモジュール自体は新しい公式データを取得しません。取得と検証は `bootstrap` 側に委ねます。
- 指標の比較可能性は、同一 `indicator_key` と同一 `as_of` を前提にします。会計範囲や団体区分の差が政策上重要な場合は、出力JSONの定義と原典を確認してください。
- 欠測値はゼロではなく `null` のまま扱います。
