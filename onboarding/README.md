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

## 2. 導入計画のプレビュー

```sh
python3 -m onboarding plan \
  --vault '/absolute/path/to/vault' \
  --agent codex \
  --mode integrate \
  --features core,templates,workflows
```

プレビューには、追加・再利用・競合するファイル、外部通信先、停止条件、個別承認を残す操作、決定的な`plan_sha256`を表示する。この時点では何も作らない。異なる内容の既存ファイル、symlink、Vault外へ解決される対象がある場合は停止する。

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
  --features core,templates,workflows \
  --accept-plan-sha256 '<plan_sha256>'
```

実行直前に診断と計画を再作成し、SHA-256が一致した場合だけ新規ファイルを作る。Obsidian CLIで対象Vaultを確認できない場合や、計画確認後にファイル状態が変わった場合は停止する。手動確認による迂回オプションはない。

実行結果はVault内の`.local-councilor-ai-os/runs/`へJSONで記録する。8つの業務棚MOC、OS専用MOC、専用名前空間内のテンプレート、未完了の利用者・議会プロファイルを作る。既存の入口MOCやAI指示ファイルは自動編集しない。

scaffold作成後も、本人情報と自治体固有の議会運用を確認するまではOS全体を`complete`にせず、`profile_status: incomplete`として残す。

## 4. scaffoldの検証

```sh
python3 -m onboarding verify \
  --manifest '/absolute/path/to/vault/.local-councilor-ai-os/runs/<run-id>.json'
```

manifestに記録した全artifactのSHA-256、Markdownの`description` frontmatter、8つの業務棚MOC、OS専用MOCのリンク、Obsidian CLI疎通を読み取り専用で検証する。

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
