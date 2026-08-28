# Unreleased Changes

This document tracks changes in `main` that have not yet been released in a tagged version.
When tagging a release, move these notes to `RELEASE_NOTES/vX.Y.Z.md`.

## Features
- **way-of-working/ のEBPM体系への全面刷新**: 第11章を「働きかけの宛先設計（Advocacy Routing）」へ改訂し、「見立て・ツボ・手当て」の独自メタファーをエビデンス・ロジックモデル・政策提言・アウトカムKPIに基づく公的EBPM体系へ統一。
- **旧ワークフローと参照の整理**: 旧 `workflows/policy-tsubo.md` を廃止し、`workflows/03`, `04`, `setup.md`, `docs/`, `data-contracts/`, `templates/` の全参照を `workflows/ebpm-policy-design.md` および客観的な政策設計語彙へ統一。

## Fixes
- **`ready` の定義を種別ごとに分離し、機械が予算・決算に `ready` を付ける迂回を塞いだ**: `source_profiles/verify.py` は「予算・決算に汎用抽出器は無いので verify は ready を付けられない」と明記していたが、`tools/verify_budget_settlement_concurrent.py` がこれを迂回して `status: "ready"` を書き、さらに厳格な verifier と同じ `verified_by: "verify --live"` を刻んでいたため、**2,206 件が取込アダプタで実抽出した ready と区別できなくなっていた**。
  - 新ステータス **`document_confirmed`**（予算・決算のみ）を追加。実文書に到達し構造マーカーを確認した状態を表し、`ready` とは別に数える。provenance 要件（`verified_at`/`verified_by`/`adapter`/入口URL/`evidence`）は `ready` と同等。
  - 出自を分離: 予算・決算の文書構造検証は `verified_by: "verify --doc-structure"`。
  - データ移行: 予算 1,104・決算 1,102 を `document_confirmed` へ、Level 2 検証を経ていない scout 由来の 36 件（予算・決算 各18）を `needs_review` へ降格。
  - 集計の実態: `ready` 2,184（会議録・例規のみ）/ `document_confirmed` 2,206（予算・決算）。**予算・決算の `ready` は 0 件**で、実際に取り込んだ人が付与する。
  - 回帰テスト: 予算・決算の `ready` が機械由来（`verified_by` が `verify*` / `*scout`）でないこと、`document_confirmed` が予算・決算以外に付かないことをデータセット全体で検査。`verify` が予算・決算に `ready` を返さないことも検査。

## Documentation
- `source_profiles/README.md`: 種別ごとの `ready` 条件と付与主体を表で明示し、カバレッジ表を `ready` / `document_confirmed` の2列に分離（合算しない旨を明記）。
- `README.md` / `docs/ingestion-playbook.md`: 「4,426件が実文書検証済み（ready）」という合算表記を、保証の強さで分けた表記へ修正。
