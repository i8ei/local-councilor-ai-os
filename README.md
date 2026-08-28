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

## データ構造: 2つの階層

```text
🏛【第1層: マクロの土台（全自動・手作業ゼロ）】
  ・国勢調査（e-Stat API）＋ 総務省決算データ（全国XLSX）
  ・コマンド一発（`python3 -m bootstrap.cli <自治体名>`）で、全国1,741自治体の
    「人口構造（年少・生産年齢・高齢化率）・財政力指数・決算収支」の確実な基礎データを構築。

🔍【第2層: ミクロの個別深掘り（自治体固有・議員の関心ごと）】
  ・自治体の「議事録PDF（過去答弁）」「例規集（条例）」「個別事業の決算内訳」
  ・各議員が関心のある科目（道路維持、子育て支援、特定補助金など）を取り込み、
    過去答弁と照合した「EBPM質問・政策設計カード」を作成。
```

---

## 4つのコア機能

### 1. 全国 1,741 自治体の公式データカタログ & ブートストラップ
全国の議事録・例規集・当初予算・決算の公式入口を構造化したプロファイル（`source_profiles/`）を内蔵。`python3 -m bootstrap.cli <自治体名>` を叩くだけで、国勢調査・総務省の公式指標と各自治体の取込コマンドを即座に提示します。

### 2. 議事録・例規の爆速ローカル検索
数万〜数十万件の発言記録や例規集を SQLite/FTS5（Trigram）でインデックス化。0.1秒で過去答弁や現行条文を引き出せます。

### 3. 予算・決算の自動検算と多年度ブリッジ分析
総務省ポータルや決算カード（Excel）から決算データを自動抽出し、**「不用額の常態化（前年踏襲）」「連続繰越」「歳入の未収金・不納欠損」** を一瞬で抽出。差額ゼロの機械検証をパスした確実なデータを提供します。

### 4. 実践 EBPM（証拠に基づく政策立案）質問設計
感情論や思いつきではなく、**エビデンス（事実・過去答弁） → ロジックモデル（要因分析） → 政策提言（アクション） → アウトカム指標（検証KPI）** の4段階で行政と建設的に対話できる質問カードを設計します（[ワークフロー](workflows/ebpm-policy-design.md) / [テンプレート](templates/ebpm-question-card.md) / [実例](templates/examples/)）。

---

## クイックスタート

必要なものは **Python 3.11以上** と **Git** のみです（外部ライブラリのインストール不要）。

```bash
git clone https://github.com/i8ei/local-councilor-ai-os.git
cd local-councilor-ai-os
```

### Step 1. 自治体の基礎データを取り込む（Bootstrap）
```bash
# 例：太良町の基礎データ（人口ピラミッド・財政指標・決算収支）を一括取得
python3 -m bootstrap.cli 太良町
```

### Step 2. 例規や議事録を取り込む（Ingest）
```bash
# 例：太良町例規集を取り込む
python3 -m modules.regulations.vendor_greiki \
  --base-url "https://www1.g-reiki.net/town.tara/" \
  --db /tmp/tara-regulations.db

# 例：太良町議会の議事録PDFを取り込む（親見出し・会議名を自動結合）
python3 -m modules.minutes_db.ingest \
  --adapter static \
  --config /path/to/minutes_config.json \
  --db /tmp/tara-minutes.db
```

### Step 3. 複数年度の決算データを自動取得・検算する（Settlement）
```bash
# 総務省ポータルから過去3年度分の決算データを自動取得・CSV変換・検算
python3 -m modules.settlement_review.vendor_soumu \
  --municipality "太良町" \
  --years 2022 2023 2024 \
  --db /tmp/tara-settlement.db
```

> [!TIP]
> **💡 自分のまちの「完全な予算・決算DB」を育てる**  
> 全国の自治体予算書・決算書は、様式や記載粒度が千差万別です。  
> 本OSでは、総務省データから「マクロの決算DB（款・項）」を自動構築する基盤と、1円のズレも許さない「自動検算ツール（`verify_totals`）」を用意しています。  
> 個別事業（目・節）の最深部データは、AIと一緒にご自身のまちのデータベースを手元で育ててみてください。正しい型と検算ツールを活用することで、差額ゼロの確かなデータベースを一歩ずつ整えていくことができます。

### Step 4. EBPM質問・政策設計カードを自動生成する（Bridge）
```bash
# 決算の課題（不用額・未収金）と議事録の過去答弁を照合した質問カードを出力
python3 -m modules.settlement_review.bridge \
  --db /tmp/tara-settlement.db \
  --minutes-db /tmp/tara-minutes.db \
  --municipality "太良町" \
  --format ebpm-card \
  --ebpm-out-dir ~/my-vault/EBPM_Cards
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
