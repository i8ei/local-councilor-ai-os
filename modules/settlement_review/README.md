# 決算レビュー

## 目的

このモジュールは、決算書の総括表と明細を検算可能な形へ整え、9月の決算審査で人が確認する増減や執行差の候補を作る。PDFから数字を取り出す処理そのものは自治体、年度、会計、資料公開範囲、PDF品質によって異なるため、汎用抽出器として作り込まない。

本モジュールが提供するのは、抽出後の数値をSQLiteへ格納するための入力契約、SQLiteスキーマ、差額ゼロ検算、分析候補生成、公開・非公開境界、ありがちな失敗パターンである。CSVは正本ではなく、PDF抽出AIや人手確認の結果をSQLiteへ渡すための中間入力形式である。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 総括表、歳入の款項明細、歳出の款項目節明細を格納する |
| `sqlite_input_contract.md` | `summary`、`revenue`、`expenditure` をSQLiteへ投入するための入力列契約 |
| `ingest_csv.py` | 人または個別AIが確認した取込用CSVをSQLiteへ投入する |
| `verify_totals.py` | 歳入の項から款、歳出の節から目、目から款を差額ゼロで突合する |
| `insights.py` | 検算を通ったDBから、人が確認する分析候補をJSONで出す |
| `insight_spec.md` | 分析候補と証拠台帳への受け渡しを定義する |
| `csv_templates.py` | SQLiteへ投入する取込用CSVのヘッダー雛形を出力する |
| `extraction_guidance.md` | PDF抽出を個別AIや人に任せる際の依頼方針 |
| `failure_patterns.md` | 単位、ページ、欠測、重複加算などの典型的な失敗 |
| `public_private_boundary.md` | 公開資料、内部資料、要配慮資料の境界 |
| `tests/create_fixtures.py` | 合格用と不合格用の小さな SQLite を再生成する |

`schema.sql` は、原表の複数金額列を名前付きの `value` として保持し、原行全体を `raw_value` に残す。すべての行は原単位を持つ。歳出明細は節粒度であり、同じ目合計が複数の節行に反復される前提なので、款集計の前に款、項、目で重複を除く。

## PDF抽出の位置づけ

PDFの版面、列位置、テキスト層、見開き、単位、会計区分、公開範囲は自治体ごとに異なる。したがって、このモジュールは「任意PDFを正しく読む」責任を持たない。利用者はローカルで、AI、人手、OCR、`pdftotext`、表抽出ツールなどを組み合わせてCSVを作る。

ただし、どの方法で抽出しても、最後は次を満たす必要がある。

1. `sqlite_input_contract.md` の列を満たす。
2. `source_locator` から原典位置へ戻れる。
3. `verify_totals.py` が終了コード0である。
4. 公開資料か非公開資料かを区別している。
5. 原因や政策評価を数値だけから自動確定しない。

## SQLiteへ投入する取込用CSV雛形

```sh
python3 -m modules.settlement_review.csv_templates summary > summary.csv
python3 -m modules.settlement_review.csv_templates revenue > revenue.csv
python3 -m modules.settlement_review.csv_templates expenditure > expenditure.csv
```

## SQLiteへの取込

```sh
python3 -m modules.settlement_review.ingest_csv summary summary.csv --db settlement.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/settlement'
python3 -m modules.settlement_review.ingest_csv revenue revenue.csv --db settlement.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/settlement'
python3 -m modules.settlement_review.ingest_csv expenditure expenditure.csv --db settlement.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/settlement'
python3 -m modules.settlement_review.verify_totals settlement.db \
  --manifest-dir '/path/to/vault/.local-councilor-ai-os/runs/settlement'
```

CSVを読み込むのは `ingest_csv.py` だが、検索、検算、分析候補生成の作業対象はSQLiteである。`ingest_csv.py` は整数列のカンマ、`△`、Unicode minusを正規化し、既存行は一意キーで更新する。ここで検証済みに昇格するわけではない。差額ゼロ検算と人の原典確認を通した後に `reconciled` として扱う。
`lcaios status`で`module_ready:settlement`にするには、取込manifestだけでなく、
`settlement_reconciliation`が成功した検算manifestが必要である。

## 分析候補

検算を通ったDBから、人が確認する候補を生成する。

```sh
python3 -m modules.settlement_review.insights settlement.db \
  --min-unused-ratio 0.1 \
  --min-carryover-ratio 0.1 \
  --min-outstanding-ratio 0.1
```

`insights.py` は実行時に `verify_totals.py` を通し、検算に失敗したDBでは候補を生成しない。出力は「大きな不用額」「大きな繰越」「大きな収入未済」などの確認候補であり、原因や妥当性を断定しない。

## 検算フィクスチャ

```sh
python3 -m modules.settlement_review.tests.create_fixtures
python3 -m modules.settlement_review.verify_totals \
  modules/settlement_review/tests/passing.db
python3 -m modules.settlement_review.verify_totals \
  modules/settlement_review/tests/failing.db
```

`passing.db` は終了コード0、`failing.db` は終了コード1を期待する。

## 外部利用条件

対外利用できるのは、原則として公式公開資料に基づき、差額ゼロ検算と人の原典確認を通った値だけである。非公開資料や内部資料から得た数値は、公開資料で検証可能な問いへ変換する。公開成果物には内部ファイルパス、内部リンク、非公開資料由来の数値を混ぜない。
