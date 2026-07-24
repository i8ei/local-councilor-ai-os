# 予算レビュー

## 目的

このモジュールは、予算書や補正予算書から確認した数値をSQLiteへ格納し、歳入歳出の一致、階層合計、前年度比較、補正前後の整合を検算する。PDFから数字を取り出す汎用抽出器は提供しない。自治体ごとのPDF、AI、OCR、人手確認で読んだ結果を、SQLite入力契約に沿って投入する。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 予算行を total / 款 / 項 / 目 / 節 粒度で格納する |
| `sqlite_input_contract.md` | SQLiteへ投入するための入力列契約 |
| `csv_templates.py` | 取込用CSVヘッダーを出力する |
| `ingest_csv.py` | 取込用CSVをSQLiteへ投入する |
| `verify_totals.py` | 歳入歳出一致、階層合計、前年度比較、補正前後を検算する |
| `insights.py` | 検算後に人が確認する予算審議候補を生成する |
| `failure_patterns.md` | 予算PDF・入力時の典型的な失敗 |
| `budget_settlement_bridge.md` | 予算レビューと決算レビューの接続方針 |

CSVは正本ではなく、SQLiteへ投入するための中間入力形式である。検索、検算、分析候補生成はSQLite上で行う。

## 使い方

```sh
python3 -m modules.budget_review.csv_templates > budget.csv
python3 -m modules.budget_review.ingest_csv budget.csv --db budget.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/budget'
python3 -m modules.budget_review.verify_totals budget.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/budget'
python3 -m modules.budget_review.insights budget.db
```

`verify_totals.py` が失敗したDBでは、`insights.py` は候補を生成しない。確認候補は予算審議の入口であり、必要性、妥当性、削減可否を自動で決めない。
`lcaios status`で`module_ready:budget`にするには、取込manifestだけでなく、
`budget_reconciliation`が成功した検算manifestが必要である。

## 検算ゲート

- 歳入総額と歳出総額が一致する。
- total、款、項、目、節の合計が、存在する粒度の範囲で一致する。
- `current_year_amount - previous_year_amount = comparison_amount`。
- `pre_supplement_amount + supplement_amount = post_supplement_amount`。

## 決算との接続

予算DBは、決算レビューと接続して初めて一年サイクルを追える。当初予算、補正後予算、決算の予算現額、支出済額、不用額、繰越額を混同しない。接続方針は `budget_settlement_bridge.md` を参照する。
