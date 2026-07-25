# 全国自治体観測ヒント

`municipalities.jsonl`は、任意のcompanionである
[`lcaios-explorer`](https://github.com/i8ei/lcaios-explorer)が全国の自治体公式サイトを
同じ取得契約で観測した結果を、ブートストラップ開始時のヒントへ縮約したsnapshotである。

自治体ごとに次を持つ。

- 公式ホームと静的深度1の取得状態
- 静的／JavaScript候補というnavigation mode
- 議事録、例規、予算、決算の既観測source種別と候補URL
- 深度1で取得した優先候補ページ
- g-reiki、kaigiroku.net、Discuss、VOICES等のvendor signal
- `covered`、`source_gap`、停止等の観測laneと停止理由

## 信頼境界

これは現在の公式入口を保証するregistryではない。過去の決定的観測であり、次のlive
preflightを短くするための優先順位である。

- snapshotだけでsourceを`ready`へ昇格しない
- 同一hostの候補ページは公式ホーム取得後に優先して再確認する
- 外部vendor URLは、現在の公式ページから再確認できるまで
  `human_confirmation_required`とする
- raw HTML、PDF、作業DBをGitへ入れない
- `manifest.json`のsnapshot SHA-256と自治体registry SHA-256が一致しなければ使わない

## 更新

正本はCloudflare D1である。D1からobservatory queryと同じ列をJSON exportし、次の
generatorで正規化する。

```bash
python3 -m bootstrap.observatory.update \
  --input /tmp/lcaios-observatory-export.json \
  --source-run-id <source-run-id> \
  --depth1-pilot-id <representative-pilot-id> \
  --depth1-pilot-id <rollout-pilot-id> \
  --scope-version <scope-version> \
  --explorer-revision <git-revision> \
  --replace
```

generatorは1,741自治体のコード・名称を同梱registryと照合し、重複、欠落、URL、
snapshot hashを検査する。月次snapshotをGitへ追加せず、現行ファイルを置き換えて履歴は
Git自身に持たせる。
