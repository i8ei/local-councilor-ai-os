# 自治体ブートストラップ

自治体名を一つ伝えると、エージェントが全国共通の公式公開情報を探索し、その自治体を調べるための SQLite データベースと `authority_map.yaml` の土台を作る。これが本プロジェクトのブートストラップである。目的は、最初から「それらしい数字」を集めることではない。どの用途で、どの原典を、どの定義と基準日で使うかを再現可能にすることである。

> [!IMPORTANT]
> ブートストラップは判断を自動化しない。取得・構造化・照合・検算を補助し、採用する根拠と最終判断は議員が担う。

## 生成するもの

初期構築では、少なくとも次の二つを生成対象とする。

- 自治体データベース: 自治体コード、指標、取得時刻、原典ロケータを格納する再構築可能な SQLite
- `authority_map.yaml`: `indicator × use_case` ごとに、参照すべき公式原典と SQLite 内の位置を示す裁定表

`authority_map.yaml` には指標値を複製しない。値は SQLite から取得し、裁定表は「何をどこから読むべきか」だけを管理する。

## Tier モデル

| Tier | 対象 | 自動化の目安 | 現時点の設計 |
|---|---|---:|---|
| 0 | 自治体名から標準地域コードを解決 | 高い | 同梱した全国基礎自治体registryを主経路、e-Stat地域メタ情報APIをfallbackにする |
| 1 | 国勢調査指標と財政指標 | 高い | e-Stat API と総務省の全国共通ファイルを、メタデータから探索する |
| 2 | 議事録 CMS・例規サイト | ベンダーごと | ベンダーを検出し、適合するアダプターへ振り分ける |
| 3 | 予算書・決算書など地域固有 PDF | 人の確認が必須 | 表抽出後に総額照合を行い、不一致なら公開利用を止める |

Tier 0 と Tier 1 は全国共通の入口を使えるが、公開側の表題、HTML、XLSX 配置は変更され得る。Tier 2 はベンダー差、Tier 3 は帳票差と PDF 品質の影響が大きく、完全自動化を約束しない。

## ブートストラップの流れ

1. 入力された自治体名を Unicode NFC に正規化する。
2. 同梱した全国基礎自治体registryで候補を検索し、正規化後の完全一致で絞る。未収録時だけ地域メタ情報APIへfallbackする。
3. 同名自治体が複数ある場合は、都道府県ヒントを求めて停止する。
4. 現行自治体の5桁標準地域コードを確定する。
5. e-Stat から、必要な指標が同一基準日で揃う最新の統計表を探索する。
6. 総務省の索引ページから最新の財政ファイルを発見し、6桁団体コードで行を照合する。
7. 各値を `value / as_of / definition / source` の4点セットで保存する。
8. 検算に通ったデータだけを SQLite と `authority_map.yaml` から利用可能にする。

## 原典 URL は「推測」せず「発見」する

総務省の配布ファイルには、`/main_content/` に続く数字を含む URL が多い。この数字は自治体コード、年度、資料種別から機械的に導出できる識別子ではなく、更新時に変わり得る不透明な ID である。

したがって、実装では次を守る。

1. 恒久的な索引ページを取得する。
2. 索引から対象年度ページへのリンクを発見する。
3. 年度ページから対象ファイルへのリンクを発見する。
4. 発見した URL、リンク元 URL、取得時刻、ファイルハッシュを記録する。
5. URL の数字部分を組み立てたり、検証時の URL を固定したりしない。

年度ページの URL に規則性が見えても、索引に掲載されたリンクを優先する。規則は探索を助けるヒントであって、原典の所在を保証する契約ではない。

## データの二層構造

取得した公開文書と配布ファイルは「原本」として保存し、SQLite/FTS5 はそこから再構築できる検索層とする。分析、迷い、比較、判断は Obsidian のノートを正本にする。

AI に渡すのは、検索層から用途に合わせて切り出した小さなコンテキストパックである。巨大な CSV、XLSX、PDF 全体やデータベースのダンプをそのまま渡さない。これにより、参照範囲、根拠、欠測、更新時点を人が追える状態にする。

## 検証ゲート

自動取得が成功しても、次の条件を満たさなければ公開利用可能とは扱わない。

- 自治体コードが一意に解決されている
- 指標の定義と単位が空欄でない
- 基準日または対象年度が明示されている
- 公式の閲覧用 URL または配布元ページへ戻れる
- 同じ意味条件で値が複数件になっていない
- 欠測記号を数値へ変換していない
- SQLite の整合性検査に通っている
- PDF 由来の金額は、原表の総額と照合できている

検証ゲートを通らないデータは、失敗理由とともに隔離する。過年度への自動フォールバックを行う場合は、「最新」という表示を外し、採用年度を明示する。

## キャッシュと再現

取得した原本、レスポンスのメタデータ、ハッシュをキャッシュし、同じ入力から SQLite を再構築できるようにする。初回オンライン取得後は、キャッシュだけを使うオフライン再実行で再現性を確かめる。

キャッシュは原典の代替ではない。公開元の更新確認にはオンライン取得が必要であり、前回取得物を「現在の最新資料」とみなしてはならない。

## 各 Tier の文書

- [Tier 0: 自治体コードの解決](resolve-code/README.md)
- [Tier 1: 全国共通データ](national-data/README.md)
- [Tier 2: ベンダーアダプター](vendor-adapters/README.md)
- [Tier 3: 地域固有文書](local-documents/README.md)

Tier 3はまず公式索引だけを診断し、候補を確認後に最大3件のsampleを取得できる。

```bash
python3 -m bootstrap.cli.local_documents diagnose \
  --index-url 'https://www.example.jp/finance/budget/'

python3 -m bootstrap.cli.local_documents sample \
  --index-url 'https://www.example.jp/finance/budget/' \
  --candidate 1 \
  --output-dir /tmp/municipality-budget-sample
```

sampleは原本、SHA-256、媒体型、PDF署名を記録し、`pdftotext`があれば先頭3ページの
テキスト層を確認する。数値抽出やSQLite化は行わず、確認後に予算・決算モジュールの
入力契約と検算ゲートへ渡す。

## 実装

Tier 0〜1 の標準ライブラリ版 CLI は `bootstrap/cli/` に実装した。次のどちらでも実行できる。

```bash
python3 -m bootstrap.cli '自治体名' [--prefecture '都道府県名'] \
  [--out-dir DIR] [--cache-dir DIR] [--offline] [--refresh] \
  [--cross-check] [--manifest-dir DIR]

python3 bootstrap/cli/main.py '自治体名' [--prefecture '都道府県名'] \
  [--out-dir DIR] [--cache-dir DIR] [--offline] [--refresh] \
  [--cross-check] [--manifest-dir DIR]
```

オンライン実行には `ESTAT_APPID` が必要である。e-Statのユーザー登録後、マイページの「ユーザ情報変更」→「登録内容変更」→「利用する機能」で「API機能」にチェックを入れ、「変更」で確定する。その後、「API機能（アプリケーションID発行）」からAppIdを発行する。ユーザー登録だけではAPI機能が有効になっていない場合がある。

AppId は e-Stat API リクエスト時だけ使用し、JSON 実行レポート、キャッシュメタデータ、SQLite、`authority_map.yaml` には保存しない。認証エラーでは、AppIdの値に加えて「API機能」の有効化と変更確定を確認する。既定の出力先は `bootstrap/output/<自治体名>/`、共有キャッシュは `bootstrap/.cache/` で、どちらも Git 管理対象外である。`--cache-dir`で実行単位のキャッシュを分離できる。`--refresh`は通常キャッシュとrobots cacheを使わず公式サイトを再確認するため、`--offline`とは同時に指定できない。

生成物は次の二つである。

- `municipality.db`: `municipality`、`indicator`、`build_metadata` の3表を持つSQLite。構築時に `PRAGMA integrity_check` を実行する
- `authority_map.yaml`: `indicator × use_case` ごとの公式ソース経路とSQLiteロケータ。指標値と生値は複製しない

`--manifest-dir`を指定すると、実行中、成功、失敗の状態、リポジトリrevision、Python／SQLite version、自治体と対象年度、出力path、SHA-256、integrity check、警告を共通run manifestへ記録する。Vaultの次の場所へ保存すれば、`python3 -m lcaios status --vault ...`が最新の成功runから`municipality.db`を自動発見できる。

```bash
python3 -m bootstrap.cli '自治体名' \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'
```

失敗runも上書きせず記録する。e-Stat AppIdの実値はmanifestへ保存せず、エラー文にqueryが含まれる場合もredactする。

保存済みDBの参照先別鮮度は、ネットワークへ接続せず確認できる。

```bash
python3 -m lcaios freshness --vault '/absolute/path/to/vault'
```

国勢調査と総務省財政資料を別々に評価し、原典取得日時からsource registryの次回確認期限を計算する。国勢調査fallbackは取得直後でも`stale`、確認期限を超えた資料は`due`、来歴不足は`unknown`となる。

Tier 1 DBを更新・移動する前は、schemaとintegrityを確認し、SQLite online backup APIでsnapshotを作成できる。

```bash
python3 -m lcaios verify database --file '/path/to/municipality.db'
python3 -m lcaios backup database \
  --file '/path/to/municipality.db' \
  --out-dir '/path/to/backups'
```

対応major schemaは1。`1`は`1.0`として扱う。同じmajorの新しいminorは追加列を無視して読み取るが、major不一致では停止して原典からの再構築を要求する。

HTTP取得は公式URLだけを対象に、ホストごとの `robots.txt` 判定、プロセス全体で1.5秒以上の間隔、正直なUser-Agent、SHA-256付きローカルキャッシュ、`fetched_at` の保存を行う。キャッシュヒット時と `--offline` 時は通信しない。リダイレクト先も取得前に `robots.txt` を判定する。実行レポートとrun manifestの`retrieval`には、cache directory、live request数、hit／miss／refresh数、取得元別内訳、このrunで最新性を再確認したかを記録する。

単体テストは全国基礎自治体registryの小さなCSVを除いて実データを同梱せず、合成した地域API・e-Stat応答と、テスト内でZIP/XMLから生成する最小XLSXを使う。registryの生成元、更新方法、対象範囲は[`municipalities/README.md`](municipalities/README.md)を参照する。

```bash
python3 -m unittest discover -v
```

オンライン取得とオフライン再現を一つの隔離ディレクトリで検証する入口:

```bash
python3 -m lcaios smoke-test bootstrap '自治体名' \
  --prefecture '都道府県名' \
  --work-dir /tmp/lcaios-bootstrap-smoke \
  --max-live-requests 40
```

オンラインDBとオフラインDBの指標を意味比較し、`generated_at`を除くauthority map、
SQLite integrity、オフライン通信0件、AppId非残存、ライブ要求上限を検証する。

## 実装の検証状況

2026-07-23、Python 3.14.6 で次を確認した。

- 太良町を対象に Tier 0 → Tier 1a → Tier 1b → SQLite → authority map をオンラインで完走
- `dashboard.e-stat.go.jp`、`api.e-stat.go.jp`、`www.soumu.go.jp` の `robots.txt` を先に確認し、必要経路が許可されていることを取得器で判定
- 国勢調査3指標は動的探索が成功し、フォールバックを使わず、3指標が同じ `SURVEY_DATE` で揃う調査回を選択
- 市町村別決算状況調の町村概況XLSXから6指標を6桁団体コードと見出しラベルで抽出
- `--cross-check` で同年度の決算カード6指標と照合し、全件一致
- SQLiteは9指標行、`PRAGMA integrity_check = ok`
- 同じキャッシュから `--offline --cross-check` を再実行し、通信0件、SQLiteの9指標行がオンライン実行と一致
- 実装・修正中を含むライブHTTPリクエストは累計21件。30件上限内
- AppIdの実値が `bootstrap/.cache/` と `bootstrap/output/` に残っていないことをバイト列検査
- 合成フィクスチャによる単体テスト7件が成功。曖昧な自治体名が候補を返して終了コード2になる経路と、動的探索失敗時の警告付きフォールバック選択を含む

未検証なのは、全自治体種別・全都道府県・過年度XLSXの網羅、将来のHTML/XLSX見出し変更、動的探索を意図的に失敗させた実API上の2020年表フォールバック、新設・再編自治体、決算カードの全欠測パターンである。見出しの既知別名を使った場合は `header_fallback_used` と採用ラベル・セルを `source_locator` に記録するが、意味を確定できない変更では停止する。
