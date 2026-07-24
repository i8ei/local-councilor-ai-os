# 導入診断とVault scaffold

`onboarding`は、既存のClaude Code、Codex、Obsidian環境を壊さず、地方議員AI運用OSに必要な不足分だけを追加する入口である。

診断と計画は読み取り専用で、ファイルを作らない。計画のSHA-256を確認して`scaffold`へ渡した場合だけ、存在しない棚、MOC、profile、テンプレートを作る。既存ファイルは上書き・統合・削除しない。

## 1. 読み取り専用診断

```sh
python3 -m onboarding diagnose \
  --vault '/absolute/path/to/vault'
```

既定で、次の3段階をすべて確認する。

- `obsidian vaults verbose`に対象Vaultの絶対パスが完全一致する
- 対象Vault名を明示した`vault info=path`が同じ絶対パスを返す
- 対象Vault名を明示した検索コマンドが終了コード0になる

AIクライアントを指定しない場合は`auto`診断になる。Claude CodeまたはCodexの片方だけが利用可能なら自動選択し、両方なら`--agent claude`または`--agent codex`を選ぶため終了コード2で停止する。Codexは`AGENTS.override.md`、次に`AGENTS.md`、Claude Codeは`CLAUDE.md`を有効なVaultガイドとして確認する。

Obsidian CLIがない、Vaultとして開かれていない、選択したAIクライアントのCLIやVaultガイドがない場合は`handoff_required`になる。必要なガイド、クライアント固有の権限確認、`claude-obsidian-setup`の参照先、引数を含む再診断コマンドを表示する。自動インストール、ガイドの自動作成、グローバル設定変更は行わない。

診断は、Vault登録、指示ファイル、主要CLI、既存scaffold、`ESTAT_APPID`の有無、権限上の確認事項を返す。認証情報の値は表示しない。OS上の書き込み可否と、AIクライアントのwritable rootsは別物なので、後者は`needs-confirmation`として残す。

既存Vaultでは、深さ3以内の既存ディレクトリ名から`一般質問`、`予算`、`決算`、`広報`、`住民の声`、`証拠台帳`、template等の役割候補を探す。一意な候補が2件以上あれば`recommended_layout: preserve`とし、固定8棚を「未導入」と誤認しない。診断は候補を読むだけで、対応表を作成しない。

## 2. 導入計画のプレビュー

```sh
python3 -m onboarding plan \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --layout preserve
```

プレビューには、追加・再利用・競合するファイル、外部通信先、停止条件、個別承認を残す操作、決定的な`plan_sha256`を表示する。この時点では何も作らない。異なる内容の既存ファイル、symlink、Vault外へ解決される対象がある場合は停止する。

Vault layout:

| layout | 用途 | 既定の追加 |
|---|---|---|
| `preserve` | すでにObsidianを運用中 | 一意に検出した既存pathを役割へ対応付け、`vault-map.yaml`と未作成時の`instance.json`だけ追加 |
| `scaffold` | 新規または標準構成を選ぶVault | 固定8棚、MOC、profile、選択したtemplate・workflowを新規作成 |

`preserve`は既存ノート、既存MOC、AI指示、フォルダ構造を移動・改名・編集しない。既定featureは`core`だけである。templateやworkflowの複製が必要な場合だけ`--features core,templates,workflows`を明示する。`templates`を選ぶ場合は、既存templateディレクトリが役割対応に含まれていなければ停止する。

自動候補を修正する場合は、次の制約付き形式を別ファイルへ作り、`--vault-map`で渡す。pathは既存Vaultからの相対pathだけを許可し、存在しないpath、symlink、Vault外、未知の役割は拒否する。

```yaml
schema_version: 1
product: local-councilor-ai-os
layout: preserve
managed_namespace: "地方議員AI運用OS"
roles:
  vault_home: "HOME.md"
  general_questions: "議会/一般質問"
  budget: "議会/予算"
  settlement: "議会/決算"
  templates: "Templates"
```

```sh
python3 -m onboarding plan \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --layout preserve \
  --vault-map '/absolute/path/to/reviewed-vault-map.yaml'
```

導入モード:

| モード | 用途 |
|---|---|
| `integrate` | 既存のObsidian・AI環境へ不足分だけ追加 |
| `full` | 基盤未整備。`claude-obsidian-setup`へハンドオフ |
| `components` | 選択したデータCLIだけ利用 |
| `diagnose` | 書き込みを行わず適合性だけ確認 |

## 3. scaffoldの適用

計画を確認した後、同じ引数と表示されたSHA-256を渡す。

```sh
python3 -m onboarding scaffold \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --layout preserve \
  --accept-plan-sha256 '<plan_sha256>'
```

`--vault-map`を使ったplanではscaffoldにも同じ引数を渡す。実行直前に診断と計画を再作成し、SHA-256が一致した場合だけ新規ファイルを作る。Obsidian CLIで対象Vaultを確認できない場合や、計画確認後にファイル状態が変わった場合は停止する。手動確認による迂回オプションはない。

実行結果はVault内の`.local-councilor-ai-os/runs/`へJSONで記録する。`preserve`は`.local-councilor-ai-os/vault-map.yaml`と、存在しない場合だけ`instance.json`を作る。既存の`instance.json`はschemaとproductだけ検査し、編集しない。`scaffold`は8つの業務棚MOC、OS専用MOC、専用名前空間内のテンプレート、未完了の利用者・議会プロファイルを作る。どちらも既存の入口MOCやAI指示ファイルは自動編集しない。

scaffold作成後も、本人情報と自治体固有の議会運用を確認するまではOS全体を`complete`にせず、`profile_status: incomplete`として残す。

profileを確認済みにする操作はonboarding manifestを書き換えず、
`python3 -m lcaios profile confirm ... --confirm-human-reviewed`で別のappend-only
profile manifestへ記録する。`layout: preserve`ではprofileの保存棚を固定せず、
Vault内の確認済み2ファイルを明示指定する。

## 4. scaffoldの検証

```sh
python3 -m onboarding verify \
  --manifest '/absolute/path/to/vault/.local-councilor-ai-os/runs/<run-id>.json'
```

manifestに記録した全artifactのSHA-256とObsidian CLI疎通を読み取り専用で検証する。`preserve`では役割pathが現在もVault内に存在しsymlinkでないことを確認する。`scaffold`ではMarkdownの`description` frontmatter、8つの業務棚MOC、OS専用MOCのリンクも確認する。

終了コードは、成功またはscaffold検証済み・profile未完了が`0`、AIクライアント選択・停止条件・検証失敗が`2`、基盤セットアップへのハンドオフが`3`。

## 5. OS全体の状態確認

onboardingは環境診断とscaffoldの正本である。scaffold後に、profile、Tier 1 DB、artifact完全性、鮮度を含むOS全体の状態と次の一手を確認する場合は、読み取り専用の統一入口を使う。

```sh
python3 -m lcaios doctor \
  --vault '/absolute/path/to/vault'

python3 -m lcaios status \
  --vault '/absolute/path/to/vault'
```

`doctor`はonboarding診断とreadinessを束ねるが、自動scaffold、設定変更、データ取得は行わない。基盤不足なら本READMEの段階1へ戻し、scaffold未完了なら`plan`、`scaffold`、`verify`の順を示す。
