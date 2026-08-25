# local-councilor-ai-os

<p align="center">
  <img src="docs/assets/local-councilor-ai-os-og-1200x630.png" alt="地方議員AI運用OS — 議会議事録や条例、予算・決算、公開データを見立て・ツボ・手当てにつなぐ" width="900">
</p>

<p align="center">
  <strong>議員の時間を、住民さんのために。</strong><br>
  調査・裏取り・質問設計・答弁後の追跡を、AI・Obsidian・SQLiteで支える仕事場キット。
</p>

<p align="center">
  <a href="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml"><img src="https://github.com/i8ei/local-councilor-ai-os/actions/workflows/test.yml/badge.svg" alt="test"></a>
  <a href="https://github.com/i8ei/local-councilor-ai-os/releases/latest"><img src="https://img.shields.io/github/v/release/i8ei/local-councilor-ai-os" alt="latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+">
</p>

---

地方議員の仕事では、住民さんの話を聞き、現場を見て、問いを立て、判断し、その後を見届ける時間が重要です。一方で、資料探し、過去答弁の確認、数字の転記、出典の照合にも多くの時間がかかります。

`local-councilor-ai-os` は、こうした**「探す・確かめる・つなぐ・追いかける」作業をAIとデータベースに手伝わせる**ためのオープンソースです。AIに政策判断を任せるのではなく、判断に必要な根拠を揃え、議員本人が説明できる形で残します。

## できること

| 仕事 | このOSが支援すること |
|---|---|
| 自治体の基礎調査 | 自治体名から標準地域コード、国勢調査指標、財政資料を取得し、出典付きSQLiteを構築 |
| 資料の入口探し | 議事録、例規、予算、決算の現在の公式入口を少量HTMLで確認 |
| 議事録・例規の検索 | 取込データをSQLite / FTS5で検索し、AIへ渡す小さなcontext packを生成 |
| 予算・決算の確認 | 歳入歳出、前年度比較、補正前後、差額ゼロなどを検算 |
| 質問・提案の設計 | 根拠、反対論、副作用、確認期限をつないだ実務ノートを作成 |
| 答弁後の追跡 | 答弁で終わらず、予算化、事業化、実施、検証まで記録 |
| 公開前の確認 | 内部リンク、絶対パス、秘密値候補、未検証印などを機械的に走査 |

## まず5分で試す

必要なものは **Git**、**Python 3.11以上**、インターネット接続です。ランタイムの外部依存パッケージはありません。

```bash
git clone https://github.com/i8ei/local-councilor-ai-os.git
cd local-councilor-ai-os

python3 -m bootstrap.cli.preflight \
  --prefecture '佐賀県' \
  --municipality '太良町' \
  --output /tmp/municipality-preflight.json \
  --cache-dir /tmp/municipality-preflight-cache
```

このコマンドは、自治体の公式ホームページから次の入口を探し、結果をJSONへ保存します。

- 議会議事録
- 例規
- 予算資料
- 決算資料

資料本文、PDF、外部ベンダーのデータベースは取得しません。見つからない場合も推測で埋めず、`unsupported_vendor`、`unknown_structure`、`robots_blocked`、`source_not_found` などの理由を残します。

<details>
<summary>返ってくる結果の構造例（クリックで展開）</summary>

`--output` に指定したJSONには、自治体ごとに4種の入口の判定が入ります。`ready` は現在の公式ページから入口を確認できた状態、`needs_attention` は理由付きで止めた状態です。以下は形式を示すために省略・抽象化した例で、特定自治体の実行結果ではありません。

```json
{
  "schema_version": 1,
  "test_type": "municipality_source_preflight",
  "generated_at": "<ISO 8601 timestamp>",
  "status": "needs_attention",
  "prefecture": "<都道府県名>",
  "municipality_count": 1,
  "results": [
    {
      "municipality": "<自治体名>",
      "prefecture": "<都道府県名>",
      "area_code_5": "<5桁コード>",
      "official_home_url": "<公式ホームURL>",
      "status": "needs_attention",
      "pages_fetched": 8,
      "sources": {
        "minutes": {
          "status": "ready",
          "adapter": "<対応adapter>",
          "index_url": "<公式の議事録入口URL>",
          "reason": "現在の公式ページから対応adapterの入口を確認した"
        },
        "regulations": { "status": "ready", "reason": "<確認理由>" },
        "budget": { "status": "unknown_structure", "reason": "<停止理由>" },
        "settlement": { "status": "source_not_found", "reason": "<停止理由>" }
      },
      "documents_downloaded": 0,
      "database_created": false
    }
  ]
}
```

`status` の意味と次の行動は [preflight結果の読み方](bootstrap/README.md#preflight結果の読み方) にまとめています。`ready` でない入口も失敗ではなく、次の調査先を示すヒントです。

</details>

> [!NOTE]
> このpreflightは、データ探索機能を安全に試すための入口です。OS全体の運用にはObsidianが必要です。

## OSとして導入する

### 前提

- Python 3.11以上
- [Obsidian](https://obsidian.md/)
- Claude Code または Codex
- ObsidianとAIが協働する基盤環境（推奨: [`claude-obsidian-setup`](https://github.com/i8ei/claude-obsidian-setup)）
- 全国共通データをオンライン取得する場合は e-Stat API のアプリケーションID

既存のObsidian Vaultを変更せずに、まず現在地を診断します。

```bash
python3 -m lcaios doctor --vault '/absolute/path/to/vault'
```

`doctor` は読み取り専用です。導入状態、profile、データ、鮮度を確認し、**次に実行すべき1コマンドだけ**を示します。

標準の導入順は次のとおりです。

```text
doctor
  ↓
onboarding diagnose / plan / scaffold / verify
  ↓
bootstrap
  ↓
status
```

詳しい手順は [`setup.md`](setup.md) を参照してください。既存Vaultではノートやフォルダを自動で移動・改名せず、確認した役割対応を別ファイルへ保存します。

## 自治体データ基盤を作る

全国共通の統計・財政指標が必要な場合は、自治体名からSQLiteと `authority_map.yaml` を作成できます。

```bash
export ESTAT_APPID='<your-app-id>'

python3 -m bootstrap.cli '自治体名' \
  --prefecture '都道府県名' \
  --out-dir '/path/to/output' \
  --manifest-dir '/absolute/path/to/vault/.local-councilor-ai-os/runs/bootstrap'
```

生成される主なもの:

- `municipality.db` — 出典・取得時点・定義を持つSQLite検索層
- `authority_map.yaml` — 指標と用途ごとに、参照すべき公式資料とDB位置を示す裁定表
- run manifest — 生成物、SHA-256、検証結果、警告を記録

値は必ず **`value / as_of / definition / source`** の4点セットで扱います。SQLiteは正本ではなく、保存した原典から再構築できる検索層です。

詳しくは [`bootstrap/README.md`](bootstrap/README.md) を参照してください。

## 仕組み

```text
公式公開情報・公的統計
        ↓  保存・来歴記録
再構築可能なSQLite / FTS5
        ↓  必要部分だけを抽出
小さなcontext pack
        ↓  調査・照合を補助
AIエージェント
        ↓  人が確認・判断
Obsidianの判断ノート
```

このOSでは、次の3つを混ぜません。

1. **原典** — 公式資料、取得日時、hash、原典位置
2. **検索層** — 原典から再構築できるSQLite / FTS5
3. **判断層** — 問い、論点、迷い、採否、追跡期限を残すObsidian

AIへ大量の資料をそのまま渡すのではなく、検索結果から必要な範囲だけをcontext packにします。

## 「見立て → ツボ → 手当て」

地域の困りごとを、単なる要望や責任論で終わらせず、制度を動かせる提案へ変えるための型です。

1. **見立て** — 何がどこで詰まっているか、事実と意見を分ける
2. **ツボ** — 町・県・国の制度をたどり、どこへの働きかけが効くか探す
3. **手当て** — 効果だけでなく、副作用、反対論、追跡指標まで含めて提案する

一般質問、委員会提言、町への事業提案、県への要請、意見書、省庁照会などへ展開できます。自動送信や自動公開はしません。

- [ツボ探しのワークフロー](workflows/policy-tsubo.md)
- [見立てテンプレート](templates/policy-issue.md)
- [手当てテンプレート](templates/policy-pr.md)

## AIに任せないこと

- 政策の良し悪しを決めること
- 住民の声を本人に代わって解釈すること
- 未確認の数字やURLをもっともらしく補うこと
- 個人情報や内部資料を公開情報へ混ぜること
- 質問、要請、公開物を自動で送信すること

判断、説明、提出、公開の責任は議員本人に残します。AIは調査、構造化、照合、検算の補助者です。

## 現在の実装範囲

最新リリースは **[v0.2.0](https://github.com/i8ei/local-councilor-ai-os/releases/tag/v0.2.0)** です。

- 全国1,741自治体のregistryと公開構造の観測snapshot
- 自治体名からのTier 0〜1ブートストラップ
- 議事録DB（kaigiroku.net、静的HTML / PDF、未対応向け実装ガイド）
- 例規DB（静的HTML / TXT、g-reiki系アダプター）
- 予算・決算のSQLite入力契約、検算、分析候補生成
- 自治体間ベンチマークDBと比較条件プリセット
- Obsidian Vaultの診断、安全なscaffold、状態・鮮度確認
- 公開予定稿の安全走査、DB検証、backup / restore
- 運用設計11章、実務ワークフロー8本、テンプレート、データ契約

### 現在の限界

- 自治体ごとに異なるCMS、PDF、帳票を完全自動では処理しません
- 観測snapshotは探索候補であり、現在の公式入口を保証しません
- 予算・決算PDFからの数値抽出は、人または利用者側のAI / OCRが担当します
- 抽出に成功しても、総額照合などの検算を通るまでは対外利用可能としません
- 統計値、取得原本、住民情報、利用者のDBはリポジトリに含めません

## ドキュメント案内

| 読みたいこと | ドキュメント |
|---|---|
| 導入手順 | [`setup.md`](setup.md) |
| 自治体データ基盤 | [`bootstrap/README.md`](bootstrap/README.md) |
| 議事録の取込・検索 | [`modules/minutes_db/README.md`](modules/minutes_db/README.md) |
| 例規の取込・検索 | [`modules/regulations/README.md`](modules/regulations/README.md) |
| 予算審査 | [`modules/budget_review/README.md`](modules/budget_review/README.md) |
| 決算審査 | [`modules/settlement_review/README.md`](modules/settlement_review/README.md) |
| 運用設計 | [`way-of-working/README.md`](way-of-working/README.md) |
| 安全原則 | [`principles/`](principles/) |
| データ契約 | [`data-contracts/`](data-contracts/) |
| 貢献方法 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## 自分の自治体で試して、教えてください

このプロジェクトは、各自治体で実際に試した結果から育てます。コードを書けなくても、自治体名、どこまで進んだか、どこで止まったかを知らせるだけで大きな貢献になります。

- [自治体で試した結果を報告する](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=municipality-test.yml)
- [不具合を報告する](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=bug-report.yml)
- [IssueやPull Requestの出し方](CONTRIBUTING.md)

Claude CodeやCodexと作ったPull Requestも歓迎します。提出前に差分を確認し、秘密値、個人情報、内部資料、取得原本が含まれていないことを確認してください。

## 開発

```bash
./run_tests.sh
ruff check .
mypy
```

通常のCIでは、外部通信しない合成fixtureテストをPython 3.11と3.14で実行します。実APIの契約確認は週次または手動の `Live bootstrap contract` に分離しています。

## ライセンス

コードと文書は、特記がない限り [MIT License](LICENSE) で提供します。
