# 貢献ガイド

`local-councilor-ai-os`は、各自治体で実際に試した結果から育てたいプロジェクトです。
コードを書けなくても、どこで止まったか、公式資料の入口がどこかをIssueで知らせる
だけで重要な貢献になります。

## 歓迎する貢献

- 自治体で試した結果と、止まった段階の報告
- 自治体コードや公式ホームページ入口の訂正
- 議事録、例規、予算・決算資料の公式入口に関する情報
- 未対応ベンダーや新しい公開方式の再現例
- READMEやセットアップ文書で迷った箇所の改善
- アダプター、検証処理、回帰テストの追加

## Issueを出す

最初から原因や修正方法を特定する必要はありません。該当するテンプレートを使い、
分かる範囲だけ記入してください。

- [自治体で試した結果](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=municipality-test.yml)
- [不具合報告](https://github.com/i8ei/local-councilor-ai-os/issues/new?template=bug-report.yml)

URLや資料の場所が不明な場合は、推測せず「不明」と書いてください。実行ログを貼る
場合は、APIキー、個人情報、内部資料、端末内の不要な絶対パスを除いてください。

## Pull Requestを出す

Claude CodeやCodexなどを使って作ったPRも歓迎します。AIが生成した変更でも、提出者が
差分を読み、対象自治体での意味と安全性を説明できる状態にしてください。

1. Issueがある場合は、先に関連付ける
2. 1つのPRでは1つの問題を扱う
3. 既存のデータ契約、安全原則、CLI導線を守る
4. 実在する個人情報、秘密値、内部資料、取得原本をcommitしない
5. 変更に対応するテストまたは再現手順を加える
6. `./run_tests.sh`を実行する
7. 必要に応じて`ruff check .`と`mypy`を実行する

モジュールのCLIは、リポジトリrootから次の形式で実行します。

```bash
python3 -m modules.minutes_db.ingest --help
python3 -m modules.regulations.ingest --help
python3 -m modules.budget_review.verify_totals --help
python3 -m modules.settlement_review.verify_totals --help
```

## 自治体固有の情報を扱うとき

- 公式サイトで確認できる公開情報だけを根拠にする
- 自治体コード、URL、議事録ベンダー、PDF位置を推測しない
- robots.txtや取得条件に反する経路を迂回しない
- 未対応なら`unsupported_vendor`、`unknown_structure`、
  `robots_blocked`、`source_not_found`、
  `human_confirmation_required`のいずれかで止める
- 「技術的に試した」と「議員が実際に利用した」を混同しない

自治体固有の修正が他地域でも再利用できそうな場合は、設定例、fixture、最小テストを
添えてください。実データそのものではなく、再現に必要な最小構造を共有します。
