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

Verify promotes `needs_review` g_reiki entries to `ready` only after a live fetch succeeds.

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
4. On success: set `verified_at` (UTC ISO8601 Z, now), `verified_by="verify --live"`, append `{url, observed_on, sha256, fetched_at}` to `evidence` (idempotent on `url+sha256`), set `status="ready"`, self-validate via `schema.validate_profile` before saving.

For `minutes/static` the index is checked for a council-scoped minutes document link (`.pdf` or label/URL contains 会議録/議事録 with council token). If none is found and `config.follow_link_regex` is set, the verifier follows at most 3 same-host HTML links whose label or URL (percent-decoded) matches the regex, depth 1 only, using `HttpClient` (robots/low rate/cache). The first follow page containing a council document promotes to `ready` and appends evidence for both the root index and the successful follow page (idempotent on `url+sha256`). No follow occurs when the regex is absent or invalid; invalid regex is a validation/verify error and the profile is not saved. `決算審査` etc. are excluded by choosing a minimal year regex such as `(令和|平成)(元|[0-9]+)年`.

Trust boundary:

- Only a live (or human) verification can promote to `ready`; observatory/preflight hints never auto-promote.
- URL changes or host drift cause safe stop (exit `2`, no rewrite).
- No tenant or URL guessing; entry URL is derived only from the stored `base_url`.
- `--offline` uses only the verified cache; missing cache is a failure, not a network fallback.

## Saga coverage (20)

- 15 g_reiki (`needs_review`, base_url verified via preflight + regulations-clusters live recheck): 唐津/鳥栖/伊万里/武雄/鹿島/小城/嬉野/神埼/上峰/玄海/有田/太良/佐賀/多久/白石
- 2 D1-Law (`unsupported`, index_url): みやき (ops-jg), 江北 (en3-jg)
- 1 Joureikun (`unsupported`, index_url): 大町
- 2 needs_review null: 吉野ヶ里 (JS), 基山 (旧HTML FRAME)

Evidence URLs host-match the entry URLs; no guessed tenants.

## Testing

```bash
python3 -m unittest source_profiles.tests.test_schema source_profiles.tests.test_cli source_profiles.tests.test_verify
ruff check source_profiles
mypy source_profiles
python3 -m source_profiles.cli validate --all --prefecture "佐賀県"
```
