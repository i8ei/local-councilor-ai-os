# 決算レビュー SQLite 入力契約

## 目的

この契約は、PDF、表抽出、OCR、AI、人手入力など任意の方法で読み取った決算数値を、検算可能なSQLiteへ入れるための入力契約である。CSVは正本ではなく、SQLiteへ投入するための単純な中間入力形式である。PDFから数字を読む方法は自治体、年度、会計、資料公開範囲によって異なるため、本モジュールは抽出器を汎用化しない。共通化するのは、SQLiteへ格納する前に満たすべき列、粒度、原典位置、検証状態である。

## 取込用CSVの種別

`ingest_csv.py` はSQLiteへ投入する中間入力として、次の3種類のCSVを受け付ける。

| 種別 | 対応表 | 粒度 |
|---|---|---|
| `summary` | 歳入歳出の総括表 | 款 |
| `revenue` | 歳入明細 | 款・項 |
| `expenditure` | 歳出明細 | 款・項・目・節 |

投入順は `summary`、`revenue`、`expenditure` を推奨する。最後に必ず `verify_totals.py` を実行する。

```sh
python3 modules/settlement-review/ingest_csv.py summary summary.csv --db settlement.db
python3 modules/settlement-review/ingest_csv.py revenue revenue.csv --db settlement.db
python3 modules/settlement-review/ingest_csv.py expenditure expenditure.csv --db settlement.db
python3 modules/settlement-review/verify_totals.py settlement.db
```

## 共通必須列

すべてのCSVに次の列を置く。

| 列 | 内容 |
|---|---|
| `fiscal_year` | 年度。西暦整数 |
| `account_name` | 会計名。一般会計、国民健康保険特別会計など |
| `raw_value` | 原表行またはセル群の生表記。AIの整形結果ではなく、確認に戻れる最小の原表断片 |
| `unit` | 原表の単位。円、千円など。暗黙に換算しない |
| `as_of` | 対象年度・期間。例: `2025年度決算` |
| `definition` | 表・列の定義。普通会計/一般会計、予算現額/支出済額などを含める |
| `source_name` | 公式資料名 |
| `source_url` | 公式に到達できるURL。非公開資料では外部利用不可の扱いを明記する |
| `source_locator` | JSON文字列。PDFページ、印刷ページ、表名、行番号、セル範囲など |
| `fetched_at` | 原典取得または確認時刻。ISO 8601 |
| `verification_state` | `draft`、`discovered`、`verified`、`reconciled`、`rejected` |
| `print_page` | 資料に印字されたページ |
| `pdf_page` | PDFビューア上のページ。1始まり |

`source_locator` の例:

```json
{"pdf_page": 12, "print_page": "10", "table": "歳出決算事項別明細書", "row": "2款1項1目", "columns": ["予算現額", "支出済額", "不用額"]}
```

## summary.csv

追加必須列:

| 列 | 内容 |
|---|---|
| `side` | `revenue` または `expenditure` |
| `kan_code` / `kan_name` | 款コード・款名 |
| `budget_current_amount` | 予算現額 |

`side = revenue` の追加必須列:

- `collected_amount`
- `uncollectible_amount`
- `outstanding_amount`

`side = expenditure` の追加必須列:

- `spent_amount`
- `carryover_amount`
- `unused_amount`

## revenue.csv

追加必須列:

- `kan_code`, `kan_name`
- `ko_code`, `ko_name`
- `budget_current_amount`
- `collected_amount`
- `uncollectible_amount`
- `outstanding_amount`

## expenditure.csv

追加必須列:

- `kan_code`, `kan_name`
- `ko_code`, `ko_name`
- `moku_code`, `moku_name`
- `setsu_code`, `setsu_name`
- `item_budget_current_amount`
- `item_spent_amount`
- `item_carryover_amount`
- `item_unused_amount`
- `section_budget_current_amount`
- `section_spent_amount`
- `section_carryover_amount`
- `section_unused_amount`

歳出では、節行ごとに目合計が反復される帳票が多い。`verify_totals.py` は、目合計を重複加算しないよう、款・項・目で目合計を一度だけ数える。抽出時は反復されている目合計を消さず、原表どおり各節行に入れる。

## 数値表記

`ingest_csv.py` は整数列について、カンマ、`△`、Unicode minusを正規化する。単位換算はしない。`千円`表を`円`に変換したい場合は、別途 `transformations` を残す運用記録を作る。欠測、秘匿、非該当、ゼロは同じではない。意味が未確認なら `draft` または `needs_review` 相当の運用で止め、公開利用しない。

## 完了条件

取込用CSVをSQLiteへ投入しただけでは検証済みではない。少なくとも次を満たすまで、対外利用可能な決算DBと呼ばない。

1. `verify_totals.py` が終了コード0である。
2. 不一致がないことを人が原典で確認している。
3. `source_locator` から該当セルまたは原表行へ戻れる。
4. 公開資料か非公開資料かの境界が記録されている。
