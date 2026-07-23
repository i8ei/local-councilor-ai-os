# 自治体比較

全国統一の公式統計・財政資料から作られた `bootstrap` の `municipality.db` を束ね、比較用SQLiteを作る最小モジュールです。順位づけではなく、定義、時点、団体区分が比較可能な範囲を明示して政策上の問いを深めるために使います。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 自治体と指標値を、4点セットと来歴つきで格納する |
| `build_from_bootstrap.py` | 複数の `municipality.db` から比較DBを作る |
| `compare.py` | 指標と時点を指定して比較JSONを出す |

## 使い方

まず各自治体で `bootstrap` を実行して `municipality.db` を作ります。

```bash
python3 -m bootstrap.cli '太良町' --out-dir bootstrap/output/太良町 --cross-check
python3 -m bootstrap.cli '川棚町' --prefecture '長崎県' --out-dir bootstrap/output/川棚町 --cross-check
```

次に比較DBを作ります。

```bash
python3 modules/benchmark/build_from_bootstrap.py \
  bootstrap/output/太良町/municipality.db \
  bootstrap/output/川棚町/municipality.db \
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

## 限界

- このモジュール自体は新しい公式データを取得しません。取得と検証は `bootstrap` 側に委ねます。
- 指標の比較可能性は、同一 `indicator_key` と同一 `as_of` を前提にします。会計範囲や団体区分の差が政策上重要な場合は、出力JSONの定義と原典を確認してください。
- 欠測値はゼロではなく `null` のまま扱います。
