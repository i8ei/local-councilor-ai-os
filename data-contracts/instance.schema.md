# Instance設定契約

## 目的

`<vault>/.local-councilor-ai-os/instance.json`は、対象VaultとVault外を含むデータartifactの位置を結ぶlocatorである。ready状態や政治判断を手入力する場所ではない。

## 最小例

```json
{
  "schema_version": 1,
  "product": "local-councilor-ai-os",
  "paths": {
    "bootstrap_database": ".local-councilor-ai-os/data/bootstrap/municipality.db"
  }
}
```

相対pathは対象Vaultを基準に解決する。絶対pathも利用できるため、SQLiteをVault外へ置く運用を妨げない。

## v1フィールド

| フィールド | 必須 | 説明 |
|---|---:|---|
| `schema_version` | yes | 現在は`1` |
| `product` | yes | `local-councilor-ai-os` |
| `paths.bootstrap_database` | no | Tier 1 `municipality.db` |

`lcaios status`はinstanceがない場合も停止せず、対象モジュールを`not_configured`として報告する。不正JSON、未対応schema、product不一致は警告し、手入力でreadyへ昇格させない。

