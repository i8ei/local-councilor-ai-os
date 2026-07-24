# セットアップ

## 前提

このプロファイルは、Obsidian Vault、AIエージェント、保存規約が [`claude-obsidian-setup`](https://github.com/i8ei/claude-obsidian-setup) で構築済みであることを前提とする。Obsidian は単なる保存先ではなく、MOC、wikilink、backlink、frontmatter、lifecycle、検算記録、公開前レビュー、答弁後追跡を接続する判断層である。Obsidian がない環境では、このセットアップを開始しない。SQLiteや各CLIは単体のデータツールとして実行できるが、それは本OSの一部部品であり、運用体験の完了とは扱わない。基盤を置き換えず、地方議会実務の棚、プロファイル、ワークフロー、公開情報の検索層を上に追加する。

セットアップはAIエージェントとの対話で一段階ずつ進める。各段階は「確認、実行、検証、停止」で閉じ、次へ自動で進まない。開始前に作成先、既存物、外部通信、生成物、巻き戻し対象と理由を示し、明示承認を得る。実行時は作成記録を残す。同名ファイルは自動上書きせず、差分を示して承認された項目だけを統合する。不合格なら未完了として停止する。

## 段階0 Obsidian・AI環境・権限

### 確認

最初に、利用者と一緒にObsidianを起動し、対象Vaultの名前と絶対パスを確認する。新規Vaultの作成、Obsidian CLIの有効化、Claude Code／Codexの基本設定、グローバルなSkillや権限設定は基盤側の`claude-obsidian-setup`が担当する。本リポジトリはそれらを自動で変更しない。

既存Vaultの現在地、Tier 1 DB、鮮度、次に実行すべき1コマンドまでまとめて確認する場合は、読み取り専用の統一入口を使う。

```sh
python3 -m lcaios doctor \
  --vault '/absolute/path/to/vault'
```

`doctor`は基盤不足を終了コード3、AIクライアント選択や未完了項目を終了コード2、導入・データ・profileが整っている状態を終了コード0で返す。自動scaffold、設定変更、データ取得は行わない。詳細な環境診断とscaffoldは引き続き`onboarding`が正本である。

既存環境へ統合する場合は、次を読み取り専用で診断する。

```sh
python3 -m onboarding diagnose \
  --vault '/absolute/path/to/vault'
```

AIクライアントを指定しない場合は、Claude CodeとCodexのCLIを読み取り専用で検出する。片方だけなら自動選択し、両方なら今回使うものを一度だけ選ぶよう停止する。選択後は`--agent claude`または`--agent codex`で再診断する。Codexでは`AGENTS.override.md`があればそれを、なければ`AGENTS.md`を有効な指示として扱う。Claude Codeでは`CLAUDE.md`を確認する。不足時は必要ファイル、クライアント固有の権限確認、基盤手順、再診断コマンドを表示し、自動作成しない。Obsidian CLIは、Vault一覧、対象Vaultの絶対パス、対象Vaultを明示した検索疎通の三つが一致して初めて利用可能と判定する。

権限は一括して「問題なし」と推測せず、少なくとも次を分けて確認する。

| 権限・操作 | 導入前の扱い |
|---|---|
| 対象Vaultの読み書き | ホストOS上の可否と、AIクライアントのworkspace範囲を分ける |
| リポジトリ内コマンドの実行 | 使用するAIクライアントの承認方式を確認する |
| Obsidianアプリの起動・CLI操作 | OSやクライアントが都度確認する可能性を伝える |
| 公式サイト・APIへの外部通信 | 選択した機能ごとに通信先を計画へ出す |
| グローバル設定・Skill・パッケージ変更 | 本リポジトリでは自動実行しない |
| 既存ファイルの上書き・統合・削除 | scaffoldの対象外。別の明示確認を要する |

承認ダイアログの回数はAIクライアントと利用者設定に依存するため、CLIは正確な回数を断定しない。代わりに、通常の新規作成、別確認が必要な操作、予定される外部通信先を計画に分けて表示する。

利用形態は次から選ぶ。

| モード | 用途 |
|---|---|
| `integrate` | ObsidianとClaude Code／Codexが既に動く。不足するOS構成だけ追加する |
| `full` | 基盤未整備。`claude-obsidian-setup`へ戻り、完了後に`integrate`で再診断する |
| `components` | データCLIだけを選んで使う。OS全体の導入完了とは表示しない |
| `diagnose` | 何も作らず、現状と不足だけ確認する |

### 実行

`integrate`の場合は、作成予定と権限プレビューを先に出す。

```sh
python3 -m onboarding plan \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --layout '<diagnoseが示したscaffoldまたはpreserve>'
```

表示された対象、競合、外部通信、`plan_sha256`を利用者が確認した後、同じ引数とSHA-256でscaffoldを作る。

```sh
python3 -m onboarding scaffold \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --layout '<diagnoseが示したscaffoldまたはpreserve>' \
  --accept-plan-sha256 '<plan_sha256>'
```

実行直前に診断と計画を再作成し、SHA-256が一致しなければ停止する。異なる内容の既存ファイル、symlink、Vault外の対象は上書きや追従をせず停止する。作成結果はVault内の`.local-councilor-ai-os/runs/`へ記録する。

既存Vaultで`recommended_layout: preserve`になった場合は、固定棚を追加しない。一意に検出した既存pathと役割の対応をplanで確認し、`.local-councilor-ai-os/vault-map.yaml`へ保存する。候補が誤っている場合は制約付き`vault-map.yaml`を別に作って`--vault-map`でplanとscaffoldへ渡す。既存ノート、MOC、AI指示、`instance.json`は自動編集しない。

### 検証

返されたmanifestを指定して検証する。

```sh
python3 -m onboarding verify \
  --manifest '/absolute/path/to/vault/.local-councilor-ai-os/runs/<run-id>.json'
```

`scaffold` layoutでは8つの棚、各MOCの`description`、OS専用MOCからの接続、テンプレートを確認する。`preserve` layoutでは役割pathの存在、Vault内解決、symlink不使用を確認する。共通してartifactのSHA-256とObsidian CLI疎通を確認する。基盤が正しくても、本人情報と自治体固有の議会運用が未確認なら`profile_status: incomplete`のまま段階1へ渡す。

### 中止・巻き戻し

基盤不足なら何も作らず`claude-obsidian-setup`へハンドオフする。scaffold後に戻す必要がある場合はmanifestの`action: create`だけを人が確認して退避候補にする。`action: reuse`、既存の入口MOC、AI指示ファイル、基盤側の設定は巻き戻し対象にしない。

## 段階1 議員業務プロファイル

### 確認

AIエージェントは、次を一項目ずつ質問する。

1. 議会での役割と任期
2. 所属する常任委員会、特別委員会、その他の会議
3. 継続して追う政策分野と、扱わない分野
4. 一般質問、委員会質問、資料要求、公開説明、監視などの成果物
5. 公開、内部、要配慮を分ける境界と、外部送信時の承認者

質問の理由も添える。回答は期限と権限、検索、ワークフロー選択、保存境界の設定に使う。個人情報は基盤側の内部領域だけで扱う。

### 実行

`scaffold` layoutでは、既存のVault構造を確認し、次の棚を不足分だけ作る。`preserve` layoutでは棚を作らず、確認済み`vault-map.yaml`の既存pathを利用する。

| 棚 | `description` に書く役割 |
|---|---|
| 会期ごとの作業場 | 会期中の案件、期限、提出物の現在地を束ねる |
| 一般質問 | 質問の設計、通告前検算、答弁後追跡をつなぐ |
| 予算決算 | 予算化、執行、決算、総額照合を追う |
| 条例制度 | 法令、例規、制度要件、改廃履歴を確認する |
| 行政視察 | 視察目的、確認事項、公開原典、帰着後の判断を残す |
| 広報 | 読者別の公開説明と公開前レビューを管理する |
| 住民の声 | 内部原典を隔離し、公開可能な問いへの変換を追う |
| 証拠台帳 | 一主張一項目で原典位置と検証状態を管理する |

各棚には一行の`description`を持つMOCを置き、基盤側の入口MOCから接続する。入口がなければ現在地を再発見できないからである。

`templates/`のひな型はVaultのノートテンプレート領域へ複製する。同名があれば置換せず、差分を示す。聞き取り結果から`profiles/councilor-profile.yaml`と`profiles/council-adapter.md`を記入する。前者には役割と作業条件、後者には公開資料で確認できる議会日程、通告様式、会議録経路を置く。個人の判断と公開された議会運用を混ぜない。

本人確認後、両ファイルをVault内の任意の棚へ保存し、次を実行する。

```bash
python3 -m lcaios profile confirm \
  --vault '/absolute/path/to/vault' \
  --profile '/absolute/path/to/vault/任意の棚/councilor-profile.yaml' \
  --council-adapter '/absolute/path/to/vault/任意の棚/council-adapter.md' \
  --confirm-human-reviewed
```

本文はmanifestへ複製されず、pathとhashだけが記録される。AIによる推定、未記入
placeholder、Vault外path、symlinkでは`profile_ready`にしない。

### 検証

八つの棚、各MOCの`description`、基盤MOCからの接続、テンプレートの件数、二つのプロファイルの未記入欄を確認する。既存物を置換していないこと、公開側から`住民の声`へリンクがないことも確認する。

### 中止・巻き戻し

作成記録にある新規棚、MOC、テンプレート複製だけを退避する。プロファイルは差分記録に沿って承認済みの追加だけを戻す。基盤側には触れず、段階2は別に同意を得る。

## 段階2 実務ワークフロー

### 確認

AIエージェントは、日常業務のどの合図で[ワークフロー](workflows/)を提案するかを示し、利用者に選ばせる。案件の着想では01、相談や現場観察では02、検証可能な問いができたら03、質問の根拠がそろったら04、公開説明を作るときは05、提出または公開の直前は06、答弁後と確認期限の到来時は07を提案する。制度・事業・運用のどこへ働きかければ動くかを探すときは、[ツボ探し](workflows/policy-tsubo.md)を提案する。提案は開始許可ではない。

### 実行

各ワークフローの発動条件、入力、完了条件を常設手順へ接続する。[運用憲章](principles/charter.md)、[根拠と検証](principles/evidence-and-verification.md)、[安全境界](principles/safety-boundary.md)は、議会案件の前に読む常設規則とする。判断責任、証拠条件、公開境界を手順より先に固定するためである。

### 検証

各ワークフローの合図を例示入力で試し、正しい手順を提案し、許可なく開始しないことを確認する。数値の例では4点セット、内部情報の例では逆リンク禁止、外部出力の例では本人承認を確認する。

### 中止・巻き戻し

常設手順へ追加した規則とトリガーだけを作成記録に従って外す。Vaultの案件ノートや段階1の棚は残す。ここで停止し、段階3は別に同意を得る。

## 段階3 公開情報基盤

この段階は任意である。全国共通の統計と財政指標が必要な場合だけ実行する。

### 確認

対象自治体、都道府県ヒント、新規の出力先、外部通信を確認する。オンライン実行には`ESTAT_APPID`が必要である。[e-Stat APIの利用ガイド](https://www.e-stat.go.jp/api/api-info/api-guide)に沿って、次の順に準備する。

1. e-Statへユーザー登録してログインする
2. マイページから「ユーザ情報変更」→「登録内容変更」を開く
3. 「利用する機能」で「API機能」にチェックを入れ、画面下部の「変更」で確定する
4. マイページの「API機能（アプリケーションID発行）」からアプリケーションIDを発行する

ユーザー登録だけではAPI機能が有効になっていない場合がある。アプリケーションID発行の入口が表示されない場合や、APIが認証エラーになる場合は、先に手順2〜3を確認する。地図機能を使わない場合、「地図で見る統計（jSTAT MAP）」の有効化は本CLIの実行条件ではない。

アプリケーションIDは環境変数として実行時だけ渡し、ノート、実行報告、キャッシュ、データベースへ書かない。ユーザーID、メールアドレス、アプリケーションIDの画面をセットアップ記録へ貼らない。

参照可能なAPIと保存方針はsource registryで確認できる。

```sh
python3 -m bootstrap.cli.sources list
python3 -m bootstrap.cli.sources show jgrants-public-api
```

e-StatとJグランツは都度参照＋取得時刻・hashつきcacheを既定とする。一度だけの確認にSQLiteは必須ではない。反復検索、複数原典の結合、年度比較、差額検算が必要になった時だけ正規化SQLiteを作り、対外利用したレコードはsnapshotを固定する。Jグランツは補助金候補の発見に使えるが、自治体が実際に採択・活用した事実や、公募要領・交付要綱の本文確認を代替しない。

### 実行

[自治体ブートストラップ](bootstrap/README.md)のCLIを新規出力先へ実行する。自治体名の候補が複数なら都道府県ヒントを求めて停止する。成功すると自治体DBと`authority_map.yaml`ができる。後者は値の複製ではなく、指標と利用目的から公式原典とDB位置を選ぶ裁定表である。

```sh
python3 -m bootstrap.cli '自治体名' \
  --prefecture '都道府県名' \
  --out-dir bootstrap/output/自治体名 \
  --cross-check \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'
```

初回オンライン実行の後、同じ入力でオフライン再構築を行い、キャッシュだけで再現できることを確認する。

```sh
python3 -m bootstrap.cli '自治体名' \
  --prefecture '都道府県名' \
  --out-dir bootstrap/output/自治体名-offline \
  --offline \
  --cross-check \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'
```

複数自治体の比較が必要な場合は、各自治体の`municipality.db`を作成した後、比較DBを別名で生成する。比較DBは新たな公式取得を行わず、検証済みのブートストラップ出力だけを束ねる。

```sh
python3 -m modules.benchmark.build_from_bootstrap bootstrap/output \
  --db benchmark.db
python3 -m modules.benchmark.compare zaiseiryoku_shisuu \
  --db benchmark.db \
  --limit 20
```

取得は公式URLに限り、`robots.txt`を守り、低い頻度で行い、取得物をキャッシュする。禁止経路を推測URLで迂回しない。公開条件と相手側の負荷を尊重するためである。

### 検証

最初にe-Stat APIの認証が成功することを確認する。`ESTAT_APPID`が設定済みでも認証エラーになる場合は、「API機能」の有効化と変更確定、発行したアプリケーションIDの値を再確認する。

その後、自治体コードの一意性、DBの整合性、指標の値、時点、定義、出典、`authority_map.yaml`に値が複製されていないことを確認する。同じキャッシュからオフライン再構築し、行数と主要値が一致することも確認する。不一致は隔離し、最新値として使わない。

統一状態確認と参照先別鮮度を確認する。

```sh
python3 -m lcaios status \
  --vault '/absolute/path/to/vault' \
  --require tier1_data_ready

python3 -m lcaios freshness \
  --vault '/absolute/path/to/vault'
```

`fresh / due / stale / unknown`は取得時刻と対象期を分けて判定する。オフライン再構築した日を最新確認日として扱わない。DB更新前にはschemaとintegrityを確認し、必要に応じて非上書きbackupを作る。

```sh
python3 -m lcaios verify database \
  --file bootstrap/output/自治体名/municipality.db

python3 -m lcaios backup database \
  --file bootstrap/output/自治体名/municipality.db \
  --out-dir '/absolute/path/to/backups'
```

### 中止・巻き戻し

新規出力先とこの段階で作ったキャッシュだけを作成記録から特定し、退避する。`ESTAT_APPID`を実行環境から外す。段階1と段階2の設定は残す。段階4は別に同意を得る。

## 段階4 議事録DB

この段階も任意である。議事録を反復検索する場合だけ実行する。

### 確認

公式の議会ページ、取得対象期間、新規DBの保存先、外部通信、取得上限を確認する。ベンダー対応範囲は一様ではない。検出できても取込未対応の場合があり、静的サイトはリンク条件やPDF条件の設定を手で調整することがある。

### 実行

[議事録データベース](modules/minutes_db/README.md)の順に、まず`detect`で公開方式の証拠を得る。次に検出結果と公式ページを人が照合し、対応アダプターまたは静的設定を選ぶ。最後に`ingest`で原典、来歴、SQLiteと全文索引を作る。`unknown`を推測で既知ベンダーへ割り当てず、取込不能なら未対応として止める。

```sh
python3 -m modules.minutes_db.detect '公式議会ページURL'
python3 -m modules.minutes_db.ingest \
  --adapter static \
  --config minutes-static.json \
  --limit 20 \
  --dry-run
python3 -m modules.minutes_db.ingest \
  --adapter static \
  --config minutes-static.json \
  --db minutes.db \
  --limit 10
python3 -m modules.minutes_db.search '防災' --db minutes.db --k 10
python3 -m modules.minutes_db.context_pack '防災' \
  --question '地域防災計画はいつ見直されたか' \
  --db minutes.db \
  --k 5 \
  --char-budget 6000
```

例規を反復検索する場合は、公式例規索引を人が確認したうえで、静的例規設定を作る。例規DBも検索層であり、外部引用前には公式画面で施行時点、条番号、前後条文を確認する。

```sh
python3 -m modules.regulations.ingest \
  --config regulations-static.json \
  --db regulations.db \
  --limit 20
python3 -m modules.regulations.search '個人情報' --db regulations.db --k 10
python3 -m modules.regulations.context_pack '個人情報' \
  --question '個人情報の取扱いは何条にあるか' \
  --db regulations.db \
  --k 5 \
  --char-budget 6000
```

### 検証

別の検索語を二つ使い、会議名、日付、話者、原典URL、原典位置、取得時刻が返ることを確認する。原典の一箇所と検索結果を画面で照合し、コンテキストパックが文字数上限を守ることも確認する。ライブ未検証のアダプターは、その状態を外さない。

### 中止・巻き戻し

この段階で新規作成したDB、キャッシュ、静的設定だけを作成記録から特定し、退避する。取得済み原典を残すかも人が決める。他段階の検索層とVaultノートは変更しない。

## 段階5 予算レビュー

この段階は任意である。予算書、補正予算書、予算説明資料などの数値を検算可能なSQLiteへ入れたい場合だけ実行する。PDFから数字を読む処理そのものは、自治体、年度、会計、公開範囲、PDF品質によって異なるため、本リポジトリは汎用抽出器を提供しない。ここで固定するのは、抽出後のSQLite入力契約、SQLite格納、歳入歳出一致、階層合計、前年度比較、補正前後の検算である。

### 確認

最初に自治体公式サイトの索引だけを診断し、予算・決算の候補を列挙する。この時点では文書を取得しない。

```sh
python3 -m bootstrap.cli.local_documents diagnose \
  --index-url '自治体公式の予算・決算索引URL'
```

候補を見て、今は見送る、1〜3件だけPDF品質と抽出経路を試す、年度・会計・種類を限定する、必要範囲を全件取り込む、のいずれかを利用者が選ぶ。その後、対象資料が当初予算、補正予算、現計予算のどれかを確認する。対象年度、会計名、議案番号、ページ範囲、原表単位、PDFページと印刷ページの対応、外部AIへ送ってよい資料かを確認する。

### 実行

[予算レビュー](modules/budget_review/README.md)と[SQLite入力契約](modules/budget_review/sqlite_input_contract.md)、[失敗パターン](modules/budget_review/failure_patterns.md)を読む。取込用CSVの雛形を生成し、個別AIまたは人がPDFから転記した値を、原典位置つきで埋める。CSVは受け渡し用であり、作業の中心は投入後のSQLiteである。

```sh
python3 -m modules.budget_review.csv_templates > budget.csv
python3 -m modules.budget_review.ingest_csv budget.csv --db budget.db
python3 -m modules.budget_review.verify_totals budget.db
```

検算に通ったDBだけ、確認候補生成へ進める。

```sh
python3 -m modules.budget_review.insights budget.db
```

### 検証

`verify_totals.py` が終了コード0であること、歳入歳出総額、階層合計、前年度比較、補正前後の整合が取れていること、`source_locator` から原表の該当箇所へ戻れることを確認する。`insights.py` の候補は予算審議の入口であり、必要性や妥当性を自動確定しない。

### 中止・巻き戻し

この段階で作った取込用CSV、DB、抽出ログ、内部資料由来の作業物だけを作成記録から特定して退避する。他段階のDBやVaultノートは変更しない。

## 段階6 決算レビュー

この段階は任意である。決算書など自治体固有PDFの数値を検算可能なDBへ入れたい場合だけ実行する。PDFから数字を読む処理そのものは、自治体、年度、会計、公開範囲、PDF品質によって異なるため、本リポジトリは汎用抽出器を提供しない。ここで固定するのは、抽出後のSQLite入力契約、SQLite格納、差額ゼロ検算、公開・非公開境界である。CSVは正本ではなく、SQLiteへ投入するための中間入力形式として扱う。

### 確認

段階5で索引診断をしていない場合は、同じ`bootstrap.cli.local_documents diagnose`で決算候補まで確認し、文書取得の範囲を利用者が選ぶ。対象資料が公式公開資料か、内部資料か、要配慮資料かを先に分類する。対象年度、会計名、ページ範囲、原表単位、PDFページと印刷ページの対応、外部AIへ送ってよい資料かを確認する。非公開資料は公開用DBへ混ぜず、公開成果物では公式公開資料で再検証できる問いへ変換する。

### 実行

まず [SQLite入力契約](modules/settlement_review/sqlite_input_contract.md)、[抽出ガイダンス](modules/settlement_review/extraction_guidance.md)、[失敗パターン](modules/settlement_review/failure_patterns.md)、[公開・非公開境界](modules/settlement_review/public_private_boundary.md)を読む。取込用CSVの雛形を生成し、個別AIまたは人がPDFから転記した値を、原典位置つきで埋める。作業の中心は投入後のSQLiteであり、CSVは受け渡し用である。

```sh
python3 -m modules.settlement_review.csv_templates summary > summary.csv
python3 -m modules.settlement_review.csv_templates revenue > revenue.csv
python3 -m modules.settlement_review.csv_templates expenditure > expenditure.csv

python3 -m modules.settlement_review.ingest_csv summary summary.csv --db settlement.db
python3 -m modules.settlement_review.ingest_csv revenue revenue.csv --db settlement.db
python3 -m modules.settlement_review.ingest_csv expenditure expenditure.csv --db settlement.db
python3 -m modules.settlement_review.verify_totals settlement.db
```

検算に通ったDBだけ、確認候補生成へ進める。

```sh
python3 -m modules.settlement_review.insights settlement.db
```

`insights.py` の出力は質問候補ではなく、人が確認する入口である。原因、妥当性、政策評価は自動確定しない。

### 検証

`verify_totals.py` が終了コード0であること、`source_locator` から原表の該当箇所へ戻れること、単位、会計、年度、ページが一致していることを確認する。`insights.py` の候補は、原典と担当課説明や監査資料など別根拠へ戻してから採用する。

### 中止・巻き戻し

この段階で作った取込用CSV、DB、抽出ログ、内部資料由来の作業物だけを作成記録から特定して退避する。公開情報基盤、議事録DB、Vaultノートは変更しない。非公開資料を誤って公開用DBへ入れた場合は、そのDBを外部利用不可として破棄し、公開資料だけで再構築する。

## 完了報告

各段階後に、作成物、作成先、スキップ、統合差分、検証結果、未確認事項を報告し、解除方法を示す。段階1は棚とテンプレート、段階2は常設規則とトリガー、段階3は自治体DB・裁定表・run manifest・鮮度、段階4は議事録DB・例規DBとキャッシュ、段階5は予算の取込用CSV・SQLite DB・検算結果、段階6は決算の取込用CSV・SQLite DB・検算結果が対象である。作成記録と照合し、基盤側を削除対象にしない。次へ進むかは利用者が決める。

公開・提出前には、固定した対象稿へ読み取り専用の安全検査を実行し、検出項目を人が確認する。

```sh
python3 -m lcaios verify output \
  --file '/absolute/path/to/public-draft.md'
```

検出0件は公開承認を意味しない。数値4点セット、引用、再識別可能性、宛先、版、公開範囲を議員本人が最終確認する。
