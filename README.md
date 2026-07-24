# local-councilor-ai-os

議員の時間を、住民さんのためにいちばん活きるところへ。

議員の仕事で本当に時間を使いたいのは、住民さんの話を聞き、現場を見て、問いを立て、判断し、実現に向けて働きかけ、その後を見届けることです。

一方で、資料を探す、過去の答弁をたどる、数字を転記する、出典を確かめるといった作業にも多くの時間がかかります。`local-councilor-ai-os` は、そうした作業をAIとデータベースに手伝わせ、議員本人が住民さんとの対話と判断に集中するための仕事場キットです。

## このOSで変えたいこと

- 必要な資料や過去の議論を探す時間を短くする
- 予算や決算の数字を確かめ、見落としに気づきやすくする
- 住民さんから聞いたことを、公開情報で確かめられる問いにつなげる
- 地域の困りごとを見立て、町・県・国のどこへ働きかければ動くかを探し、副作用まで考えた提案にする
- 質問して終わらず、答弁後の予算化や実施まで追いかける
- 何を根拠に考え、どう判断したかを後からたどれるようにする

すべてを自動化することが目的ではありません。人が考えるための材料を探し、整理し、確かめるところを支えます。

## AIに任せないこと

AIは、調査、整理、照合、検算を手伝います。しかし、政策の良し悪しを決めたり、住民の声を代わりに解釈したり、議員としての判断を引き受けたりはしません。

判断と説明の責任は議員本人に残します。そのために、根拠だけでなく、迷ったこと、確かめたこと、更新したこと、うまくいかなかったことも記録します。

## このリポジトリに入っているもの

地方議員の実務の中で使いながら整えてきた、仕事の進め方と道具を、実データを除いてまとめています。

- **調べる道具** — 議事録、例規、統計、予算・決算資料を探しやすくする
- **確かめる仕組み** — 数字の一致、前年度との比較、出典や更新時点を確認する
- **働きかける先を探す型** — 地域の課題を見立て、町・県・国のどこに効くかたどり、副作用込みの提案にする（ツボ探し）
- **考えたことを残す場所** — 住民の声、調査、質問案、答弁、その後の経過をつなぐ
- **混ぜないためのルール** — 公開情報と個人情報、事実と判断、内部メモと公開説明を分ける

中心に使うのはObsidianです。検索や検算にはSQLiteなどの小さなデータベースとコマンドラインツールを使います。最初から全部を導入する必要はなく、現在地を確認しながら一段ずつ整えられます。

## 自分の自治体で試して、教えてください

このOSは、各自治体で実際に試した結果から育てたいプロジェクトです。コードを書けなくても、
自治体名、どこまで進んだか、どこで止まったかをIssueで知らせるだけで大きな貢献に
なります。公式資料の入口情報だけでも歓迎します。

- [自治体で試した結果を報告する](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=municipality-test.yml)
- [不具合を報告する](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=bug-report.yml)
- [IssueやPull Requestの出し方](CONTRIBUTING.md)

Claude CodeやCodexと一緒に作ったPull Requestも歓迎します。AIが作った変更は、提出者が
差分を確認し、実在する個人情報、秘密値、内部資料を含めないでください。

## 使い込むほど効く「ツボ探し」

地域の困りごとは、町だけで解決できるものもあれば、県の計画や国の制度までたどらないと動かないものもあります。反対に、国や県に使える制度がすでにあり、町の運用や意思決定が変われば動くこともあります。

このOSでは、誰かを悪者にして要望を作るのではなく、次の順で働きかける先を探します。

1. **見立て** — 何が、どこで詰まっているかを、事実と意見を分けて整理する
2. **ツボ** — 町・県・国の制度をたどり、どこへ働きかければ一番効くかを見つける
3. **手当て** — 期待する効果だけでなく、副作用や反対論まで含めた変更案を書く

> 住民さんの困りごとを見立てて、ツボを見つけて、副作用まで見た手当てを書く。

導入した人は、コードを追加しなくてもテンプレートから始められます。

- [ツボ探しのワークフロー](workflows/policy-tsubo.md)
- [見立てテンプレート](templates/policy-issue.md)
- [手当てテンプレート](templates/policy-pr.md)
- [見立てデータ契約](data-contracts/policy-issue.schema.md)
- [手当てデータ契約](data-contracts/policy-pr.schema.md)

一般質問、委員会提言、町への事業提案、県への要請、意見書、省庁照会などへ形を変えられますが、自動送信はしません。働きかける相手、内容、提出、公開は議員本人が決めます。

## Obsidian は必須です

`local-councilor-ai-os` は、Obsidian Vault を判断ノートの正本として使う前提です。Obsidian は単なる保存先ではなく、MOC、wikilink、backlink、frontmatter、lifecycle、検算記録、公開前レビュー、答弁後追跡を接続する判断層です。

Obsidian がなければ、このOSは機能しません。各CLIを単体のデータツールとして実行することはできますが、それは `local-councilor-ai-os` の一部部品を使っているだけであり、本リポジトリが想定する運用体験ではありません。Obsidian以外のノートアプリへの互換レイヤーは提供しません。

## 標準の導入導線

初見の利用者は、次の順に読み取り専用で進めると迷いにくい。

```bash
# 1. 現在地と次の一手を1コマンドで確認
python3 -m lcaios doctor --vault '/absolute/path/to/vault'

# 2. 環境診断と安全なscaffold
python3 -m onboarding diagnose --vault '/absolute/path/to/vault'

# 3. 自治体データ基盤の構築
python3 -m bootstrap.cli '<自治体名>' \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'

# 4. 導入・profile・データ・鮮度の再確認
python3 -m lcaios status --vault '/absolute/path/to/vault'

# 5. オンライン取得とキャッシュ再現を一度に検証
python3 -m lcaios smoke-test bootstrap '<自治体名>' \
  --prefecture '<都道府県名>'
```

`doctor`は診断とreadinessを束ね、次に実行すべき1コマンドだけを示す。基盤が未整備なら`claude-obsidian-setup`へのハンドオフを終了コード3で返す。部品だけを使う利用者には、OS全体の導入完了とは表示しない。

## 共通化するもの、委ねるもの

このリポジトリは、利用者ごとの自治体事情、政治判断、資料の癖まで吸収する完成アプリではありません。共通化するのは、地方議員がAIを使うときに外すと危ない運用の芯だけです。

共通化するものは次です。

- 判断責任を議員本人に残す原則
- 原典、再構築可能なSQLite/FTS5検索層、Obsidian判断ノートを分ける二層構造
- 数値を `value / as_of / definition / source` の4点セットで扱う契約
- 公式公開情報と内部情報を混ぜない安全境界
- `authority_map.yaml` による指標と用途ごとの出典ルーティング
- 議事録、例規、統計、決算などを小さなコンテキストパックへ切り出す形式
- 公開前、質問前、決算DB利用前の検算ゲート
- PDF、OCR、AI抽出、年度比較で起きやすい失敗パターン

利用者に委ねるものは次です。

- どの政策分野、案件、質問を優先するか
- 自治体ごとのPDF、CMS、例規サイト、議会運用への個別対応
- 住民の声や内部資料をどこまで扱うかのローカルな安全判断
- Obsidian Vaultの細部、棚の名前、MOCの粒度
- どのAI、OCR、表抽出ツールを使って原資料を読むか
- 抽出結果、分析候補、質問候補を採用するかどうかの最終判断
- 未対応ベンダーの議事録取込を、契約と参照実装に沿って自分のAIと書くこと

したがって、たとえば決算PDFから数字を抜き取る処理は、自治体や資料ごとのAI・人手・OCRに委ねます。このリポジトリが提供するのは、その結果をSQLiteへ格納するための入力契約、DB構造、原典位置、差額ゼロ検算、公開・非公開境界です。CSVは正本ではなく、SQLiteへ投入するための中間入力形式です。

## これは何ではないか

- 初心者向けプロンプト集ではありません。
- AIに政策判断や政治的責任を移す仕組みではありません。
- 実在する自治体のデータ、住民情報、議員個人の運用環境を配布するリポジトリではありません。
- 非公開情報を匿名化だけで公開情報へ転用する仕組みではありません。
- 取得したPDFやAPI応答を、そのままAIへ大量投入する仕組みではありません。
- 一度つくれば更新も検証も不要になるデータ基盤ではありません。

## ブートストラップの体験

目標とする入口は単純です。

> 自治体名を伝える。エージェントが全国標準の公式公開データを探し、その自治体用のSQLiteデータベースと `authority_map.yaml` を組み立てる。

処理は、自治体名から5桁標準地域コードを解決するところから始まります。続いて国勢調査指標と総務省の財政資料を取得し、値の来歴を保ったままSQLiteへ格納します。`authority_map.yaml` は値を複製せず、「どの指標を、どの用途で、どの公式資料とDB位置から読むか」を示します。

取得した原典は保存対象であり、SQLite／FTS5はいつでも原典から作り直せる検索層です。AIへ渡すのは検索結果から組み立てた小さなコンテキストパックだけです。最終的な解釈、論点、迷い、判断はObsidianノートを正本として残します。

## ブートストラップの段階

| Tier | 対象 | 自動化の目標 | 現在の限界 |
|---|---|---|---|
| 0 | 自治体コード | 同梱した全国基礎自治体registryで現行コードと公式ホームページ入口をオフライン解決し、未収録時だけe-Stat APIへfallbackする | 同名自治体は都道府県ヒントまたは人の選択が必要。スナップショット更新時は公式三経路の再照合が必要 |
| 1 | 全国共通データ | e-Statの国勢調査指標と総務省の財政資料を取得し、出典付きでDB化する | 表題、分類、XLSX構造は将来変更され得るため、発見と意味照合が必要 |
| 2 | 議事録・例規 | ベンダーを検出し、CMS／例規サイト別アダプターへ接続する | 全国一律の公開仕様ではなく、サイトごとの実装と保守が必要 |
| 3 | 予算・決算資料 | PDF等を人の確認付きで取り込み、総額照合を通過したデータだけを利用する | 帳票構造、単位、会計区分が自治体・年度で異なる。完全自動化を前提にしない |

Tier 3では、抽出に成功したことと、数値が正しいことを分けます。ページ内小計、款項目、歳入歳出総額などの照合ゲートを通らないデータは、対外利用可能な状態にしません。

## 基盤リポジトリとの関係

このリポジトリは、[`claude-obsidian-setup`](https://github.com/i8ei/claude-obsidian-setup) の発展編・実務編です。先に基盤リポジトリで、ObsidianとAIエージェントが安全に協働する環境を整えてください。

- `claude-obsidian-setup`: 汎用的な環境構築と基本規約
- `local-councilor-ai-os`: 地方議会実務へ適用するデータ契約、運用原則、ワークフロー、モジュール

本リポジトリの `setup.md` は、基盤側の規約を前提に、追加構成を段階式に案内します。

既存のObsidian・Claude Code・Codex環境へ導入する場合は、先に読み取り専用診断を実行できます。

```sh
python3 -m onboarding diagnose \
  --vault '/absolute/path/to/vault'
```

診断はClaude Code／Codexの利用可否も確認する。片方だけなら自動選択し、両方なら今回使うものを一度だけ選び、`--agent claude`または`--agent codex`で再診断する。以後は計画、計画ハッシュを確認したscaffold、検証の順で進む。詳細は[`onboarding/README.md`](onboarding/README.md)と[`setup.md`](setup.md)の段階0を参照してください。

すでにObsidianを運用しているVaultでは、診断が既存の一般質問、予算、決算、広報、住民の声、証拠台帳、template等を候補として検出し、`layout: preserve`を提案する。既存ノートやフォルダを移動・改名せず、確認した役割対応を`.local-councilor-ai-os/vault-map.yaml`へ保存する。新規Vaultで標準8棚を作る`layout: scaffold`とは分離されている。

## 現在の状態

現在は **v0.1系** です。最新リリースは v0.1.8 で、`main` には次のOS制御層（次期リリース候補）を追加済みです。公開済み・実装済みの範囲は次のとおりです。

- 運用設計の中核 [`way-of-working/`](way-of-working/README.md) 11章と、実務手順 `workflows/` 8本
- 安全原則、データ契約（証拠、来歴、コンテキストパック、権威マップ）、実務テンプレート
- ツボ探しのワークフローと、見立て・手当てのテンプレートおよびデータ契約（地域課題を、働きかける先と副作用込みの制度提案へ変換する型。コード追加なしで利用可）
- 自治体名からのブートストラップCLI（Tier 0〜1、実装・ライブ検証済み）
- 議事録DB（ベンダー検出、kaigiroku.netアダプター、静的HTML/PDFアダプター、検索、コンテキストパック生成）
- 予算審査モジュールのSQLiteスキーマ、入力契約、取込用CSV、歳入歳出一致・前年度比較・補正前後の検算、分析候補生成
- 決算審査モジュールのSQLiteスキーマ、入力契約、取込用CSV、差額ゼロ検算、分析候補生成
- 例規DBの静的HTML/TXT取込、検索、コンテキストパック生成
- 複数のブートストラップDBを束ねるベンチマークDBと指標比較CLI
- ベンチマークDBの比較条件プリセット。同一年度、同一定義で安全に比較できる指標の組み合わせを定義済みにした
- 例規のベンダーアダプター。静的汎用型に加えて、g-reiki系テナントの50音索引を辿るアダプター（実在1自治体でライブ検証済み）
- 議事録の静的設定プリセット集。よくある公開レイアウト4種の設定例と確認手順を同梱
- Obsidian・AI環境の読み取り専用診断、計画ハッシュ確認、安全なVault scaffold、manifest検証
- 予算・決算索引の文書未取得診断と、e-Stat・Jグランツを含む参照先レジストリ
- 同梱自治体registryの公式ホームURLから議事録・例規・予算・決算の入口を少量HTMLだけで分類する都道府県一括preflight
- 議事録の本文・PDF・DB未取得dry-runと、検索式から独立した問いを持つcontext pack
- 複数AI協働の設計と、段階式セットアップ手順
- OS制御層 `lcaios/`（`main`追加分）。導入・profile・データ・鮮度を横断する読み取り専用の状態確認、参照先別の鮮度判定、公開前output安全検査、SQLiteのschema互換検証・非上書きbackup・SHA-256確認付き復旧、生成物一覧、次の一手を示す`doctor`
- bootstrap、議事録、例規、比較、予算、決算の共通run manifestとデータ契約（run manifest、instance、鮮度、情報区分、schema互換）、外部コンテンツをデータとして扱うprompt injection境界

## 統一状態確認

既存Vaultのonboarding manifestとTier 1の`municipality.db`を変更せずに読み、導入、profile、artifact完全性、SQLite integrity、指標の検証状態を一つのJSONまたはMarkdownへまとめられます。

```bash
python3 -m lcaios status \
  --vault '/absolute/path/to/vault' \
  --bootstrap-db '/absolute/path/to/municipality.db'
```

bootstrap実行時にrun manifestをVaultへ保存すると、以後は`--bootstrap-db`を省略できます。

```bash
python3 -m bootstrap.cli '自治体名' \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'

python3 -m lcaios status --vault '/absolute/path/to/vault'
```

run manifestを使わない場合は、Vault内の`.local-councilor-ai-os/instance.json`へDB位置を記録できます。通常の状態表示は未完了項目があっても終了コード0で報告し、CIや公開前ゲートでは`--require tier1_data_ready`等を指定すると、未達時に終了コード2を返します。状態確認は読み取り専用で、manifestやDBを作成・修正しません。

本人確認済みprofileを状態表示へ接続する場合は、profile本文をVault内の任意の棚へ
保存してから確認コマンドを実行する。

```bash
python3 -m lcaios profile confirm \
  --vault '/absolute/path/to/vault' \
  --profile '/absolute/path/to/vault/任意の棚/councilor-profile.yaml' \
  --council-adapter '/absolute/path/to/vault/任意の棚/council-adapter.md' \
  --confirm-human-reviewed
```

このコマンドは本文を変更・複製せず、pathとSHA-256だけをappend-only manifestへ
記録する。確認後に内容が変われば`profile_ready`を`invalid`へ戻す。`doctor`は
onboardingで選んだClaude CodeまたはCodexをmanifestから再利用するため、両方が
インストール済みでも選択画面へ戻り続けない。

各モジュールの取込・検算CLIにも`--manifest-dir`を指定できます。保存先は
`<vault>/.local-councilor-ai-os/runs/<module>`とし、`<module>`は
`minutes`、`regulations`、`benchmark`、`budget`、`settlement`のいずれかです。
`status`と`doctor`はartifactのSHA-256、SQLite integrity、モジュール固有の必須checkを
確認します。CIで個別モジュールを必須にする場合は、次のように指定します。

```bash
python3 -m lcaios status \
  --vault '/absolute/path/to/vault' \
  --require 'module_ready:regulations'
```

参照先別の鮮度だけを確認する場合:

```bash
python3 -m lcaios freshness --vault '/absolute/path/to/vault'
```

鮮度は`fresh / due / stale / unknown`で表示します。DBを今日再構築しただけでは`fresh`にせず、各行に保存された原典取得日時、対象期、source registryの再確認間隔を使います。offline rebuildは最新公表期を再確認したものとして扱いません。

公開予定稿の機械的な漏えい走査:

```bash
python3 -m lcaios verify output --file '/absolute/path/to/draft.md'
```

内部wikilink、内部・絶対path、未検証印、秘密値候補、隠しコメント、不可視制御文字、内部区分標識を検出し、errorがあれば終了コード2を返します。自動削除・修正・公開は行いません。検出0件でも、個人の再識別可能性、数値・引用の正しさ、公開可否は保証しないため、人による公開前レビューが必要です。

Tier 1 SQLiteのschema互換・backup・復旧:

```bash
# 読み取り専用検証
python3 -m lcaios verify database --file '/path/to/municipality.db'

# 既存ファイルを上書きしないSQLite snapshot
python3 -m lcaios backup database \
  --file '/path/to/municipality.db' \
  --out-dir '/path/to/backups'

# 出力されたbackup SHA-256を画面で確認して復旧
python3 -m lcaios restore database \
  --backup '/path/to/backup.db' \
  --target '/path/to/municipality.db' \
  --accept-sha256 '<backup_sha256>'
```

backup前と復旧前後にSQLite integrityとschemaを検証します。既存targetは削除せず`.previous-*`へ退避し、確認済みSHA-256が一致しなければ復旧しません。manifestが宣言した生成物は、削除せず一覧できます。

```bash
python3 -m lcaios generated-files --vault '/absolute/path/to/vault'
```

次の正式リリースはv0.2の予定です。OS制御層のコードと自動テストは`main`へ追加済みで、残る完了条件は次です。

- 予算・決算PDF抽出そのものではなく、SQLite入力契約、公開・非公開境界、失敗パターン、分析候補生成の実戦検証と閾値設計を深める
- 9月決算審査と次回予算審議で、検算閾値、導入負荷、現場へ戻せた時間を評価する

議事録のベンダーアダプターが未対応の場合でも、行き止まりにはなりません。利用者のAIエージェントが、本リポジトリの契約（正規化スキーマ、礼節基盤、参照実装2本）に沿って自分の議会向けの取込を書けます。手順は [`modules/minutes_db/adapter_guidance.md`](modules/minutes_db/adapter_guidance.md) にあります。汎用化できた実装は、実戦検証を経て本体へ取り込みます。

## リポジトリ案内

| パス | 内容 |
|---|---|
| `principles/` | 判断責任、安全境界、証拠と検証の憲章 |
| `lcaios/` | 導入状態、artifact完全性、Tier 1 DBを横断する読み取り専用status CLI |
| `onboarding/` | Obsidian・AI環境の診断、権限プレビュー、安全なVault scaffold |
| [`way-of-working/`](way-of-working/README.md) | 正本、MOC、権威ルーター、問い化、ツボ探しなどの運用設計 |
| `bootstrap/` | 自治体名から全国共通データ基盤を立ち上げる設計 |
| `workflows/` | 案件開始から答弁後の実装追跡までの手順 |
| `profiles/` | 議員の役割や議会固有差分を表す設定 |
| `data-contracts/` | 出典、証拠、権威マップ、来歴の契約 |
| `modules/` | 議事録、予算審査、決算審査、例規、比較分析の各モジュール |
| `templates/` | Obsidianで使う実務ノートのひな型 |
| `collaboration/` | 複数AIを使う際の委任と合議の設計 |

## 安全原則

1. 判断と説明責任は議員本人に残します。
2. AIは調査、構造化、照合、検算を補助します。
3. 対外的な根拠には、公式に公表された公開情報と公的統計だけを使います。
4. 数値は「値・基準時点・定義・出典」の4点セットで扱います。
5. 原典、再構築可能な検索DB、判断ノートを分離します。
6. 住民の声や現場観察は内部原典として隔離し、匿名化ではなく、公開情報で検証可能な問いへ変換します。
7. 公開成果物から内部ノートへリンクしません。
8. 外部送信、公開、個人情報を含む処理には、人による確認を設けます。
9. 不一致、欠測、定義不明は埋めずに記録し、検証状態を下げます。
10. 質問と答弁だけで閉じず、予算化、事業化、実施、検証まで追跡します。

詳しくは [`principles/`](principles/) を参照してください。

## テスト

全モジュールのテストは一つの入口から実行します。

```bash
./run_tests.sh
```

`lcaios/`、`bootstrap/`、`onboarding/`、`modules/` はすべてインポート可能なパッケージなので、ルートの `python3 -m unittest discover` 一回で全テストを検出します。`run_tests.sh` はこの全体検出に加えて予算・決算の検算ゲートまでをまとめて実行し、いずれかが失敗すると終了コード1を返します。テスト実行時は `PYTHONWARNINGS=error::ResourceWarning` を設定し、DB接続リークを失敗として検出します。

lint と型チェックは次で実行します。

```bash
ruff check .
mypy
```

通常のpush／PRでは、外部通信しない合成fixtureテストをPython 3.11と3.14で実行します。
実APIの契約確認は、週次または手動の`Live bootstrap contract` workflowへ分離しています。
ローカルで同じ検証を行うには`ESTAT_APPID`を環境変数へ設定し、次を実行します。

```bash
python3 -m lcaios smoke-test bootstrap '伊万里市' \
  --prefecture '佐賀県' \
  --work-dir /tmp/lcaios-imari-smoke \
  --max-live-requests 40
```

このsmoke testは、隔離した共有キャッシュでオンライン構築とオフライン再構築を続けて行い、
指標、authority map、SQLite integrity、通信0件、AppId非残存、HTTP要求上限を検証します。

## ライセンス

コードと文書は、特記がない限り [MIT License](LICENSE) で提供します。
