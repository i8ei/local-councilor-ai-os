# Unreleased — v0.2候補

OS制御層のコードと自動テストは`main`へ反映済み。正式なv0.2リリース前に、9月決算審査と次回予算審議での実戦検証を残す。

## Added

- Added the reusable "ツボ探し" workflow for turning a local problem into a fact-based 見立て, identifying the most effective municipal, prefectural, or national lever, and drafting a 手当て with side effects, objections, follow-up indicators, and exit conditions.
- Added `policy-issue` and `policy-pr` templates and data contracts so installed users can run the workflow in their own municipalities without publishing local case data.
- Added the read-only `lcaios` OS control plane:
  - `doctor` combines environment diagnosis, readiness, and one recommended next command.
  - `status` derives foundation, scaffold, profile, and Tier 1 readiness from existing manifests and SQLite artifacts.
  - `freshness` evaluates `fresh / due / stale / unknown` per official source without network access.
  - `verify output` detects internal wikilinks and paths, unverified markers, secret-like values, hidden comments, and invisible characters without editing or publishing.
  - `verify database` checks SQLite integrity and major/minor schema compatibility.
  - `backup database` creates a verified non-overwriting SQLite snapshot.
  - `restore database` requires an explicit backup SHA-256 and preserves the prior target as `.previous-*`.
  - `generated-files` lists only manifest-declared artifacts and never deletes them.
- Added bootstrap `--manifest-dir` output with source revision, versions, scope, artifact hashes, checks, warnings, failure recording, and e-Stat AppId redaction.
- Added data contracts for run manifests, instance locators, freshness, information classification, and schema compatibility.
- Added source registry 1.2 with source-specific freshness policies and the Soumu municipal fiscal source.
- Added an explicit prompt-injection boundary: external documents are data, not instructions. Context packs can carry `source_content_policy`.
- Added a detect/ingest/live-verification compatibility table for minutes adapters.
- Added shared Ruff and mypy configuration in `pyproject.toml`, with CI gates covering all importable packages.

## Changed

- Onboarding manifests now include `run_type: onboarding` for the shared manifest reader.
- The public-output review template and workflow now invoke the deterministic output safety scanner before human review.
- Setup and README documentation now use the standard path `doctor → onboarding → bootstrap → status`.
- Converted `modules/` into importable Python packages and standardized module commands on `python3 -m modules.<package>.<command>`.
- Rewrote the README introduction around the purpose, practical changes, and human decision boundary before technical details.

## Fixed

- Closed SQLite connections deterministically and made `ResourceWarning` fail the test suite.
- Centralized read-only SQLite URI construction and added allowlists for dynamic SQL identifiers.

## Safety

- All status, doctor, freshness, generated-file inventory, and verification commands are read-only.
- Output verification never prints detected secret values.
- Backup and recovery reject symlinks; recovery requires a confirmed SHA-256 and keeps the previous database.
- A recent rebuild is not treated as proof that the source period is current.

## Verified

- Verified all 181 tests and the settlement reconciliation gates.
- Verified Ruff and mypy across 105 source files.
- Verified offline `bootstrap → manifest → status` with nine normalized indicators and `tier1_data_ready: ready`.
- Verified per-source freshness against saved e-Stat and Soumu provenance.
- Verified public-output clean/blocked examples.
- Verified SQLite `verify → backup → restore → verify` against the Tara Tier 1 database.
- Verified `doctor` against an existing Obsidian Vault, including the Claude Code/Codex selection branch.
