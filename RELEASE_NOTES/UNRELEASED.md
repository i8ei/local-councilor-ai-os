# Unreleased Changes

This document tracks changes in `main` that have not yet been released in a tagged version.
When tagging a release, move these notes to `RELEASE_NOTES/vX.Y.Z.md`.

## Features
- **EBPM（証拠に基づく政策立案）質問設計ワークフロー**: 従来の「見立て・ツボ・手当て」を公的なEBPM体系（エビデンス → ロジックモデル → 政策提言 → アウトカムKPI）に再編し、`workflows/ebpm-policy-design.md` および `templates/ebpm-question-card.md` を追加。
- **不用額・未収金の4大類型パターンと3大原則**: 自治体財政の典型要因（国県随伴型、受診乖離型、補正代替型、滞納固定化型）の改善アプローチをワークフローに明記。
- **決算ブリッジからのEBPMカード一発生成 & 議事録自動照合**: `modules/settlement_review/bridge.py` に `--format ebpm-card`、`--minutes-db`、`--ebpm-out-dir` を追加。不用額常態化科目と過去答弁を自動突合したカードをワンコマンドで生成可能に。
- **EBPM質問設計カードの公式実例同梱**: `templates/examples/ebpm-card-disability-welfare.md`（障害福祉費）および `templates/examples/ebpm-card-fixed-asset-tax.md`（固定資産税）を追加。

## Fixes
- None currently.

## Documentation
- `README.md`: コア機能4を「EBPMによる質問・提案設計」に更新。
