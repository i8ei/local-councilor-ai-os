# Run manifest契約

## 目的

Run manifestは、データ取得・変換・scaffoldの各実行について、入力、実装版、対象範囲、出力、検証結果を再確認するための機械可読な記録である。判断の正本ではなく、OSの状態を再計算するための来歴である。

## 互換性

`onboarding`が既に出力するschema version 1を基礎とする。既存のトップレベルフィールドを削除または意味変更しない。共通フィールドを追加する場合も、version 1のonboarding manifestを`lcaios status`が読めなければならない。

## 共通フィールド

| フィールド | 説明 |
|---|---|
| `schema_version` | manifest形式の版 |
| `product` | `local-councilor-ai-os` |
| `source_revision` | 実行したリポジトリのcommit。既存互換のためトップレベルに保持 |
| `run_id` | 上書きしない実行ID |
| `run_type` | `onboarding`、`profile`、`bootstrap`、`minutes`、`regulations`、`benchmark`、`budget`、`settlement`等。旧manifestでは省略可能 |
| `status` | run自体の状態。旧onboarding語彙もreaderが解釈する |
| `started_at` / `finished_at` | UTC ISO 8601 |
| `target` / `scope` | Vault、自治体、対象期間、会計等 |
| `inputs` | 原典URL、保存原本、hash。秘密値は含めない |
| `artifacts` / `outputs` | 出力path、hash、schema、件数 |
| `checks` | integrity、検算、対象Vault確認等 |
| `warnings` / `failures` | 未解決事項と失敗 |

取得を伴うrunは、可能な場合に`retrieval`へcache directory、offline／refresh、
live request数、cache hit／miss、取得元別内訳、最新性をこのrunで再確認したかを記録する。
秘密値を含むquery parameterはredactする。

## モジュール固有のready条件

`lcaios status`は成功manifestの出力hashとSQLite integrityに加え、次のcheckを要求する。

| `run_type` | 必須check |
|---|---|
| `minutes` | `meeting_rows` |
| `regulations` | `document_rows` |
| `benchmark` | `municipality_rows` |
| `budget` | `budget_reconciliation` |
| `settlement` | `settlement_reconciliation` |

`profile`はSQLite moduleではない。`inputs`に置いた`councilor_profile`と
`council_adapter`のartifact hash、および`human_profile_confirmation`を必須とする。本文はmanifestへ
保存しない。詳細は[`profile-confirmation.schema.md`](profile-confirmation.schema.md)。

予算・決算は取込だけではreadyにならず、`verify_totals.py`が成功したrun manifestが必要である。

## 状態の扱い

- `running`は完了とみなさない。
- `failed`は過去の最新成功runを無効化しない。最新runの失敗は警告し、成功runが一度もなければ`blocked`とする。
- onboardingの`status: incomplete`は、`scaffold_status: complete`と`profile_status: incomplete`を分けて解釈する。
- artifactごとに最新の完了runを候補とし、現在のhashと一致しない場合は`modified_after_run`相当としてreadyにしない。

## 秘密値

- 環境変数は値を保存せず、`present`または`missing`だけを記録する。
- URLの認証queryは既存HTTP層と同じredaction規則を使う。
- cache、DB、manifestへ`ESTAT_APPID`等の生値を保存しない。
