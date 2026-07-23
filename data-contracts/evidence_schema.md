# 根拠データ契約

## 目的

この契約は、調査で見つけた根拠を、原典から質問案や公開説明まで追跡できる単位で保存するための最小仕様である。根拠は文章の飾りではなく、主張を再検証し、誤りを修正するための記録として扱う。

## 1レコードの責務

1レコードは、原典の特定箇所が支える一つの事実、または一つの数値を表す。複数の主張、複数の定義、異なる時点の値を一つのレコードへ詰め込まない。

分析、仮説、判断は根拠レコードそのものにせず、別のノートや成果物から根拠IDを参照する。

## 必須フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `evidence_id` | string | 内部で一意かつ安定したID |
| `kind` | enum | `numeric` または `textual` |
| `claim` | string | この根拠が直接支えられる最小の事実 |
| `value` | number/string/null | 数値根拠の値。原表記を保つ必要がある場合は文字列 |
| `unit` | string/null | 値の単位 |
| `as_of` | object | 基準時点または対象期間 |
| `definition` | object | 対象、範囲、算定方法 |
| `source` | object | 公表主体、文書、索引、原典内位置 |
| `verification` | object | 状態、確認項目、確認日時 |
| `provenance_ref` | string | 取得・抽出履歴への参照 |

`kind: numeric` では `value`、`unit`、`as_of`、`definition`、`source` を必須とする。`kind: textual` でも、`as_of` は原典の施行日、公開日、対象期間など、主張の時間的な有効範囲が分かる形で記録する。

## 数値の4点セット

### `value`

- 値と単位を分離して保持する
- 丸め前の値がある場合は `raw_value` に保持する
- 丸め、概数化、単位換算は `transformations` に記録する
- 欠損、秘匿、非該当、ゼロを同じ値で表さない

### `as_of`

時点値と期間値を区別する。

```yaml
as_of:
  type: "<point-or-period>"
  date: "<YYYY-MM-DD-or-null>"
  starts_on: "<YYYY-MM-DD-or-null>"
  ends_on: "<YYYY-MM-DD-or-null>"
  label: "<source-period-label>"
```

### `definition`

```yaml
definition:
  text: "<definition-in-source-context>"
  population: "<included-subjects>"
  exclusions: []
  calculation: "<calculation-or-null>"
  accounting_scope: "<scope-or-null>"
  definition_source_ref: "<source-location-or-null>"
```

### `source`

```yaml
source:
  source_id: "<source-registry-id>"
  publisher: "<official-publisher>"
  title: "<official-document-or-dataset-title>"
  release_label: "<release-label>"
  index_url: "<official-index-page-url>"
  resolved_url_at_fetch: "<resolved-url>"
  location:
    page: "<page-or-null>"
    table: "<table-or-null>"
    sheet: "<sheet-or-null>"
    cell_or_row: "<cell-row-or-null>"
    section: "<section-or-null>"
  published_at: "<ISO-8601-date-or-null>"
  retrieved_at: "<ISO-8601-datetime>"
```

`index_url` は、第三者が公開経路を確認するための公式索引ページを指す。`resolved_url_at_fetch` は取得時の記録であり、将来も有効な固定URLとはみなさない。

## 推奨フィールド

| フィールド | 説明 |
|---|---|
| `raw_value` | 変換前の原表記 |
| `transformations` | 丸め、単位換算、計算の決定的な手順 |
| `comparison` | 比較可能性と差異の記録 |
| `excerpt` | 必要最小限の引用または表見出しを含む抜粋 |
| `context_pack_refs` | この根拠を含めたコンテキストパック |
| `used_by` | 質問案、説明資料、判断ノートなどからの参照 |
| `supersedes` | 差し替え前の根拠ID |
| `notes` | 未解決事項や利用上の注意 |

## 検証オブジェクト

```yaml
verification:
  state: "<draft-or-discovered-or-verified-or-reconciled-or-rejected>"
  checked_at: "<ISO-8601-datetime-or-null>"
  checked_by: "<reviewer-role-or-null>"
  checks:
    official_publisher: null
    index_to_source_route: null
    version_and_period: null
    transcription: null
    definition_and_scope: null
    unit: null
    totals_reconciled: null
    cross_check: null
  issues: []
  rejection_reason: "<reason-or-null>"
```

検証状態の意味は次のとおりとする。

- `draft`：入力途中で、原典未確認を含む
- `discovered`：公式の原典候補と公開経路を確認した
- `verified`：原典、定義、時点、単位、転記を確認した
- `reconciled`：合計値または独立した関連資料との整合まで確認した
- `rejected`：旧版、対象外、誤抽出などにより利用しない

状態を進めるときは、対応する `checks` と `checked_at` を更新する。状態だけを手入力で変更しない。

## 完全なレコード形

```yaml
evidence_id: "<stable-id>"
kind: "<numeric-or-textual>"
claim: "<single-verifiable-claim>"
value: "<value-or-null>"
raw_value: "<raw-value-or-null>"
unit: "<unit-or-null>"
as_of:
  type: "<point-or-period>"
  date: "<YYYY-MM-DD-or-null>"
  starts_on: "<YYYY-MM-DD-or-null>"
  ends_on: "<YYYY-MM-DD-or-null>"
  label: "<source-period-label>"
definition:
  text: "<definition-in-source-context>"
  population: "<included-subjects>"
  exclusions: []
  calculation: "<calculation-or-null>"
  accounting_scope: "<scope-or-null>"
  definition_source_ref: "<source-location-or-null>"
source:
  source_id: "<source-registry-id>"
  publisher: "<official-publisher>"
  title: "<official-document-or-dataset-title>"
  release_label: "<release-label>"
  index_url: "<official-index-page-url>"
  resolved_url_at_fetch: "<resolved-url>"
  location:
    page: "<page-or-null>"
    table: "<table-or-null>"
    sheet: "<sheet-or-null>"
    cell_or_row: "<cell-row-or-null>"
    section: "<section-or-null>"
  published_at: "<ISO-8601-date-or-null>"
  retrieved_at: "<ISO-8601-datetime>"
transformations: []
comparison:
  comparable: null
  differences: []
verification:
  state: "<verification-state>"
  checked_at: "<ISO-8601-datetime-or-null>"
  checked_by: "<reviewer-role-or-null>"
  checks: {}
  issues: []
  rejection_reason: "<reason-or-null>"
provenance_ref: "<provenance-record-id>"
context_pack_refs: []
used_by: []
supersedes: "<evidence-id-or-null>"
notes: []
```

## 外部利用条件

外部成果物で確定的に使えるのは、原則として `verified` または `reconciled` の根拠だけとする。予算・決算の総額や内訳のように合計突合が必要な値は `reconciled` を要求する。

外部利用時は、レコードをそのまま大量に貼り付けるのではなく、主張に必要な根拠だけを選び、`value`、`as_of`、`definition`、`source` が読者に分かる表示へ変換する。変換後も `evidence_id` から元レコードと原典へ戻れるようにする。

## 更新と訂正

原典の改訂や誤りの発見時は、過去のレコードを黙って上書きしない。新しいレコードを作成し、`supersedes` で旧レコードを参照する。旧レコードを `rejected` にする場合は理由を残し、`used_by` を使って影響する成果物を確認する。
