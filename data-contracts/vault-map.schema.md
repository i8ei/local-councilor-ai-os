# 既存Vault役割対応契約

## 目的

`<vault>/.local-councilor-ai-os/vault-map.yaml`は、すでに運用されているObsidian Vaultの既存pathを、地方議員AI運用OSの論理的な役割へ対応付ける。既存ノートを移動・改名・複製する指示ではない。

## 形式

外部YAML依存を増やさず安全に検証するため、次の限定形式だけを受け付ける。

```yaml
schema_version: 1
product: local-councilor-ai-os
layout: preserve
managed_namespace: "地方議員AI運用OS"
roles:
  vault_home: "HOME.md"
  general_questions: "議会/一般質問"
  budget: "議会/予算"
  settlement: "議会/決算"
  templates: "Templates"
```

- `schema_version`は`1`
- `product`は`local-councilor-ai-os`
- `layout`は`preserve`
- `managed_namespace`と各pathはJSON互換の引用文字列
- role pathはVaultからの相対path
- `vault_home`は既存ファイル
- その他のroleは既存ディレクトリ
- 絶対path、Vault外、存在しないpath、symlink、未知のroleは拒否

## v1 role

| role | 意味 |
|---|---|
| `vault_home` | Vault全体の既存入口 |
| `sessions` | 会期ごとの作業場 |
| `general_questions` | 一般質問の現行作業場 |
| `question_archive` | 過去の一般質問 |
| `budget` | 予算審査 |
| `settlement` | 決算審査 |
| `regulations` | 条例・例規 |
| `inspections` | 行政視察 |
| `public_relations` | 広報・公開説明 |
| `resident_voices` | 住民の声の隔離領域 |
| `evidence_ledger` | 証拠台帳 |
| `templates` | 既存のObsidian template領域 |

全roleを埋める必要はない。自動診断は深さ3以内で名称が一意な既存pathだけを候補にする。候補がないroleを推測せず、新規棚も自動作成しない。

## 変更契約

`plan`は正規化した対応表、作成対象、SHA-256を表示する。`scaffold`は同じplan hashが再現できた場合だけ、未作成の`vault-map.yaml`を書き込む。同名ファイルの内容が異なる場合は上書き・統合せず停止する。

既存の`instance.json`がある場合はschemaとproductを確認して再利用し、編集しない。役割pathの変更は新しいplanとして確認し、既存mapを自動置換しない。
