# 全国自治体観測snapshotの更新

この文書は保守者向けである。通常利用者はCloudflareへ接続せず、同梱snapshotを使う。

正本は`lcaios-explorer`のCloudflare D1である。CloudflareからGitHubへ直接pushせず、
D1を読み取り専用でexportし、このリポジトリのgeneratorで検査してから通常のPRとして
更新する。

## 更新する条件

定期的に毎月更新すること自体を目的にしない。次のいずれかに該当する場合に更新する。

- municipality registryのrevisionが変わった
- explorerのsource scopeまたは取得契約が変わった
- 新しい全国runを正式な比較対象として採用した
- 実利用で、snapshotの陳腐化が複数自治体に反復していると確認した

単発のURL変更だけで全国snapshotを更新する必要はない。該当自治体のlive preflight結果を
fixtureへ還元し、全国観測を更新する価値があるかを分けて判断する。

## 1. 対象runを固定する

次を記録する。

- source run ID
- representative depth1 pilot ID
- rollout depth1 pilot ID
- source scope version
- `lcaios-explorer`のGit revision
- municipality registry revision

source runと二つのdepth1 pilotが同じregistryを対象にし、1,741自治体を重複なく覆うことを
D1で確認する。

## 2. D1を読み取り専用でexportする

次の列名は`bootstrap.observatory.update`の入力契約である。`SOURCE_RUN_ID`と二つの
`DEPTH1_PILOT_ID`を、採用する実IDへ置き換える。

```sql
SELECT
  m.area_code_5 AS municipality_code,
  m.prefecture_name,
  m.municipality_name,
  o.observed_at AS home_observed_at,
  o.acquisition_status AS home_acquisition_status,
  o.stop_reason AS home_stop_reason,
  o.stop_stage AS home_stop_stage,
  s.feature_json,
  o.source_signals_json AS home_source_signals_json,
  o.vendor_signals_json AS home_vendor_signals_json,
  d1.observed_at AS depth1_observed_at,
  d1.profile_id AS depth1_profile_id,
  d1.acquisition_status AS depth1_acquisition_status,
  d1.candidate_count AS depth1_candidate_count,
  d1.pages_attempted AS depth1_pages_attempted,
  d1.pages_acquired AS depth1_pages_acquired,
  d1.new_source_urls_json AS depth1_new_source_urls_json,
  d1.added_vendor_signals_json AS depth1_added_vendor_signals_json,
  d1.page_results_json AS depth1_page_results_json,
  d1.stop_reason AS depth1_stop_reason
FROM runs r
JOIN municipalities m
  ON m.registry_revision_id = r.registry_revision_id
LEFT JOIN observations o
  ON o.run_id = r.id
 AND o.municipality_code = m.area_code_5
LEFT JOIN signatures s
  ON s.sha256 = o.signature_sha256
LEFT JOIN depth1_observations d1
  ON d1.source_observation_id = o.id
 AND d1.pilot_id IN ('DEPTH1_PILOT_ID_1', 'DEPTH1_PILOT_ID_2')
WHERE r.id = 'SOURCE_RUN_ID'
ORDER BY m.area_code_5;
```

上のSQLをID置換後に`/tmp/lcaios-observatory-export.sql`へ保存する。
`lcaios-explorer`のrepository rootで、Wranglerの`--remote --json`を使って結果を一時JSONへ
保存する。これはD1 readであり、`meta.changes = 0`、`rows_written = 0`を確認する。

```bash
query="$(< /tmp/lcaios-observatory-export.sql)"
npx wrangler d1 execute lcaios-explorer \
  --remote \
  --json \
  --command "$query" \
  > /tmp/lcaios-observatory-export.json

jq '.[0] | {
  success,
  row_count: (.results | length),
  changes: .meta.changes,
  rows_written: .meta.rows_written
}' /tmp/lcaios-observatory-export.json
```

期待値は`success: true`、`row_count: 1741`、書込み0である。違う場合はgeneratorへ進まない。

## 3. generatorで置換する

`local-councilor-ai-os`のrepository rootで実行する。

```bash
python3 -m bootstrap.observatory.update \
  --input /tmp/lcaios-observatory-export.json \
  --source-run-id '<source-run-id>' \
  --depth1-pilot-id '<representative-pilot-id>' \
  --depth1-pilot-id '<rollout-pilot-id>' \
  --scope-version '<scope-version>' \
  --explorer-revision '<git-revision>' \
  --replace
```

generatorは次を行う。

- Wrangler JSONから1,741行を抽出
- 同梱municipality registryとコード・名称を照合
- URLをHTTP(S)だけへ正規化
- source種別、vendor signal、候補ページ、停止理由を重複排除
- コード順のcanonical JSONLを生成
- snapshotとregistryのSHA-256を`manifest.json`へ記録

## 4. 差分をレビューする

少なくとも次を確認する。

```bash
python3 - <<'PY'
from collections import Counter
from pathlib import Path

from bootstrap.observatory import load_catalog

catalog = load_catalog()
records = list(catalog["records"].values())
print("records", len(records))
print("bytes", Path("bootstrap/observatory/municipalities.jsonl").stat().st_size)
print("lanes", Counter(record["lane"] for record in records))
print(
    "with_source_urls",
    sum(
        any(record["source_urls"][kind] for kind in record["source_urls"])
        for record in records
    ),
)
print(
    "with_candidate_pages",
    sum(bool(record["candidate_pages"]) for record in records),
)
PY
```

- 件数が1,741である
- snapshot容量が説明できない増加をしていない
- lane、URL、candidateの増減が採用runの変更と整合する
- raw HTML、文書本文、PDF、秘密値、ローカル絶対pathが入っていない
- `manifest.json`のrun ID、scope、revision、生成時刻が正しい
- 日付別snapshotを新規追加せず、現行2ファイルだけを置換している

## 5. 検証する

```bash
python3 -m unittest -v bootstrap.observatory.tests.test_catalog
python3 -m unittest -v bootstrap.cli.tests.test_preflight
ruff check bootstrap/observatory bootstrap/cli/preflight.py
mypy bootstrap/observatory bootstrap/cli/preflight.py
./run_tests.sh
```

合成テストに加え、採用runで意味のある自治体1件を最大4〜8ページでlive preflightする。
過去URLだけで`ready`にならず、現在の公式リンクで確認したsourceだけが昇格することを見る。

## 6. PRに残すもの

- 採用したrun ID、pilot ID、scope version、explorer revision
- 自治体件数、snapshot容量、主要laneとURL件数の差分
- live確認した自治体と、ready／非readyの理由
- 全テスト、lint、mypyの結果
- raw HTML、PDF、DBを同梱していないこと

更新に失敗した場合は生成途中の`.tmp`ではなく、Git上の直前snapshotへ戻す。D1の観測正本を
修正する必要がある場合は`lcaios-explorer`側で別の変更として扱い、こちらで結果を手編集しない。
