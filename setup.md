# セットアップ

## 前提

このプロファイルは、Obsidian Vault、AIエージェント、保存規約が [`claude-obsidian-setup`](https://github.com/i8ei/claude-obsidian-setup) で構築済みであることを前提とする。Obsidian は単なる保存先ではなく、MOC、wikilink、backlink、frontmatter、lifecycle、検算記録、公開前レビュー、答弁後追跡を接続する判断層である。Obsidian がない環境では、このセットアップを開始しない。SQLiteや各CLIは単体のデータツールとして実行できるが、それは本OSの一部部品であり、運用体験の完了とは扱わない。基盤を置き換えず、地方議会実務の棚、プロファイル、ワークフロー、公開情報の検索層を上に追加する。

セットアップはAIエージェントとの対話で一段階ずつ進める。各段階は「確認、実行、検証、停止」で閉じ、次へ自動で進まない。開始前に作成先、既存物、外部通信、生成物、巻き戻し対象と理由を示し、明示承認を得る。実行時は作成記録を残す。同名ファイルは自動上書きせず、差分を示して承認された項目だけを統合する。不合格なら未完了として停止する。

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

既存のVault構造を確認し、次の棚を不足分だけ作る。

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

### 検証

八つの棚、各MOCの`description`、基盤MOCからの接続、テンプレートの件数、二つのプロファイルの未記入欄を確認する。既存物を置換していないこと、公開側から`住民の声`へリンクがないことも確認する。

### 中止・巻き戻し

作成記録にある新規棚、MOC、テンプレート複製だけを退避する。プロファイルは差分記録に沿って承認済みの追加だけを戻す。基盤側には触れず、段階2は別に同意を得る。

## 段階2 実務ワークフロー

### 確認

AIエージェントは、日常業務のどの合図で[ワークフロー](workflows/)を提案するかを示し、利用者に選ばせる。案件の着想では01、相談や現場観察では02、検証可能な問いができたら03、質問の根拠がそろったら04、公開説明を作るときは05、提出または公開の直前は06、答弁後と確認期限の到来時は07を提案する。提案は開始許可ではない。

### 実行

七つの発動条件、入力、完了条件を常設手順へ接続する。[運用憲章](principles/charter.md)、[根拠と検証](principles/evidence-and-verification.md)、[安全境界](principles/safety-boundary.md)は、議会案件の前に読む常設規則とする。判断責任、証拠条件、公開境界を手順より先に固定するためである。

### 検証

七つの合図を例示入力で試し、正しいワークフローを提案し、許可なく開始しないことを確認する。数値の例では4点セット、内部情報の例では逆リンク禁止、外部出力の例では本人承認を確認する。

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

### 実行

[自治体ブートストラップ](bootstrap/README.md)のCLIを新規出力先へ実行する。自治体名の候補が複数なら都道府県ヒントを求めて停止する。成功すると自治体DBと`authority_map.yaml`ができる。後者は値の複製ではなく、指標と利用目的から公式原典とDB位置を選ぶ裁定表である。

```sh
python3 -m bootstrap.cli '自治体名' \
  --prefecture '都道府県名' \
  --out-dir bootstrap/output/自治体名 \
  --cross-check
```

初回オンライン実行の後、同じ入力でオフライン再構築を行い、キャッシュだけで再現できることを確認する。

```sh
python3 -m bootstrap.cli '自治体名' \
  --prefecture '都道府県名' \
  --out-dir bootstrap/output/自治体名-offline \
  --offline \
  --cross-check
```

複数自治体の比較が必要な場合は、各自治体の`municipality.db`を作成した後、比較DBを別名で生成する。比較DBは新たな公式取得を行わず、検証済みのブートストラップ出力だけを束ねる。

```sh
python3 modules/benchmark/build_from_bootstrap.py bootstrap/output \
  --db benchmark.db
python3 modules/benchmark/compare.py zaiseiryoku_shisuu \
  --db benchmark.db \
  --limit 20
```

取得は公式URLに限り、`robots.txt`を守り、低い頻度で行い、取得物をキャッシュする。禁止経路を推測URLで迂回しない。公開条件と相手側の負荷を尊重するためである。

### 検証

最初にe-Stat APIの認証が成功することを確認する。`ESTAT_APPID`が設定済みでも認証エラーになる場合は、「API機能」の有効化と変更確定、発行したアプリケーションIDの値を再確認する。

その後、自治体コードの一意性、DBの整合性、指標の値、時点、定義、出典、`authority_map.yaml`に値が複製されていないことを確認する。同じキャッシュからオフライン再構築し、行数と主要値が一致することも確認する。不一致は隔離し、最新値として使わない。

### 中止・巻き戻し

新規出力先とこの段階で作ったキャッシュだけを作成記録から特定し、退避する。`ESTAT_APPID`を実行環境から外す。段階1と段階2の設定は残す。段階4は別に同意を得る。

## 段階4 議事録DB

この段階も任意である。議事録を反復検索する場合だけ実行する。

### 確認

公式の議会ページ、取得対象期間、新規DBの保存先、外部通信、取得上限を確認する。ベンダー対応範囲は一様ではない。検出できても取込未対応の場合があり、静的サイトはリンク条件やPDF条件の設定を手で調整することがある。

### 実行

[議事録データベース](modules/minutes-db/README.md)の順に、まず`detect`で公開方式の証拠を得る。次に検出結果と公式ページを人が照合し、対応アダプターまたは静的設定を選ぶ。最後に`ingest`で原典、来歴、SQLiteと全文索引を作る。`unknown`を推測で既知ベンダーへ割り当てず、取込不能なら未対応として止める。

```sh
python3 modules/minutes-db/detect.py '公式議会ページURL'
python3 modules/minutes-db/ingest.py \
  --adapter static \
  --config minutes-static.json \
  --db minutes.db \
  --limit 10
python3 modules/minutes-db/search.py '防災' --db minutes.db --k 10
python3 modules/minutes-db/context_pack.py '防災計画の見直し' \
  --db minutes.db \
  --k 5 \
  --char-budget 6000
```

例規を反復検索する場合は、公式例規索引を人が確認したうえで、静的例規設定を作る。例規DBも検索層であり、外部引用前には公式画面で施行時点、条番号、前後条文を確認する。

```sh
python3 modules/regulations/ingest.py \
  --config regulations-static.json \
  --db regulations.db \
  --limit 20
python3 modules/regulations/search.py '個人情報' --db regulations.db --k 10
python3 modules/regulations/context_pack.py '個人情報の取扱い' \
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

対象資料が当初予算、補正予算、現計予算のどれかを確認する。対象年度、会計名、議案番号、ページ範囲、原表単位、PDFページと印刷ページの対応、外部AIへ送ってよい資料かを確認する。

### 実行

[予算レビュー](modules/budget-review/README.md)と[SQLite入力契約](modules/budget-review/sqlite_input_contract.md)、[失敗パターン](modules/budget-review/failure_patterns.md)を読む。取込用CSVの雛形を生成し、個別AIまたは人がPDFから転記した値を、原典位置つきで埋める。CSVは受け渡し用であり、作業の中心は投入後のSQLiteである。

```sh
python3 modules/budget-review/csv_templates.py > budget.csv
python3 modules/budget-review/ingest_csv.py budget.csv --db budget.db
python3 modules/budget-review/verify_totals.py budget.db
```

検算に通ったDBだけ、確認候補生成へ進める。

```sh
python3 modules/budget-review/insights.py budget.db
```

### 検証

`verify_totals.py` が終了コード0であること、歳入歳出総額、階層合計、前年度比較、補正前後の整合が取れていること、`source_locator` から原表の該当箇所へ戻れることを確認する。`insights.py` の候補は予算審議の入口であり、必要性や妥当性を自動確定しない。

### 中止・巻き戻し

この段階で作った取込用CSV、DB、抽出ログ、内部資料由来の作業物だけを作成記録から特定して退避する。他段階のDBやVaultノートは変更しない。

## 段階6 決算レビュー

この段階は任意である。決算書など自治体固有PDFの数値を検算可能なDBへ入れたい場合だけ実行する。PDFから数字を読む処理そのものは、自治体、年度、会計、公開範囲、PDF品質によって異なるため、本リポジトリは汎用抽出器を提供しない。ここで固定するのは、抽出後のSQLite入力契約、SQLite格納、差額ゼロ検算、公開・非公開境界である。CSVは正本ではなく、SQLiteへ投入するための中間入力形式として扱う。

### 確認

対象資料が公式公開資料か、内部資料か、要配慮資料かを先に分類する。対象年度、会計名、ページ範囲、原表単位、PDFページと印刷ページの対応、外部AIへ送ってよい資料かを確認する。非公開資料は公開用DBへ混ぜず、公開成果物では公式公開資料で再検証できる問いへ変換する。

### 実行

まず [SQLite入力契約](modules/settlement-review/sqlite_input_contract.md)、[抽出ガイダンス](modules/settlement-review/extraction_guidance.md)、[失敗パターン](modules/settlement-review/failure_patterns.md)、[公開・非公開境界](modules/settlement-review/public_private_boundary.md)を読む。取込用CSVの雛形を生成し、個別AIまたは人がPDFから転記した値を、原典位置つきで埋める。作業の中心は投入後のSQLiteであり、CSVは受け渡し用である。

```sh
python3 modules/settlement-review/csv_templates.py summary > summary.csv
python3 modules/settlement-review/csv_templates.py revenue > revenue.csv
python3 modules/settlement-review/csv_templates.py expenditure > expenditure.csv

python3 modules/settlement-review/ingest_csv.py summary summary.csv --db settlement.db
python3 modules/settlement-review/ingest_csv.py revenue revenue.csv --db settlement.db
python3 modules/settlement-review/ingest_csv.py expenditure expenditure.csv --db settlement.db
python3 modules/settlement-review/verify_totals.py settlement.db
```

検算に通ったDBだけ、確認候補生成へ進める。

```sh
python3 modules/settlement-review/insights.py settlement.db
```

`insights.py` の出力は質問候補ではなく、人が確認する入口である。原因、妥当性、政策評価は自動確定しない。

### 検証

`verify_totals.py` が終了コード0であること、`source_locator` から原表の該当箇所へ戻れること、単位、会計、年度、ページが一致していることを確認する。`insights.py` の候補は、原典と担当課説明や監査資料など別根拠へ戻してから採用する。

### 中止・巻き戻し

この段階で作った取込用CSV、DB、抽出ログ、内部資料由来の作業物だけを作成記録から特定して退避する。公開情報基盤、議事録DB、Vaultノートは変更しない。非公開資料を誤って公開用DBへ入れた場合は、そのDBを外部利用不可として破棄し、公開資料だけで再構築する。

## 完了報告

各段階後に、作成物、作成先、スキップ、統合差分、検証結果、未確認事項を報告し、解除方法を示す。段階1は棚とテンプレート、段階2は常設規則とトリガー、段階3は自治体DBと裁定表、段階4は議事録DB・例規DBとキャッシュ、段階5は予算の取込用CSV・SQLite DB・検算結果、段階6は決算の取込用CSV・SQLite DB・検算結果が対象である。作成記録と照合し、基盤側を削除対象にしない。次へ進むかは利用者が決める。
