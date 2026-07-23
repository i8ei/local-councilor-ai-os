# 例規検索

公式に公開された例規ページを、条単位で検索できるSQLite/FTS5へ変換する最小モジュールです。SQLiteは原典の代替ではなく、公式表示へ戻るための検索層です。現行条文、改正履歴、施行時点の解釈は、必ず原典画面で確認してから外部利用します。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 例規文書、条単位本文、取得来歴を格納する |
| `ingest.py` | 設定した公式索引ページからHTML/TXT例規を発見・取得・条分割する |
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

## 限界

- ベンダー固有の改正履歴APIやフレーム構造は未対応です。
- PDF例規、画像、JavaScript必須ページの抽出は未対応です。
- 施行日と公布日の機械抽出は簡易推定にとどまります。
- 外部引用前には公式画面で条番号、施行時点、前後条文を人が確認してください。
