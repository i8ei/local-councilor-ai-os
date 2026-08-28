# source_profiles

Municipality source profiles: verified entry points for ingestion modules.

## Layout

```text
source_profiles/
  schema.py                          # validator (stdlib only)
  cli.py                             # validate / ingest-command / verify / resolve
  __main__.py                        # python3 -m source_profiles.cli
  schema/source_profile.schema.json  # contract (draft-07, reference)
  municipalities/*/*.json            # 1 file per municipality (1,741 files across 47 prefectures)
  tests/
  README.md
```

## Profile shape

One JSON per municipality (`area_code_5` is primary key).

```json
{
  "schema_version": 1,
  "area_code_5": "41441",
  "prefecture": "佐賀県",
  "municipality": "太良町",
  "official_home_url": "http://www.town.tara.lg.jp/",
  "sources": {
    "minutes":     {"status": "needs_review", "adapter": "static", "index_url": "http://www.town.tara.lg.jp/gikai/", "verified_at": null, "verified_by": null, "evidence": [], "notes": "..."},
    "regulations": {"status": "ready", "adapter": "g_reiki", "base_url": "https://www1.g-reiki.net/town.tara/", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [{"url": "https://www1.g-reiki.net/town.tara/reiki_menu.html", "observed_on": "http://www.town.tara.lg.jp/", "sha256": "...", "fetched_at": "..."}], "notes": "..."},
    "budget":      {"status": "ready", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/budget.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...], "notes": "..."},
    "settlement":  {"status": "ready", "adapter": "official_document_index", "index_url": "http://www.town.tara.lg.jp/zaisei/settlement.html", "verified_at": "2026-08-28T00:00:00Z", "verified_by": "verify --live", "evidence": [...], "notes": "..."}
  }
}
```

- `status`: `ready | needs_review | unsupported | not_found | blocked | not_evaluated`
- `adapter`: `null | kaigiroku_net | static | g_reiki | dbsr | voices | d1_law | joureikun | official_document_index`
- Entry keys are mutually exclusive: `g_reiki` requires `base_url`, `static` / `d1_law` / `official_document_index` require `index_url`, `kaigiroku_net` requires `tenant_url`. Multiple simultaneous entries are rejected.
- `ready` requires `verified_at` (UTC ISO8601, not future) + `verified_by` + adapter + entry URL + `evidence>=1`. `verified_at` violation or missing fields => validation error.
- Host check (anti-guess): whenever an entry URL exists, `evidence` must contain at least one entry whose `url` or `observed_on` shares the same host as the entry URL.

## Registry drift

`validate_profile` cross-checks `area_code_5 / prefecture / municipality / official_home_url` against `bootstrap/municipalities/registry.py`. Any mismatch is an error (official_home_url drift included).

## CLI

```bash
# validate all profiles in a prefecture (or omit --prefecture for nationwide 1,741)
python3 -m source_profiles.cli validate --all --prefecture "佐賀県"

# single profile
python3 -m source_profiles.cli validate --profile source_profiles/municipalities/41-saga/41441-tara.json

# generate ingest command (does not execute)
python3 -m source_profiles.cli ingest-command --municipality "太良町" --prefecture "佐賀県" --kind regulations --limit 3
# -> python3 modules/regulations/vendor_greiki.py --base-url https://www1.g-reiki.net/town.tara/ --db /tmp/41441-reg.db --source-name "太良町例規集" --limit 3

# unsupported adapter => exit 2 with next action
python3 -m source_profiles.cli ingest-command --municipality "江北町" --prefecture "佐賀県" --kind regulations
```

`validate` prints a JSON report to stdout and exits `2` on errors. `ingest-command` prints the command (plus a leading `# NEEDS LIVE VERIFICATION` comment when `needs_review`) and exits `2` for unsupported adapters.

## Verify (live re-check with HttpClient)

Verify promotes `needs_review`/`not_evaluated` entries to `ready` only after a live fetch succeeds AND a real extraction probe yields structural evidence.

```bash
# verify a single municipality live (low rate, robots respected, HttpClient cache)
python3 -m source_profiles.cli verify \
  --municipality "太良町" --prefecture "佐賀県" \
  --kind regulations --cache-dir /tmp/sp-verify-cache

# offline: use only verified cache
python3 -m source_profiles.cli verify \
  --municipality "太良町" --prefecture "佐賀県" \
  --kind regulations --cache-dir /tmp/sp-verify-cache --offline

# nationwide Level 2 verification for budget & settlement
python3 tools/verify_budget_settlement_concurrent.py --workers 16
```

Steps inside `verify_profile` (injectable `client`, no guessing):

1. **Gate & Ingress**: Entry URL derivation (e.g. `base_url + "reiki_menu.html"` or `reiki.html` for g_reiki; `reiki.htm` / `reiki.html` for D1-Law).
2. **Robots & Fetch**: Fetch via `lcaios.http.HttpClient` (`REGULATIONS_USER_AGENT` / `MINUTES_USER_AGENT`, low rate throttle, robots check, SHA256 cache).
3. **Fail-closed**: On `RobotsDeniedError` (e.g. `houmu.h-chosonkai.gr.jp`, `dbsr.jp`, `kaigiroku.net`), status becomes `blocked` with evidence and notes. On host drift or structure mismatch, safe stop (exit `2`).
4. **Extraction probe**: Call real ingest extractor / document reader against target documents:
   - **R1 — promotion gate**: `ready` is granted ONLY when prior status is `needs_review` or `not_evaluated`.
   - **R2 — structural identification**:
     - `minutes`: ≥1 speech with non-empty `speaker`.
     - `regulations`: ≥1 article with non-empty `article_no`.
     - `budget` / `settlement`: ≥2 structural markers (`歳入`, `歳出`, `款`, `項`, `目`, `節`, `決算額`, `予算額`, `実質収支` 等) confirmed in deepest PDF/Excel document, with SHA256 recorded in `evidence`.
   - **R3 — robots denial on probe**: status becomes `blocked`.
   - **R4 — read but unidentifiable / newsletter only**: status stays/becomes `needs_review`.
   - **R5 — probe inconclusive / unreachable**: status stays unchanged (`failed` report).

## Nationwide Coverage（全国プロファイル確定状況）

全国 1,741 自治体 × 4 種別（計 6,958 エントリ）において、**4,420 件（63.5%）** が `ready`（検証済み）に到達。

| 種別 | ready | needs_review | not_found | blocked | unsupported | 総数 | ready率 |
|---|---|---|---|---|---|---|---|
| **minutes (会議録)** | 1,073 | 68 | 418 | 135 | 47 | 1,741 | 61.6% |
| **regulations (条例・例規)** | 1,111 | 260 | 224 | 87 | 59 | 1,741 | 63.8% |
| **budget (当初予算)** | 1,119 | 186 | 361 | 72 | 0 | 1,738 | 64.4% |
| **settlement (決算・財政)** | 1,117 | 182 | 369 | 70 | 0 | 1,738 | 64.3% |
| **合計** | **4,420** | **696** | **1,372** | **364** | **106** | **6,958** | **63.5%** |

### アダプタ分類と境界線
- `ready` (4,420件): `kaigiroku_net`, `g_reiki`, `d1_law`, `joureikun`, `static` (直PDF/HTML), `official_document_index` (構造検証済PDF/Excel)
- `blocked` (364件): 北海道町村会 (`houmu.h-chosonkai.gr.jp`), `dbsr.jp`, `kaigiroku.net` 等の `robots.txt` Disallow による安全隔離
- `unsupported` (106件): ぎょうせい JSF POST動的型 (`legal-square.com`), ASP型 voices (`gijiroku.com`), 動画配信 (`discussvision.net`) 等
- `not_found` (1,372件): Web未公開または検索不能が確定した町村
- `needs_review` (696件): スキャン画像PDF（OCR未処理）、または広報誌要約のみ掲載の小規模町村

## Resolve (on-demand document lookup)

`resolve` は profile の入口URLから索引ページを取得（robots遵守・キャッシュ付き）し、同一hostの文書リンク（pdf/xls/doc/csv）をラベル付きで一覧化する。数値の抽出はしない（読み取りは利用者側AI/人/OCR）。

```bash
# 文書リストをJSONで取得
python3 -m source_profiles.cli resolve \
  --municipality "伊万里市" --prefecture "佐賀県" --kind budget --cache-dir /tmp/sp-cache

# 年度ハブ配下のサブページも辿る（既定8ページ、深さ2まで）
python3 -m source_profiles.cli resolve ... --follow-pages 8

# N番目の文書をダウンロードしてローカルパス+sha256を返す
python3 -m source_profiles.cli resolve ... --get 5
```

- statusが `ready` 以外でも動作するが `warnings` を添える。robots拒否・範囲外の `--get` は exit 2
- 取得したPDFは HttpClient キャッシュ（sha256キー）に保存され、再実行時はキャッシュヒットする

## Testing

```bash
python3 -m unittest source_profiles.tests.test_schema source_profiles.tests.test_cli source_profiles.tests.test_verify
ruff check source_profiles
mypy source_profiles
python3 -m source_profiles.cli validate --all
```
