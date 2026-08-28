# Unreleased Changes

This document tracks changes in `main` that have not yet been released in a tagged version.
When tagging a release, move these notes to `RELEASE_NOTES/vX.Y.Z.md`.

## Features

### 🏛️ 全国自治体プロファイル 総 ready 4,420 件（63.5%）到達
- 全国 1,741 自治体 × 4 種別（計 6,958 エントリ）のソースプロファイルにおいて、実エビデンスに基づく検証済み（`status: "ready"`）が **4,420 件（63.5%）** に到達。
  - **会議録 (`minutes`)**: 1,073 ready (61.6%) / 68 needs_review / 135 blocked / 47 unsupported
  - **条例・例規 (`regulations`)**: 1,111 ready (63.8%) / 260 needs_review / 87 blocked / 59 unsupported
  - **当初予算 (`budget`)**: 1,119 ready (64.4%) / 186 needs_review / 72 blocked / 0 unsupported
  - **決算・財政 (`settlement`)**: 1,117 ready (64.3%) / 182 needs_review / 70 blocked / 0 unsupported

### 📑 予算・決算 Level 2 深掘り文書構造検証 (`tools/verify_budget_settlement_concurrent.py`)
- ランディングページから PDF / Excel（『財政状況資料集』等）への 1〜2 ホップ探索および構造マーカー（歳入・歳出・款・項・目・決算額等）自動検出を実装。
- **高速化 & 排他制御**: `pdftotext -l 15`（先頭15ページ制限・15秒タイムアウト）と XLSX 先頭500セル制限により処理時間を大幅短縮。自治体パス単位のタスク化によりマルチスレッド書き込み競合を防止。
- 全国走査により予算・決算プロファイル **1,489 件を ready へ昇格**。

### 📜 D1-Law（第一法規）直リンク形式および拡張子対応 (`modules/regulations/vendor_d1law_reiki.py`)
- フレームを使用せず `<a href="<id>/<id>_j.html">` 形式で直接条文へリンクしている自治体（江別市、当別町、留寿都村等）に対応する `_DIRECT_J_RE` を追加。
- `reiki.htm` / `reiki.html` の両拡張子フォールバックに対応。

### 🛡️ Fail-closed に基づく robots.txt 制限サイトの安全隔離
- 北海道町村会（`houmu.h-chosonkai.gr.jp` 40件）、`dbsr.jp`、`kaigiroku.net` などの robots.txt Disallow サイトを自動検知し、`status: "blocked"` へ正常隔離（計 364 件）。外部サイトへの破壊的リクエストを防止。

### 🔍 静的議事録・条例の 1〜2 ホップ探索
- 静的議事録・条例ページから年度別・定例会別の会議録インデックスを追跡し、実体文書が確認できた 27 自治体の会議録を ready 昇格。
- `voices`（`gijiroku.com` ASP型 16件）などの未対応アダプタを `unsupported` へ整理。

## Fixes
- `modules/regulations/vendor_d1law_reiki.py`: 探索ページ上限到達時に refs が抽出済みであれば例外を送出せず refs を返すよう修正。
- `modules/regulations/vendor_greiki.py` / `source_profiles/verify.py`: `reiki_menu.html` 不在時の `reiki.html` フォールバック追加。

## Tests & Quality
- **全 560 テスト合格**（`./run_tests.sh` exit 0、ruff / mypy エラー 0 件）
