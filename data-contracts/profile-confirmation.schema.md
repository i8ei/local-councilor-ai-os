# Profile確認契約

## 目的

議員本人の作業条件と議会固有運用は、AIが推定して完成扱いにしない。本人が確認した
`councilor-profile.yaml`と`council-adapter.md`について、本文をrun manifestへ複製せず、
pathとSHA-256だけを記録する。

## 標準コマンド

```bash
python3 -m lcaios profile confirm \
  --vault '/absolute/path/to/vault' \
  --profile '/absolute/path/to/vault/任意の棚/councilor-profile.yaml' \
  --council-adapter '/absolute/path/to/vault/任意の棚/council-adapter.md' \
  --confirm-human-reviewed
```

このコマンドはprofile本文を作成・修正しない。検証成功時だけ
`<vault>/.local-councilor-ai-os/runs/profile/`へappend-only manifestを新規作成する。

## 確認条件

- 両ファイルが対象Vault内の通常ファイルであり、symlinkではない
- UTF-8で、1ファイル1 MB以下
- `<...>`形式のplaceholderと`執筆予定`が残っていない
- YAMLの`schema_version`が`1`
- YAMLの`status`が`confirmed`、`ready`、`complete`のいずれか
- YAMLに`council`、`working_style`、`privacy`がある
- MarkdownにH1が1件以上、H2が2件以上あり、確認内容が100文字以上
- `--confirm-human-reviewed`で本人確認を明示している

## Manifest

`run_type`は`profile`、成功時の`status`は`succeeded`とする。`inputs`には
`councilor_profile`と`council_adapter`のpath、SHA-256、sizeだけを保存する。本文、
個人情報、内部資料、判断内容は保存しない。`checks`には次を記録する。

- `profile_contract`
- `council_adapter_contract`
- `human_profile_confirmation`

`lcaios status`は、成功manifest、両artifactの存在、Vault内包、非symlink、SHA-256、
人の確認checkを再検証してから`profile_ready: ready`とする。確認後に本文が変われば
`invalid`へ戻し、再確認を要求する。既存onboarding manifestは変更しない。
