# Tier 2: 議事録CMS・静的公開サイトのアダプター

自治体議会の議事録は全国共通のURLや画面構造では公開されていません。
Tier 2では、自治体公式サイトから実在する公開経路を発見し、ベンダーCMSまたは
静的HTML/PDF用アダプターへ振り分けます。tenant名、ファイル名、不透明なIDを
推測して直接アクセスしません。

## 確認できているベンダー系統

- **kaigiroku.net**:
  `https://ssp.kaigiroku.net/tenant/<name>/`形式。佐久穂・肝付のtenantが
  2026-07-23時点で存在することを確認済みです。tenantトップは
  `pg/index.html`へ移り、そこはShift_JIS/CP932、`MinuteBrowse.html`と
  `MinuteSearch.html`はUTF-8のJavaScript画面です。共有release JavaScriptは
  `/tenant/js/release/`、JSONP APIのrootは`/dnp/search/`です。
- **VOICES**:
  `<name>.gijiroku.com/voices/`形式。開発元サイトは80以上の自治体での利用を
  掲げています。現状はリンク/ホスト検出だけで、取込アダプターは未実装です。
- **Discussシリーズ**:
  複数の導入形態・ホスト表現があり得るため、既知シグナルへの
  best-effort検出に限定します。曖昧な場合は`unknown`とし、取込は未実装です。

## 実装する二つの系統

1. **ベンダーCMSアダプター**

   現在はkaigiroku.netを対象に、議会一覧→表示年→会議索引→会議本文という
   JSON(P) APIの流れを共通の会議・発言スキーマへ正規化します。
2. **設定駆動の静的HTML/PDFアダプター**

   自治体公式の索引URLとリンクの包含/除外正規表現を設定し、索引上にある
   HTML/PDFだけを取得します。HTMLは発言者記号を手掛かりに分割し、構造が
   なければ段落へ戻します。PDFは`pdftotext`がある環境だけでテキスト化し、
   ない場合も原本キャッシュと明示的なskip状態を残します。

正規化結果は`modules/minutes-db/`のSQLite/FTS5へ入り、検索結果から
引用、話者、会議、日付、原典URL、位置、取得時刻を含む小さな
コンテキストパックを生成できます。評価や政治的判断はDBへ保存しません。

## 検出戦略

最初の入力は自治体公式サイトまたは既知の議事録ページです。入力URL自身と
ページ内リンクを調べ、一致した実URLをevidenceとして返します。

```text
ssp.kaigiroku.net/tenant/...        -> kaigiroku_net
<name>.gijiroku.com/voices/...      -> voices
明確なDiscussシグナル             -> discuss
議事録らしいHTML/PDFへの実リンク   -> static_candidate
根拠なし・曖昧                     -> unknown
```

自治体名からtenantを生成する、検索エンジンの断片だけで採用する、索引にない
PDF URLを連番で組み立てる、といった探索はしません。検出だけ対応する
`voices`/`discuss`を取込可能と表示しないことも運用上の要件です。

取込アダプターがないベンダーは、利用者のAIエージェントが本モジュールの契約に
沿って自作する想定です。手順は
[未対応ベンダーに出会ったら](../../modules/minutes-db/adapter_guidance.md)を
参照してください。

## 取得時の礼節

- 各ホストの`robots.txt`を先に確認し、禁止経路は取得しない。
- 正直なUser-Agent
  `local-councilor-ai-os minutes ingester (research; low rate)`を使う。
- 単一接続経路とし、HTTP要求の開始間隔を1.5秒以上空ける。
- 取得物と取得メタデータをローカルキャッシュし、再実行で再取得しない。
- `fetched_at`、取得時URL、メディア型、SHA-256、発見元索引、変換結果を残す。
- リダイレクト、文字コード、JSONP、抽出不能を黙って成功扱いにしない。
- 禁止や構造不明を推測URL・別経路で迂回しない。

## 現在の実装範囲と検証状態

`modules/minutes-db/`にはkaigiroku.netアダプター、静的HTML/PDFアダプター、
検出CLI、取込CLI、SQLite/FTS5、検索CLI、コンテキストパック生成があります。
VOICESとDiscussは検出のみです。

2026-07-23、佐久穂tenantについてライブ要求を1件だけ行い、
`robots.txt`を確認しました。`/tenant/`は許可されますが、共有JavaScriptの
`/tenant/js/`とAPIの`/dnp/search/`は禁止でした。このため禁止経路への要求を
行わず、実会議の取得件数は0件です。kaigiroku.netアダプターは
**implemented, live-unverified**であり、JSONP処理と正規化は合成fixtureで
検証します。静的アダプターも合成fixtureで検証し、特定自治体に対するライブ
取得実績をこの文書では主張しません。
