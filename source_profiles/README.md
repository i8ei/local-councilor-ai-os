# 自治体ソースプロファイル (`source_profiles`)

全国 1,741 自治体の議事録・例規集・当初予算・決算の公式入口（URL・アダプタ・検証エビデンス）を管理する基盤です。

## ディレクトリ構成

```text
source_profiles/
  schema.py                          # プロファイル検証ロジック（標準ライブラリのみ）
  cli.py                             # CLI（validate / ingest-command / verify / resolve）
  __main__.py                        # python3 -m source_profiles.cli
  schema/source_profile.schema.json  # JSONスキーマ定義
  municipalities/*/*.json            # 自治体プロファイル（全国47都道府県・1,741ファイル）
  tests/                             # 単体・回帰テスト
  README.md
```

## プロファイル構造

1自治体につき1つのJSONファイル（`area_code_5` を主キー）で管理します。

```json
{
  "schema_version": 1,
  "area_code_5": "41441",
  "prefecture": "佐賀県",
  "municipality": "太良町",
  "official_home_url": "http://www.town.tara.lg.jp/",
  "sources": {
    "minutes":     {"status": "ready", "adapter": "static", "index_url": "http://www.town.tara.lg.jp/gikai/", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...]},
    "regulations": {"status": "ready", "adapter": "g_reiki", "base_url": "https://www1.g-reiki.net/town.tara/", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...]},
    "budget":      {"status": "document_confirmed", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/budget.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --doc-structure", "evidence": [...]},
    "settlement":  {"status": "document_confirmed", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/settlement.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --doc-structure", "evidence": [...]}
  }
}
```

### ステータス (`status`)

`ready` の意味は種別ごとに違います。**この2つを合算した数字は「検証済み」を過大に見せる**ので、集計は分けて出してください。

| 種別 | `ready` の条件 | 付与する主体 |
|---|---|---|
| 会議録 (`minutes`) | 取込アダプタが**発言者付きの発言を1件以上抽出**できた | `verify --live`（機械） |
| 条例・例規 (`regulations`) | 取込アダプタが**条番号付きの条を1件以上抽出**できた | `verify --live`（機械） |
| 当初予算 (`budget`) | **実際に取り込んで**予算レコードを得た | 人（取込後に手動付与） |
| 決算・財政 (`settlement`) | **実際に取り込んで**決算レコードを得た | 人（取込後に手動付与） |

予算・決算に汎用抽出器は提供しません（`extraction_guidance` のとおり、レコード抽出は利用者のAI/人に委ねる境界）。したがって機械検証が到達できる上限は `document_confirmed` であり、`verify` および `tools/verify_budget_settlement_concurrent.py` は予算・決算に `ready` を付けません。

- `ready`: 取込アダプタが実レコードを抽出できた状態（上表参照）
- `document_confirmed`（予算・決算のみ）: 実文書に到達し構造マーカー（`歳入`・`歳出`・`款`・`項` 等を2つ以上）を確認した状態。**文書の存在は検証済みだが、レコード抽出は未実施**
- `needs_review`: 公式ページ上に入口は検知されたが、詳細精査が未完了の状態
- `not_found`: 標準探索の範囲内では入口が未検出（またはWeb非公開）の状態
- `blocked`: `robots.txt` によりクローラーのアクセスが拒否されている状態（安全隔離）
- `unsupported`: ぎょうせい JSF POST動的型や動画配信など、現行クローラー未対応の方式
- `not_evaluated`: 未評価

### 主要アダプタ (`adapter`)
- `kaigiroku_net`: 会議録Net（`tenant_url` 必須）
- `g_reiki`: ぎょうせい 例規Net（`base_url` 必須）
- `d1_law`: 第一法規 D1-Law（`index_url` 必須）
- `joureikun`: 条例Web / joureikun（`index_url` 必須）
- `static`: 自治体公式の静的HTML / PDF一覧（`index_url` 必須）
- `official_document_index`: 予算・決算などの公式文書インデックス（`index_url` 必須）

---

## 主なコマンド操作

### 1. プロファイルの整合性検証 (`validate`)
自治体コード・自治体名・公式URLのドリフトや、スキーマ違反がないかを検証します。

```bash
# 全 1,741 自治体のプロファイルを一括検証
python3 -m source_profiles.cli validate --all

# 特定都道府県のみ検証
python3 -m source_profiles.cli validate --all --prefecture "佐賀県"

# 単一プロファイルの検証
python3 -m source_profiles.cli validate --profile source_profiles/municipalities/41-saga/41441-tara.json
```

### 2. 取り込みコマンドの自動生成 (`ingest-command`)
プロファイルの設定に基づき、対応モジュールの実行コマンドを生成します（※コマンドの出力のみで実行はしません）。

```bash
python3 -m source_profiles.cli ingest-command \
  --municipality "太良町" --prefecture "佐賀県" --kind regulations --limit 3
# 出力例:
# python3 modules/regulations/vendor_greiki.py --base-url https://www1.g-reiki.net/town.tara/ --db /tmp/41441-reg.db --source-name "太良町例規集" --limit 3
```

### 3. 実データ検証と昇格 (`verify`)
実際に自治体サーバーまたはベンダーにアクセスして昇格させます（※推測URLは禁止、robots.txt 厳守）。会議録・例規は取込アダプタで実レコード（発言・条）を抽出できた場合に `ready` へ、予算・決算は実文書に到達し構造マーカーを確認できた場合に `document_confirmed` へ昇格します（予算・決算に `ready` は付きません）。

```bash
# 単一自治体の例規をライブ検証
python3 -m source_profiles.cli verify \
  --municipality "太良町" --prefecture "佐賀県" \
  --kind regulations --cache-dir /tmp/sp-verify-cache

# 全国予算・決算の Level 2 並列文書構造検証
python3 tools/verify_budget_settlement_concurrent.py --workers 16
```

### 4. オンデマンド文書一覧の解決 (`resolve`)
自治体のインデックスページから、同一ホスト内の実体文書（PDF / Excel / Word）をラベル付きで抽出・ダウンロードします。

```bash
# 文書リストをJSONで取得
python3 -m source_profiles.cli resolve \
  --municipality "伊万里市" --prefecture "佐賀県" --kind budget --cache-dir /tmp/sp-cache

# 特定の文書（5番目）をダウンロードしてローカルパスとSHA256を取得
python3 -m source_profiles.cli resolve \
  --municipality "伊万里市" --prefecture "佐賀県" --kind budget --get 5
```

---

## 全国プロファイル確定状況 (Nationwide Coverage)

全国 1,741 自治体 × 4 種別（計 6,964 エントリ）の内訳です。**`ready` と `document_confirmed` は保証の強さが違うので合算しないでください。**

| 種別 | ready | document_confirmed | needs_review | not_found | blocked | unsupported | 総数 |
|---|---|---|---|---|---|---|---|
| **会議録 (`minutes`)** | 1,073 | — | 68 | 418 | 135 | 47 | 1,741 |
| **条例・例規 (`regulations`)** | 1,111 | — | 260 | 224 | 87 | 59 | 1,741 |
| **当初予算 (`budget`)** | 0 | 1,104 | 204 | 361 | 72 | 0 | 1,741 |
| **決算・財政 (`settlement`)** | 0 | 1,102 | 200 | 369 | 70 | 0 | 1,741 |
| **合計** | **2,184** | **2,206** | **732** | **1,372** | **364** | **106** | **6,964** |

- **取込アダプタで実レコードを抽出済み（`ready`）**: 2,184 件（31.4%）— 会議録・例規
- **実文書に到達し構造を確認済み（`document_confirmed`）**: 2,206 件（31.7%）— 予算・決算。取込は利用者の工程
- 予算・決算の `ready` は現在 0 件です。実際に取り込んだ人が付与するため、機械走査では増えません

---

## テスト

```bash
# 全テスト実行
python3 -m unittest source_profiles.tests.test_schema source_profiles.tests.test_cli source_profiles.tests.test_verify
ruff check source_profiles
mypy source_profiles
python3 -m source_profiles.cli validate --all
```
