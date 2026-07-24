# 鮮度契約

## 目的

取得日時が新しいことと、収録データが最新であることを分ける。statusは保存済み来歴とsource registryだけを使って読み取り専用で判定し、ネットワークへ自動接続しない。

## 共通フィールド

| フィールド | 説明 |
|---|---|
| `source_periods` | DBに収録した基準日・会計年度 |
| `retrieved_at` | 原典を取得した日時 |
| `latest_period_checked` | その取得時に最新として確認した対象期 |
| `checked_at` | 最新期を公式経路で確認した日時 |
| `check_due_at` | source registryの確認間隔から計算した次回期限 |
| `recommended_check_interval_days` | 公式索引を再確認する標準間隔 |
| `state` | `fresh / due / stale / unknown` |
| `reason` | 状態を決めた機械判定理由 |

## 状態

- `fresh`: 公式経路で確認した最新期を収録し、再確認期限内
- `due`: 最後に確認した時点では最新だったが、registryの確認期限を超過
- `stale`: より古いfallbackを使用した、または新しい公表期の存在を確認済み
- `unknown`: 対象期、取得日時、確認方法のいずれかが不足

`fetched_at`やDBの`built_at`だけでは`fresh`にしない。offline rebuildは保存済み原典の取得日時を引き継ぎ、最新期を再確認した日時として更新しない。

## Tier 1

Tier 1では、国勢調査3指標を`estat-api-v3`、財政6指標を`soumu-municipal-fiscal-overview`として別々に判定する。全体状態は最も慎重なsource状態を採用する。

国勢調査fallbackを使用した場合は、取得日時が新しくても`stale`とする。

