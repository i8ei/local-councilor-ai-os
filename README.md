# local-councilor-ai-os

<p align="center">
  <img src="docs/assets/local-councilor-ai-os-og-1200x630.png" alt="地方議員AI運用OS" width="900">
</p>

<p align="center">
  <strong>議員の時間を、住民さんのために。</strong><br>
  自治体の議事録・例規・予算決算データを手元に集約し、AIとObsidianで「データに基づく政策提言（EBPM）」を組み立てる仕事場キット。
</p>

<p align="center">
  <a href="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml"><img src="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <a href="https://github.com/i8ei/local-councilor-ai-os/releases/latest"><img src="https://img.shields.io/github/v/release/i8ei/local-councilor-ai-os" alt="latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Dependencies-Zero-green.svg" alt="Zero Dependencies">
</p>

---

## 概要

自治体の公式ウェブサイトから議事録・条例・予算・決算データを手元の SQLite データベースに取り込み、Markdown エディタ（Obsidian 等）や AI（Claude Code / Codex / Antigravity 等）と連携して、**事実と数字に基づいた建設的な政策提言（EBPM）を素早く組み立てる**ためのオープンソース基盤です。

外部ライブラリへの依存はゼロ（Python 3.11+ 標準ライブラリのみ）。PC内だけで安全・軽快に完結します。

---

## 解決する実務課題

| 従来の悩み (Before) | このOSを使うと (After) |
|---|---|
| 自治体の検索システムが使いづらく、過去答弁の確認に時間がかかる | 手元の SQLite/FTS5 から**1秒で全文検索**。該当発言と文脈を瞬時にリスト化 |
| 関連条例の最新条文や改正経緯を追うのが大変 | 自治体の全例規をインデックス化。**条番号やキーワードで即座に参照** |
| 予算書・決算書の数字確認や前年比較に追われる | **コマンド1発で検算**。歳入歳出一致・款項目突合の差額ゼロを機械検証 |
| 決算書から不用額常態化や連続繰越を見つけるのが大変 | **0.1秒で多年度を横断分析**。不用額・未収金・連続繰越を自動抽出 |
| 議会質問が感情論や単なる要望になり「検討します」と流される | **決算推移・過去答弁・改善策・次回検証KPI**が揃ったEBPM質問カードを作成 |

---

## 4つのコア機能

### 1. 全国 1,741 自治体の公式データカタログ
全国の議事録・例規集・当初予算・決算の公式入口を構造化したプロファイル（`source_profiles/`）を内蔵。**4,426 件（63.6%）の入口が実文書検証済み（ready）** です。

### 2. 議事録・例規の爆速ローカル検索
数万〜数十万件の発言記録や例規集を SQLite/FTS5（Trigram）でインデックス化。0.1秒で過去答弁や現行条文を引き出せます。

### 3. 予算・決算の自動検算と多年度ブリッジ分析
決算データを横断し、**「不用額の常態化（前年踏襲）」「連続繰越」「歳入の未収金・不納欠損」** を一瞬で抽出。9月決算審査の指摘を翌年度予算の適正化へと直結させます。

### 4. 実践 EBPM（証拠に基づく政策立案）質問設計
感情論や思いつきではなく、**エビデンス（事実） → ロジックモデル（要因分析） → 政策提言（アクション） → アウトカム指標（検証KPI）** の4段階で行政と建設的に対話できる質問カードを設計します（[ワークフロー](workflows/ebpm-policy-design.md) / [テンプレート](templates/ebpm-question-card.md) / [実例](templates/examples/)）。

---

## クイックスタート

必要なものは **Python 3.11以上** と **Git** のみです。

```bash
git clone https://github.com/i8ei/local-councilor-ai-os.git
cd local-councilor-ai-os
```

### Step 1. 自治体の公式データ入口を確認する（Preflight / Profile）
```bash
# 自治体プロファイルの検証
python3 -m source_profiles.cli validate --profile source_profiles/municipalities/41-saga/41441-tara.json
```

### Step 2. 例規や議事録を取り込む（Ingest）
```bash
# 例：例規集を取り込む
python3 modules/regulations/vendor_greiki.py \
  --base-url 'https://www1.g-reiki.net/town.tara/' \
  --db /tmp/tara-regulations.db \
  --source-name '太良町例規集'
```

### Step 3. 手元のデータベースを検索する（Search）
```bash
# 条文検索
python3 -m modules.regulations.search --db /tmp/tara-regulations.db --query '空き家'
```

### Step 4. 多年度決算の課題を抽出する（Bridge）
```bash
# 不用額常態化・連続繰越・未収金を自動分析
python3 -m modules.settlement_review.bridge --db /path/to/settlement_multi.db --min-years 2
```

### Step 5. 見取り図ノート（MOC）を生成する
```bash
# ワークスペース内に自治体データ見取り図ノートを自動生成
python3 -m lcaios dashboard --vault '/path/to/your/markdown-workspace' --write-vault
```

---

## 設計原則

- **政策の判断は人間（議員）が行う**: AIは調査・構造化・検算の補助者です。
- **推測でデータを埋めない（Fail-closed）**: 見つからないURLや数字をAIが捏造しないよう、厳格な検証ゲートを設けています。
- **個人情報や秘密情報を守る**: 住民の個人情報や内部資料が公開用データに混入しないよう配慮します。
- **ゼロ外部依存**: `pip install` による環境破壊を避けるため、標準ライブラリのみで動作します。

---

## リポジトリ構成

```text
local-councilor-ai-os/
├── source_profiles/ # 全国の自治体公式データプロファイル（1,741自治体）
├── modules/         # 各種データ処理モジュール（議事録・例規・予算・決算）
├── workflows/       # 実務ワークフロー（EBPM質問設計・決算審査等）
├── templates/       # 質問設計カード・政策提案テンプレート
├── data-contracts/  # 各種データのスキーマ定義
└── lcaios/          # OS制御層（doctor / status / dashboard）
```

---

## ドキュメント一覧

- [EBPM質問・政策設計ワークフロー (`workflows/ebpm-policy-design.md`)](workflows/ebpm-policy-design.md)
- [EBPM質問カードテンプレート (`templates/ebpm-question-card.md`)](templates/ebpm-question-card.md)
- [EBPM質問カード公式実例集 (`templates/examples/`)](templates/examples/)
- [決算審査モジュール仕様 (`modules/settlement_review/README.md`)](modules/settlement_review/README.md)
- [議事録モジュール仕様 (`modules/minutes_db/README.md`)](modules/minutes_db/README.md)
- [例規モジュール仕様 (`modules/regulations/README.md`)](modules/regulations/README.md)
- [自治体プロファイル基盤 (`source_profiles/README.md`)](source_profiles/README.md)
- [導入ガイド (`setup.md`)](setup.md)
- [コントリビューションガイド (`CONTRIBUTING.md`)](CONTRIBUTING.md)

---

## 開発・テスト

```bash
# 全自動テストの実行 (560 tests)
./run_tests.sh

# 静的解析・型チェック
ruff check .
mypy .
```

## ライセンス

[MIT License](LICENSE)
