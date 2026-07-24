# 議事録データベース

自治体が公式公開する議事録を、取得元へ戻れる来歴付きで
SQLite/FTS5へ格納するTier 2アダプターです。SQLiteは原典の代替ではなく、
ローカルキャッシュから再構築できる検索用の派生層です。

## 構成

```text
公式索引・CMS API
  └─ adapters（取得・抽出）
       └─ 会議・発言の共通形式 + provenance
            └─ SQLite + FTS5（trigram、非対応時はunicode61）
                 ├─ search.py（LIKE補完）
                 └─ context_pack.py（出典付き最小抜粋）
```

`meetings`は会議名、日付、取得元URL、アダプター、取得時刻を保持します。
`speeches`は会議内の連番、話者、役職、本文、原典位置を保持します。
`provenance`には公式索引、取得時URL、取得時刻、メディア型、SHA-256、
キャッシュ位置、決定的な変換内容、処理状態と問題点を記録します。

## 対応アダプター

### 対応範囲の区分

| アダプター | detect | ingest | ライブ検証 | 既知の限界 |
|---|---|---|---|---|
| kaigiroku.net | 対応 | 対応 | 合成fixture（robots制約でAPIライブ未検証） | tenant URL必須。tenant名は推測しない |
| 静的HTML/PDF | 対応 | 対応 | 実在1サイトでライブ検証済み | OCR・画像PDF・複雑な表は未対応 |
| voices系 | 検出のみ | 未対応 | — | 利用者AIが契約に沿って自作 |
| discuss系 | 検出のみ | 未対応 | — | 利用者AIが契約に沿って自作 |
| unknown | 検出のみ | 未対応 | — | 公式経路を人が確認して設定 |

「検出できる」ことと「安全に取込できる」ことは別である。detectだけのベンダーは、対応adapterがある状態と混同しない。

### kaigiroku.net

`https://ssp.kaigiroku.net/tenant/<name>/` 形式のtenant URLだけを受け付けます。
tenant名はURLから抽出し、推測しません。CMSが使うJSONP APIについて、
議会一覧、表示年、会議索引、会議本文を順に取得し、callbackラッパー、
UTF-8/CP932を処理する実装です。

ただし2026-07-23のライブ確認では、`robots.txt`が`/tenant/`を許可する一方、
共有JavaScriptの`/tenant/js/`とAPIの`/dnp/search/`を禁止していました。
そのためAPI呼び出しと実会議の取込は行っていません。現在の状態は
**implemented, live-unverified**です。APIパラメーターと応答差異への対応は
合成JSONP fixtureでのみ検証済みで、サイト変更時はfixtureの更新が必要です。

### 静的HTML/PDF

小規模自治体など、公式サイトの索引ページから通常のHTML/PDFとして議事録を
公開する場合に使います。JSON設定例:

```json
{
  "index_url": "https://www.example.jp/gikai/minutes/",
  "link_include_regex": "(gijiroku|kaigiroku|minutes)",
  "link_exclude_regex": "(summary|agenda)",
  "pdf": true,
  "council_name": "例示町議会"
}
```

索引に実在するリンクだけを発見し、不透明なPDF URLを組み立てません。
`pdf`は`true`でPDFだけ、`false`（未指定時の既定値）でHTMLだけを対象にします。
HTMLはタグ除去後、`○議長`、`◯○○君`、`〔……〕`などを手掛かりに発言へ
分割します。話者構造が見つからない場合は段落単位に戻します。

PDFはPATH上の`pdftotext`が利用できる場合だけテキスト化します。ない場合も
PDF本体はキャッシュし、`pdf_cached_pdftotext_unavailable`状態とキャッシュパスを
来歴へ残します。OCR、画像PDF、表組み、ページ番号の厳密な復元は未対応です。

### 静的設定プリセット

自治体サイトでよくある公開構成に合わせた設定例を
[`presets/`](presets/README.md)に用意しています。最も近い構成を選び、
まず`index_url`と`council_name`だけを置き換えて`--limit 2`で確認してください。
年度索引から会期ページを経てPDFへ進む構成は、任意の`follow_link_regex`で
一致ページを1段だけ追跡できます。合うプリセットがなければ
[`adapter_guidance.md`](adapter_guidance.md)を参照してください。

### 未対応ベンダー

`detect.py` が検出だけ可能なベンダー（`voices` / `discuss`）や `unknown` を返した場合、取込は利用者のAIエージェントが本モジュールの契約に沿って自作できます。スキーマ、礼節基盤、参照実装2本を渡す手順は[未対応ベンダーに出会ったら](adapter_guidance.md)にまとめています。汎用化できた実装は本体への取り込みを歓迎します。

## 使い方

すべての例はこのディレクトリで実行します。

```bash
python3 detect.py https://www.example.jp/gikai/

python3 ingest.py \
  --adapter kaigiroku_net \
  --url https://ssp.kaigiroku.net/tenant/sakuho/ \
  --db minutes.db \
  --limit 2

python3 ingest.py \
  --adapter static \
  --config municipality.json \
  --limit 20 \
  --dry-run

python3 ingest.py \
  --adapter static \
  --config municipality.json \
  --db minutes.db \
  --limit 2

python3 search.py "防災" --db minutes.db --k 10

python3 context_pack.py "防災" \
  --question "地域防災計画はいつ見直されたか" \
  --db minutes.db \
  --k 5 \
  --char-budget 6000
```

静的アダプターの`--dry-run`は、索引と設定したfollow対象HTMLまでを読み、各リンクを`selected`、`excluded_by_regex`、`format_mismatch`、`duplicate`に分けます。会議本文やPDFを取得せず、DBも作りません。候補を確認してから少数件の本取込へ進んでください。

同じ`source_url`と会議内`seq`の再取込は更新となり、重複行を作りません。
検索結果は話者、日付、会議名、抜粋、原典URL、原典位置、取得時刻を返します。
FTS5が使えない場合、FTS構文が不正な場合、trigramで扱いにくい短語の場合は
リテラルな`LIKE`検索で補完します。

コンテキストパックは引用本文を改変せず、話者、会議、日付、原典URL、
原典位置、取得時刻とともにJSON化します。`--char-budget`は引用文字数の合計
上限です。検索式と人が答えたい問いが異なる場合は`--question`で分けます。これは検索結果であり、採用する解釈や判断は別の判断ノートへ
検索条件・対象範囲・欠落情報とともに戻してください。

## 取得時の礼節とキャッシュ

- User-Agentは
  `local-councilor-ai-os minutes ingester (research; low rate)`。
- ホストごとに`robots.txt`を確認し、禁止されたURLは取得しない。
- プロセス内で単一のHTTP接続経路を使い、全HTTP要求の開始間隔を1.5秒以上
  空ける。
- 原典と取得メタデータを`.cache/`へ保存する。再実行はキャッシュを使い、
  同じURLを再取得しない。
- 各取得に`fetched_at`、最終URL、メディア型、SHA-256を記録する。
- リダイレクト先もrobots規則の対象とし、取得不能を推測URLで迂回しない。

キャッシュ、SQLite、WAL/SHM、Pythonキャッシュは`.gitignore`対象です。
自治体の実データをテストfixtureやGitへ含めません。

## 検出と対応範囲

`detect.py`は入力URLそのものと、許可される場合はページ内リンクを調べ、
証拠となる一致URLをJSONで返します。

- `kaigiroku_net`: `ssp.kaigiroku.net`へのリンクまたは同ホスト。
- `voices`: `*.gijiroku.com/voices/`。検出のみで取込未実装。
- `discuss`:既知のホスト/URLシグナル。確証がなければ`unknown`。
- `static_candidate`:公式ページ上にHTML/PDFの議事録候補リンクがある。
- `unknown`:根拠となるリンクやパターンがない。

検出はtenant名や不透明なURLを推測しません。誤分類を避けるため、不確かな
Discuss判定や単なる一般ページは`unknown`に戻します。

## テストとライブ検証

```bash
python3 -m unittest discover -s tests -v
```

テストは小さな架空JSONP、架空HTML、任意の偽PDFだけを使います。
2026-07-23のライブ確認は佐久穂tenantに対する`robots.txt` 1要求だけです。
robotsの禁止を確認したため、会議取得は0件で、API、tenant ID初期化、
実応答の正規化はライブ未検証です。

静的アダプターは2026-07-23に、`robots.txt`が対象パスを許可する自治体公式サイトの
会議録ページ1件（PDF 1本、`--limit 1`）でライブ検証済みです。取込、trigram FTS検索、
原典URLとページ位置つきのコンテキストパック生成までの一連が実データで動作しました。
