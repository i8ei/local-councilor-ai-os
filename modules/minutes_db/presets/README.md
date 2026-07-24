# 静的アダプターのプリセット

プリセットは、自治体サイトでよく見かける公開構成に合わせた設定のひな型です。
まず自分の議会の会議録ページをブラウザーで開き、リンクを1、2段たどって、
次のうち最も近い構成を選びます。

- [`pdf-per-session.json`](pdf-per-session.json): 定例会・臨時会のページに、
  日別または議題別のPDFが直接並ぶ。
- [`pdf-index-all.json`](pdf-index-all.json): 1つの一覧ページに、複数年度の
  会議録PDFが直接並ぶ。
- [`html-minutes.json`](html-minutes.json): 一覧ページから、話者表示を含む
  HTML会議録へ直接移動する。
- [`year-index-two-level.json`](year-index-two-level.json): 1年度の索引ページから
  定例会・臨時会のページへ進み、その先にPDFが並ぶ。

選んだJSONを作業用ファイルへコピーし、`index_url`と`council_name`だけを
実際の値へ置き換えます。`_comment`は説明用で、設定として読み込んでも
アダプターの動作には影響しません。初回から正規表現を調整せず、まず2件だけ
試してください。

```bash
cp presets/pdf-per-session.json municipality.json

python3 ingest.py \
  --adapter static \
  --config municipality.json \
  --db preset-test.db \
  --limit 2
```

## 推奨する確認ループ

1. `ingest --limit 2`の結果で、`meetings`が2件以下、`statuses`が想定どおり、
   `speeches`が0件ではないことを確認します。対象ページに会議録が1件しか
   なければ、`meetings: 1`で正常です。PDFで`pdftotext`が使えない場合や
   画像PDFの場合は、状態と問題点が来歴に記録されます。
2. 取り込んだ原文に実際に現れる異なる語を2つ選び、それぞれ検索します。

   ```bash
   python3 search.py "原文にある語その1" --db preset-test.db --k 10
   python3 search.py "原文にある語その2" --db preset-test.db --k 10
   ```

3. 検索結果を少なくとも1件選び、表示された`source_url`を画面で開きます。
   話者、本文、日付、会議名を元のHTML/PDFと見比べ、同じ内容か確認します。

期待する文書が0件、関係のない資料が混入、または必要なページがさらに深い場合は、
正規表現を推測で広げ続けないでください。どのプリセットにも合わない構成は
[`adapter_guidance.md`](../adapter_guidance.md)を参照し、サイト固有アダプターを
検討します。`follow_link_regex`は索引から一致ページを1段だけ取得し、そのページの
文書リンクを調べます。多段再帰は行いません。
