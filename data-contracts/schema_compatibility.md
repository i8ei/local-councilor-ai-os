# Schema互換・再構築契約

## Version

schema versionは`major.minor`として解釈する。既存の`1`は`1.0`と同義。

- major一致: 読み取り可能
- 既知より新しいminor: 追加列を無視して読み取り、警告を残す
- major不一致: 読み取りを止め、原典から再構築または明示migrationを要求
- version欠落・不正: `unknown`としてreadyにしない

## 再構築優先

原典と取得・変換記録から再生成できるSQLiteは、複雑なin-place migrationより次を優先する。

```text
原典・cache
→ 新schemaで.database.newを構築
→ integrity・件数・検算
→ 旧DBを保持
→ atomic replace
```

人の判断や注記はDBへ埋め込まず、Obsidian判断ノートへ置く。再構築で失ってはいけない人手情報がDB内にある場合は、再構築可能な派生層という前提が崩れているため、先にデータ境界を見直す。

## Backup

- backup前にsourceのintegrityとschema互換を検証する
- SQLite online backup APIで一貫したsnapshotを作る
- backupは既存ファイルを上書きしない
- sourceとbackupのSHA-256、作成日時、検証結果を表示する

## Recovery

- backup SHA-256の明示確認を要求する
- backupと復旧用一時DBを検証してから置換する
- 既存targetは削除せず`.previous-*`へ退避する
- target置換失敗時は旧DBを元へ戻す
- symlinkは復旧対象にしない

## 生成物一覧

manifestの`artifacts`と`outputs`から生成物を列挙できるようにする。列挙は読み取り専用で、削除しない。Vault内とVault外を区別し、利用者作成物を自動的に生成物と推定しない。

