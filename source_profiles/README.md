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
    "budget":      {"status": "ready", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/budget.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...]},
    "settlement":  {"status": "ready", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/settlement.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...]}
  }
}
```

### ステータス (`status`)
- `ready`: 実エビデンス（実体文書・条文・発言等）の存在と構造が検証済みの状態
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
実際に自治体サーバーまたはベンダーにアクセスし、文書構造（条番号・発言者・予算決算マーカー）を確認して `ready` へ昇格させます（※推測URLは禁止、robots.txt 厳守）。

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

全国 1,741 自治体 × 4 種別（計 6,958 エントリ）において、**4,420 件（63.5%）** が `ready`（検証済み）に到達しています。

| 種別 | ready | needs_review | not_found | blocked | unsupported | 総数 | ready率 |
|---|---|---|---|---|---|---|---|
| **会議録 (`minutes`)** | 1,073 | 68 | 418 | 135 | 47 | 1,741 | 61.6% |
| **条例・例規 (`regulations`)** | 1,111 | 260 | 224 | 87 | 59 | 1,741 | 63.8% |
| **当初予算 (`budget`)** | 1,119 | 186 | 361 | 72 | 0 | 1,738 | 64.4% |
| **決算・財政 (`settlement`)** | 1,117 | 182 | 369 | 70 | 0 | 1,738 | 64.3% |
| **合計** | **4,420** | **696** | **1,372** | **364** | **106** | **6,958** | **63.5%** |

---

## テスト

```bash
# 全テスト実行
python3 -m unittest source_profiles.tests.test_schema source_profiles.tests.test_cli source_profiles.tests.test_verify
ruff check source_profiles
mypy source_profiles
python3 -m source_profiles.cli validate --all
```
