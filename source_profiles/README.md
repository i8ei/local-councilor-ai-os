# source_profiles

Municipality source profiles: verified entry points for ingestion modules.

## Layout

```text
source_profiles/
  schema.py                          # validator (stdlib only)
  cli.py                             # validate / ingest-command
  __main__.py                        # python3 -m source_profiles.cli
  schema/source_profile.schema.json  # contract (draft-07, reference)
  municipalities/41-saga/*.json      # 1 file per municipality
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
    "minutes":     {"status": "not_evaluated", "adapter": null, "verified_at": null, "verified_by": null, "evidence": [], "notes": null},
    "regulations": {"status": "needs_review", "adapter": "g_reiki", "base_url": "https://www1.g-reiki.net/town.tara/", "verified_at": null, "verified_by": null, "evidence": [{"url": "https://www1.g-reiki.net/town.tara/reiki_menu.html", "observed_on": "http://www.town.tara.lg.jp/"}], "notes": "..."},
    "budget":      {"status": "not_evaluated", "adapter": null, "verified_at": null, "verified_by": null, "evidence": [], "notes": null},
    "settlement":  {"status": "not_evaluated", "adapter": null, "verified_at": null, "verified_by": null, "evidence": [], "notes": null}
  }
}
```

- `status`: `ready | needs_review | unsupported | not_found | blocked | not_evaluated`
- `adapter`: `null | kaigiroku_net | static | g_reiki | dbsr | voices | d1_law | joureikun | official_document_index`
- Entry keys are mutually exclusive: `g_reiki` requires `base_url`, `static` requires `index_url`, `kaigiroku_net` requires `tenant_url`. Multiple simultaneous entries are rejected.
- `ready` requires `verified_at` (UTC ISO8601, not future) + `verified_by` + adapter + entry URL + `evidence>=1`. `verified_at` violation or missing fields => validation error.
- Host check (anti-guess): whenever an entry URL exists, `evidence` must contain at least one entry whose `url` or `observed_on` shares the same host as the entry URL.

## Registry drift

`validate_profile` cross-checks `area_code_5 / prefecture / municipality / official_home_url` against `bootstrap/municipalities/registry.py`. Any mismatch is an error (official_home_url drift included).

## CLI

```bash
# validate all Saga profiles
python3 -m source_profiles.cli validate --all --prefecture "佐賀県"

# single profile
python3 -m source_profiles.cli validate --profile source_profiles/municipalities/41-saga/41441-tara.json

# generate ingest command (does not execute)
python3 -m source_profiles.cli ingest-command --municipality "太良町" --prefecture "佐賀県" --kind regulations --limit 3
# -> # NEEDS LIVE VERIFICATION
# -> python3 modules/regulations/vendor_greiki.py --base-url https://www1.g-reiki.net/town.tara/ --db /tmp/41441-reg.db --source-name "太良町例規集" --limit 3

# unsupported adapter => exit 2 with next action
python3 -m source_profiles.cli ingest-command --municipality "江北町" --prefecture "佐賀県" --kind regulations
```

`validate` prints a JSON report to stdout and exits `2` on errors. `ingest-command` prints the command (plus a leading `# NEEDS LIVE VERIFICATION` comment when `needs_review`) and exits `2` for unsupported adapters.

## Verify (live re-check with HttpClient)

Verify promotes `needs_review`/`not_evaluated` entries to `ready` only after a live fetch succeeds AND a real extraction probe yields at least one record.

```bash
# verify a single municipality live (low rate, robots respected, HttpClient cache)
python3 -m source_profiles.cli verify \
  --municipality "太良町" --prefecture "佐賀県" \
  --kind regulations --cache-dir /tmp/sp-verify-cache
# -> {"municipality": "太良町", "kind": "regulations", "adapter": "g_reiki",
#      "result": "verified", "status_before": "needs_review", "status_after": "ready", ...}

# offline: use only verified cache
python3 -m source_profiles.cli verify \
  --municipality "太良町" --prefecture "佐賀県" \
  --kind regulations --cache-dir /tmp/sp-verify-cache --offline
```

Steps inside `verify_profile` (injectable `client`, no guessing):

1. Derive entry URL as `base_url + "reiki_menu.html"` (fixed g_reiki spec, same as `vendor_greiki.py`).
2. Fetch via `lcaios.http.HttpClient` (`REGULATIONS_USER_AGENT`, 1.5s throttle, robots check, SHA256 cache).
3. Fail closed on `RobotsDenied/OfflineCacheMiss/FetchError`, host drift (`final_url` host != `base_url` host), or structure mismatch (HTML must contain `reiki_*` or `例規` markers). Status stays `needs_review`; exit `2`.
4. Extraction probe: call the real ingest extractor (`modules/regulations/vendor_greiki.py` for g_reiki, `modules/minutes_db/adapters/*` for minutes) against exactly ONE document reached through the recorded entry URL. Promotion rules:
   - **R1 — promotion gate**: `ready` is granted ONLY when the prior status is `needs_review` or `not_evaluated`. For any other prior status (`blocked`/`unsupported`/`not_found`/already-`ready`) the status is left untouched; the report result is still `verified` when the checks passed, but the reason states the promotion was withheld because of the prior status.
   - **R2 — structural identification**: `ready` requires the probe to extract at least one STRUCTURALLY IDENTIFIED record via the real adapter code — minutes: ≥1 speech with a non-empty `speaker`; regulations: ≥1 article with a non-empty `article_no`. Fallback paragraph/document chunks (no speaker/article_no) do not count: both extractors deliberately fall back so ingestion never loses content, but a newsletter behind a 会議録 label is not verbatim minutes. Reachability alone is not ingest evidence.
   - **R3 — robots denial on the probe**: status becomes `blocked` (including downgrade from `ready`), index evidence is recorded and a note marks the bodies as robots-restricted.
   - **R4 — read but nothing identifiable**: if the probed document was read but yielded no structurally identified records (e.g. 議会だより newsletter prose, or an image-only page), the status becomes `needs_review` (never `ready`) with a note saying what was probed and that no speaker-attributed speeches (or numbered articles) were found.
   - **R5 — probe inconclusive or unreachable**: if the probe fails for any other reason the status stays exactly as it was and the report result is `failed`. No promotion, no demotion. Two distinct causes are named in the reason: (a) the document was unreachable (404, network error, structure mismatch), or (b) the adapter could not READ the probed document at all — e.g. `pdf_cached_pdftotext_unavailable` when the pdftotext binary is missing — in which case the reason names the adapter status and local tooling must be fixed before re-running. Case (b) must never be reported as "yielded no records": only an adapter status classified as a real extraction (`extracted`, `html_no_text`, `text_without_segments`, `pdf_no_text`) lets a zero record count demote to `needs_review` under R4.
   On promotion: set `verified_at` (UTC ISO8601 Z, now), `verified_by="verify --live"`, append `{url, observed_on, sha256, fetched_at}` to `evidence` (idempotent on `url+sha256`), self-validate via `schema.validate_profile` before saving.

For `minutes/static` the index is checked for a council-scoped minutes document link (`.pdf` or label/URL contains 会議録/議事録 with council token). If none is found and `config.follow_link_regex` is set, the verifier follows at most 3 same-host HTML links whose label or URL (percent-decoded) matches the regex, depth 1 only, using `HttpClient` (robots/low rate/cache). The first follow page containing a council document selects the probe document, and evidence covers both the root index and the successful follow page (idempotent on `url+sha256`). No follow occurs when the regex is absent or invalid; invalid regex is a validation/verify error and the profile is not saved. `決算審査` etc. are excluded by choosing a minimal year regex such as `(令和|平成)(元|[0-9]+)年`.

For `minutes/kaigiroku_net` the stored `tenant_url` is fetched and the probe runs only if the host stays on `ssp.kaigiroku.net` and the page carries kaigiroku entrance markers.

For `minutes/dbsr` the stored `index_url` (must be a `*.dbsr.jp` URL containing `/index.php`) is fetched once and the probe runs only if the host stays on `*.dbsr.jp` and the page carries a minutes hint (会議録/議事録/定例会/臨時会/本会議) together with either a same-host `/index.php/<id>` detail link or the observed query-list form (`/index.php/?QueryType=New&Template=List...` with a meeting label). After the entrance check passes, the verifier probes the first same-host meeting link through the real dbsr adapter (`CacheTier.DOCUMENT`); if the probe is robots-denied the entry is set to `blocked` (R3), any other fetch error leaves the status unchanged (R5), and a reachable body without speaker-attributed speeches stays/becomes `needs_review` (R4). Observed Saga dbsr tenants allow only the bare `/index.php` index in `robots.txt` and block meeting bodies/details, so ingestion requires the councilor/user to obtain municipality permission (out of scope for automated ingestion). A bare or maintenance page on the vendor host does not promote.

All adapters share the same promotion gate: `ready` only from `needs_review`/`not_evaluated`, and only after the extraction probe succeeded (R1–R5 above).

Trust boundary:

- Only a live (or human) verification can promote to `ready`; observatory/preflight hints never auto-promote.
- URL changes or host drift cause safe stop (exit `2`, no rewrite).
- No tenant or URL guessing; entry URL is derived only from the stored `base_url`.
- `--offline` uses only the verified cache; missing cache is a failure, not a network fallback.

## Saga coverage（佐賀20市町の確定状況）

80入口（20市町×議事録/例規/予算/決算）は main `c542b40`（2026-08-23）時点で次のとおりすべて判定済み:

- **議事録（minutes）**: ready 14（static 9: 武雄/鹿島/嬉野/基山/有田/大町/江北/白石/太良 ＋ kaigiroku_net 5: 唐津/鳥栖/多久/伊万里/玄海）/ blocked 4（dbsr 3: 神埼/上峰/みやき — 索引は見えるが本文がrobots制限。voices 1: 佐賀）/ needs_review 1（吉野ヶ里 — JS描画）/ unsupported 1（小城 — db-search.com）
- **例規（regulations）**: ready 17（g_reiki 15 ＋ d1_law互換 1: 基山 ＋ joureikun 1: 大町）/ blocked 1（江北 d1_law — robots到達不可につきfail-closed）/ needs_review 1（吉野ヶ里 — JS描画）/ unsupported 1（みやき — opensearch型JS＋POST必須）
- **予算（budget）**: ready 18 / blocked 1（白石 — robots `Disallow: /var/`、実プローブで確認済み）/ not_found 1（玄海 — 探索経路は記録済み）
- **決算（settlement）**: ready 18 / blocked 1（白石）/ not_found 1（大町 — 総括表のみ確認）

予算・決算は adapter=`official_document_index`＋`index_url` で保持する。evidence の `sha256`/`fetched_at` は調査封筒でなく HttpClient キャッシュのメタデータ（on-disk真実）から採用した。取込は設計上スコープ外: 数値の抽出は `modules/budget_review` / `modules/settlement_review` のCSV契約に対して利用者側のAI/人/OCRが行い、汎用抽出器は提供しない。

注: 記録時点でopenしていた PR #44 は、逐語会議録が未公開であることを確認した上で有田のminutesを `ready` から `needs_review` へ降格させる提案。

## Resolve (on-demand document lookup)

`resolve` は profile の入口URLから索引ページを取得（robots遵守・キャッシュ付き）し、同一hostの文書リンク（pdf/xls/doc/csv）をラベル付きで一覧化する。数値の抽出はしない（読み取りは利用者側AI/人/OCR）。

```bash
# 文書リストをJSONで取得
python3 -m source_profiles.cli resolve \
  --municipality "伊万里市" --prefecture "佐賀県" --kind budget --cache-dir /tmp/sp-cache

# 年度ハブ配下のサブページも辿る（既定8ページ、深さ2まで）
#   ラベルが年度パターンのリンク → 文書0なら「当初/概要/決算報告」ラベルの内容ページ1つを追従
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
python3 -m source_profiles.cli validate --all --prefecture "佐賀県"
```
