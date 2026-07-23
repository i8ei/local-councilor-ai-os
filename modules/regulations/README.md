# 例規検索

公式に公開された例規ページを、条単位で検索できるSQLite/FTS5へ変換する最小モジュールです。SQLiteは原典の代替ではなく、公式表示へ戻るための検索層です。現行条文、改正履歴、施行時点の解釈は、必ず原典画面で確認してから外部利用します。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 例規文書、条単位本文、取得来歴を格納する |
| `ingest.py` | 設定した公式索引ページからHTML/TXT例規を発見・取得・条分割する |
| `vendor_greiki.py` | g-reiki系テナントの50音索引から例規を発見・取得する |
| `search.py` | FTS5検索とLIKE補完で条文を検索する |
| `context_pack.py` | 引用文字数上限つきの条文抜粋JSONを作る |

## 設定例

```json
{
  "index_url": "https://www.example.jp/reiki/",
  "link_include_regex": "(条例|規則|reiki|html$)",
  "link_exclude_regex": "(廃止|old)",
  "municipality": "例示町",
  "source_name": "例示町例規集",
  "category": "ordinance"
}
```

`index_url` は文字列または配列です。`link_include_regex` を省略した場合は、リンクURLまたはラベルに「条例」「規則」「要綱」「例規」「reiki」等を含むものだけを候補にします。不透明URLを推測せず、索引ページ上で観測できたリンクだけを取得します。

## 使い方

```bash
python3 ingest.py --config municipality.json --db regulations.db --limit 10
python3 search.py "個人情報" --db regulations.db --k 10
python3 context_pack.py "個人情報の取扱い" --db regulations.db --k 5 --char-budget 6000
```

`ingest.py` は `robots.txt`、低頻度取得、SHA-256つきキャッシュを、議事録DBと同じ取得器で扱います。HTMLは標準ライブラリの `HTMLParser` で可視テキスト化し、`第1条` / `第一条` 形式の見出しで条単位に分割します。条見出しを検出できない場合は文書単位のfallbackチャンクを保存します。

## g-reikiベンダーアダプター

g-reiki（Reiki-Base インターネット版）は、多くの自治体が条例・規則・要綱等の公開に利用している例規サイトシステムです。`vendor_greiki.py` は、利用者が指定したテナントのベースURLだけを対象にします。テナント名は推測しません。

最初は必ず少数件で、索引構造と抽出結果を確認してください。

```bash
python3 vendor_greiki.py \
  --base-url "https://www1.g-reiki.net/<tenant>/" \
  --db regulations.db \
  --source-name "○○自治体例規集" \
  --limit 3
```

アダプターは次の順に処理します。

1. 対象ホストの `robots.txt` を確認し、拒否された経路には進みません。
2. テナント直下のg-reiki標準入口 `reiki_menu.html` を確認します。
3. 入口から実際にリンクされた `reiki_kana/` の50音索引だけを辿ります。
4. 50音索引に実在する `reiki_honbun/` リンクだけを列挙し、本文の `div#primary` を取得します。
5. 既存の `regulation_documents`、`regulation_articles`、`regulation_provenance` に保存します。

標準入口または50音索引の期待構造が見つからない場合は、別URLを試行せず `structure_mismatch` で停止します。本文はUTF-8、Shift_JIS/Windows-31J（CP932）を判定してデコードします。各文書には索引上の `source_url`、各条には解決後URLと抽出本文行を組み合わせた `locator` を保存します。取得には正直なUser-Agent `local-councilor-ai-os regulations ingester (research; low rate)`、1.5秒以上の直列間隔、SHA-256つきローカルキャッシュを使い、`fetched_at` を来歴に残します。

全件取得は遅いことが正常です。例規数と50音索引数に応じて数十分かかり、実運用例では1自治体の全件取得に約22分を要しました。これはアクセス間隔を短縮しないための意図的な設計です。初回は `--limit 3` 程度から始め、DB内の条分割と公式画面を照合してから全件取得してください。

### 検証状況

2026-07-23に、実在する1自治体のg-reikiテナントで `--limit 3` を実行しました。`robots.txt` はHTTP 404（明示的な拒否規則なし）で、標準入口、50音索引、索引上の本文リンクを順に取得し、3例規・41チャンクを既存スキーマへ保存できることを確認しました。本文3件はいずれもUTF-8でした。Shift_JIS/Windows-31J経路はCP932の合成fixtureで検証しています。

## 限界

- g-reikiのJavaScript実行を必要とするページ、PDF例規、画像本文は抽出しません。
- 改正履歴・沿革のAPIや内部エンドポイントにはアクセスしません。
- 50音索引からリンクされない例規は取得しません。
- 施行日と公布日の機械抽出は簡易推定にとどまります。
- 外部引用前には公式画面で条番号、施行時点、前後条文を人が確認してください。
