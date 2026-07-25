# 全国自治体観測ヒント

`municipalities.jsonl`は、任意のcompanionである
[`lcaios-explorer`](https://github.com/i8ei/lcaios-explorer)が全国の自治体公式サイトを
同じ取得契約で観測した結果を、ブートストラップ開始時のヒントへ縮約したsnapshotである。

これは現在の公式入口を保証するregistryではない。AIや人が毎回サイト全体をゼロから
読む代わりに、最初に再確認すべき少数ページを選ぶための過去観測である。

## 利用者に必要なもの

通常利用にCloudflareアカウントや`lcaios-explorer`は不要である。snapshotは
リポジトリに同梱され、preflightが既定で自動読込する。

```bash
python3 -m bootstrap.cli.preflight \
  --prefecture '都道府県名' \
  --municipality '自治体名' \
  --output /tmp/municipality-preflight.json \
  --cache-dir /tmp/municipality-preflight-cache
```

比較試験やsnapshot不使用の挙動を確認する場合だけ`--no-observatory-hints`を指定する。
Tier 0〜1の通常bootstrap reportにも、対象自治体1件の観測が`source_discovery`として
入る。

## 現行snapshot

`manifest.json`が来歴とhashの機械可読な正本である。2026-07-25生成分の内容は次の通り。

| 項目 | 値 |
|---|---:|
| 自治体 | 1,741 |
| 都道府県 | 47 |
| JSONL容量 | 約1.34MB |
| source URLを持つ自治体 | 1,084 |
| source URL entry | 3,062 |
| 候補ページ | 4,166 |
| 候補ページを持つ自治体 | 1,541 |
| `javascript_candidate` | 54 |

1,741は同梱registryの全基礎自治体数である。このうち公式ホームを取得できた1,650自治体が
静的深度1の対象となり、残る91自治体は`source_run_stopped`として停止理由を保持する。
したがって「1,650」は自治体総数ではなく、初回取得後に深度1へ進めた件数である。

| lane | 自治体 | 意味 |
|---|---:|---|
| `covered` | 1,252 | 4種類のsource入口を1つ以上観測 |
| `source_gap` | 274 | 静的深度1まで取得したが対象source入口を未観測 |
| `depth1_no_candidates` | 109 | 公式ホームから深度1候補を選べなかった |
| `depth1_partial` | 15 | 深度1候補の一部だけ取得 |
| `source_run_stopped` | 91 | 公式ホーム取得前後で安全停止 |

来歴:

- source run: `0d13d710-9df7-4ead-8699-e448d1c2a48e`
- depth1 representative: `0db3fe43-9149-4f18-ac41-ed7f6c058f15`
- depth1 rollout: `c0c9af7e-ab61-4b98-952b-6dba29666527`
- explorer revision: `0b5f748`
- scope version: 8

## 1自治体のデータ契約

JSONLは5桁標準地域コード順で、1自治体を1行に保存する。

| field | 意味 |
|---|---|
| `area_code_5` | 同梱municipality registryと照合する5桁コード |
| `prefecture_name` / `municipality_name` | registryと完全一致する表示名 |
| `observed_at` | 公式ホームと深度1を観測した時刻。現在性の保証ではない |
| `lane` | `covered`、`source_gap`、取得停止等の排他的な構造分類 |
| `navigation_mode` | `static`、`javascript_candidate`、`unknown` |
| `acquisition` | 公式ホームと深度1の取得状態 |
| `source_kinds` | 過去観測で見えた`minutes / regulations / budget / settlement` |
| `source_urls` | 種別ごとの過去観測URL。現在の公式入口とは限らない |
| `candidate_pages` | live preflightで優先して再確認する同一公式host候補 |
| `vendor_signals` | g-reiki、kaigiroku.net等の構造signal |
| `stop_reasons` | 観測を完了できなかった理由 |
| `profile_id` | 同じ粗い構造をまとめるexplorer側profile |

`catalog.py`はロード時に次を検査する。

- snapshot SHA-256
- municipality registry SHA-256
- 1,741自治体の完全一致、重複、欠落
- コードと自治体名の一致
- lane、navigation mode、source種別
- URL scheme、配列の重複と順序

## runtimeで起きること

```text
自治体コードを解決
  ↓
snapshotから同じコードの過去観測を1件取得
  ↓
現在の公式ホームとrobots.txtを確認
  ↓
同一公式hostの候補ページをページ上限内で優先取得
  ↓
現在のHTMLから得たevidenceでstatusを判定
```

台帳の公式ホームがHTTPで、同一公式hostのHTTPS URLをsnapshotで観測済みの場合は、
HTTPSの公式ホームを取得入口にする。この場合もHTTPS側の`robots.txt`を新たに確認し、
過去の取得成功を流用しない。

外部vendor URLやPDFは候補としてreportへ出せるが、preflight自身は取得しない。状態コードの
読み方は[ブートストラップ文書](../README.md#preflight結果の読み方)を参照する。

## 信頼境界

- snapshotだけでsourceを`ready`へ昇格しない
- 同一hostの候補ページは公式ホーム取得後に優先して再確認する
- 外部vendor URLは、現在の公式ページから再確認できるまで
  `human_confirmation_required`とする
- 過去観測でsource種別だけ見えた場合もlive discoveryまたは人の確認を要求する
- 公式ホームがrobotsで停止した場合、snapshotで上書きしない
- `source_not_found`を「資料が存在しない」と解釈しない
- raw HTML、PDF、統計値、作業DB、住民情報をGitへ入れない
- snapshotによってトークン量が必ず一定割合減るとは約束しない。読む候補を限定する仕組みである

Tier 0〜1の通常bootstrapはsnapshot破損を警告として扱い、全国共通データの構築を
継続する。preflightは候補の信頼境界が壊れた状態で暗黙に続行せず、明示エラーで停止する。
snapshotを使わず比較する場合は`--no-observatory-hints`を指定する。
`--offline`では検証済みcacheだけを使い、公式ホームがcacheにない場合は過去観測で
補完せず`human_confirmation_required`に留める。

## ファイル

| path | 役割 |
|---|---|
| `municipalities.jsonl` | Git同梱の現行snapshot |
| `manifest.json` | 来歴、件数、SHA-256、信頼境界 |
| `catalog.py` | runtime loaderと全件validation |
| `update.py` | D1 exportを正規化してsnapshotを置換するgenerator |
| [`MAINTENANCE.md`](MAINTENANCE.md) | D1 export、更新、検証、レビュー手順 |

snapshotは日付別に蓄積しない。現行ファイルを置き換え、履歴はGitに持たせる。
