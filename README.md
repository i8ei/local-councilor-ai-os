# local-councilor-ai-os

<p align="center">
  <img src="docs/assets/local-councilor-ai-os-og-1200x630.png" alt="地方議員AI運用OS — 議会議事録や条例、予算・決算、公開データを見立て・ツボ・手当てにつなぐ" width="900">
</p>

<p align="center">
  <strong>議員の時間を、住民さんのために。</strong><br>
  自治体の議事録・例規・予算データを手元に集約し、AIとObsidianで「1秒で過去答弁を探し、根拠ある質問を作る」ための仕事場キット。
</p>

<p align="center">
  <a href="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml"><img src="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <a href="https://github.com/i8ei/local-councilor-ai-os/releases/latest"><img src="https://img.shields.io/github/v/release/i8ei/local-councilor-ai-os" alt="latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Dependencies-Zero-green.svg" alt="Zero Dependencies">
</p>

---

## これは何？

地方議員の実務で最も時間を奪われる**「資料探し」「過去答弁の確認」「数字の検算」「出典の照合」を手元のPCとAIに肩代わりさせる**オープンソース（OSS）です。

自治体の公式ウェブサイトから議事録・条例・予算決算データを手元（SQLiteデータベース）に取り込み、[Obsidian](https://obsidian.md/) や AIコーディングエージェント（Claude Code / Codex / Antigravity 等）と連携して、**事実に基づいた強い質問や政策提案を素早く組み立てる**ことができます。

外部ライブラリへの依存はゼロ（Python 3.11+ 標準ライブラリのみ）。あなたのPC内だけで安全・軽快に完結します。

---

## こんな実務の悩みを解決します

| 従来の悩み (Before) | このOSを使うと (After) |
|---|---|
| 自治体の議事録検索システムが使いづらく、過去の町長・課長答弁を探すのに半日かかる | 手元のSQLite/FTS5から**1秒で全文検索**。該当発言と文脈を瞬時にリスト化 |
| 関連する条例や規則の条文を探し、最新の改正状況を追うのが大変 | 自治体の全例規を手元にインデックス化。**条番号やキーワードで即座に参照** |
| 予算書・決算書の数字が多く、手計算や電卓での検算や前年比較に追われる | **コマンド1発で検算**。歳入歳出の一致、款項目の突合差額ゼロを機械的に検証 |
| 一般質問の原稿作りで、根拠の裏取りや出典リンクの整理に追われる | **出典URL・根拠データ・反対論・副作用**が最初から揃ったObsidianノートを自動生成 |

---

## 3つのコア機能

### 1. 議事録・例規の爆速ローカル検索
過去数年〜十数年分の議事録（数万〜数十万件の発言）や例規集を手元のローカルDBに取り込みます。
Webサイトの重い検索画面を開くことなく、日本語のあいまい検索や部分一致（Trigram FTS5）で一瞬で過去答弁を引き出せます。

### 2. 予算・決算の自動検算
予算書・決算書のデータを構造化し、計算ミスや不整合がないかを機械的にチェックします。
- 歳入と歳出の総額一致チェック
- 款・項・目・節の階層合計突合
- 前年度比較・補正前後の差額検証

### 3. 「見立て → ツボ → 手当て」による質問・提案設計
住民相談や現場の課題を、単なる感情論や要望で終わらせず、行政が動かせる具体的な提案へと昇華させるワークフロー（[ツボ探し](workflows/policy-tsubo.md)）を標準装備しています。

```text
1. 見立て: 何がどこで詰まっているか？（事実と意見を分ける）
2. ツボ:   町・県・国のどの制度や予算のレバーを引けば動くか？
3. 手当て: 副作用・反対論・追跡指標まで織り込んだ質問・提言ノートを作成
```

---

## クイックスタート（3ステップで試す）

必要なものは **Python 3.11以上** と **Git** のみです。

```bash
git clone https://github.com/i8ei/local-councilor-ai-os.git
cd local-councilor-ai-os
```

### Step 1. 自分の自治体の公開状況をチェックする（Preflight）
まずは自治体の公式ホームページから、議事録・例規・予算・決算の入口がどこにあるかを自動診断します（※この時点ではPDFや文書本文はダウンロードしません）。

```bash
python3 -m bootstrap.cli.preflight \
  --prefecture '佐賀県' \
  --municipality '太良町' \
  --output /tmp/preflight.json
```

### Step 2. 議事録や例規を取り込む（Ingest）
診断結果に基づいて、手元のSQLiteデータベースへデータを取り込みます。

```bash
# 例：例規集を取り込む
python3 modules/regulations/vendor_greiki.py \
  --base-url 'https://www1.g-reiki.net/town.tara/' \
  --db /tmp/tara-regulations.db \
  --source-name '太良町例規集'
```

### Step 3. 検索してAIやObsidianで活用する
取り込んだデータはコマンドラインから即座に検索でき、AIエージェントに小さなコンテキストとして渡すことができます。

```bash
# 「空き家」に関する条文を検索
python3 -m modules.regulations.search \
  --db /tmp/tara-regulations.db \
  --query '空き家'
```

さらに本格的に運用する場合は、Obsidian Vaultと連携させて環境を診断します。

```bash
# 環境とVaultの準備状況を診断
python3 -m lcaios doctor --vault '/path/to/your/obsidian-vault'
```

詳しい導入ステップは [`setup.md`](setup.md) をご覧ください。

---

## 大切にしている設計原則（AIに任せないこと）

このOSは、AIに何でも丸投げするためのものではありません。

- **政策の判断は人間（議員）が行う**: AIは調査・構造化・検算の補助者です。
- **推測でデータを埋めない（Fail-closed）**: 見つからないURLや数字をAIがもっともらしく捏造しないよう、厳格な検証ゲートを設けています。
- **個人情報や秘密情報を守る**: 住民の個人情報や内部資料が公開用データに混入しないよう、機械的な安全スキャン機能を内蔵しています。
- **ゼロ外部依存**: `pip install` による環境汚染やバージョン破壊を避けるため、標準ライブラリのみで動作します。

---

## リポジトリ構成

```text
local-councilor-ai-os/
├── bootstrap/       # 自治体の基礎データ探索・入口診断 (preflight)
├── lcaios/          # OS制御層 (doctor / status / verify / backup)
├── modules/         # 各種データ処理モジュール
│   ├── minutes_db/        # 議事録の取込・FTS5全文検索
│   ├── regulations/       # 例規集の取込・条文検索
│   ├── budget_review/     # 予算書の構造化・自動検算
│   └── settlement_review/ # 決算書の突合・異常値検証
├── templates/       # Obsidian質問設計・政策提案テンプレート
├── workflows/       # 実務ワークフロー (ツボ探し等)
├── data-contracts/  # 各種データのスキーマ定義
└── source_profiles/ # 全国の自治体ソース確定プロファイル
```

---

## ドキュメント一覧

- [導入ガイド (`setup.md`)](setup.md)
- [議事録モジュール仕様 (`modules/minutes_db/README.md`)](modules/minutes_db/README.md)
- [例規モジュール仕様 (`modules/regulations/README.md`)](modules/regulations/README.md)
- [予算審査モジュール仕様 (`modules/budget_review/README.md`)](modules/budget_review/README.md)
- [決算審査モジュール仕様 (`modules/settlement_review/README.md`)](modules/settlement_review/README.md)
- [自治体プロファイル (`source_profiles/README.md`)](source_profiles/README.md)
- [実務ワークフロー (`way-of-working/README.md`)](way-of-working/README.md)
- [コントリビューションガイド (`CONTRIBUTING.md`)](CONTRIBUTING.md)

---

## 開発・テスト

```bash
# 全自動テストの実行 (480+ tests)
./run_tests.sh

# 静的解析・型チェック
ruff check .
mypy lcaios bootstrap modules source_profiles
```

## ライセンス

[MIT License](LICENSE)
