# 決算レビュー

## 目的

このモジュールは、9月の決算審査に向けて、決算書の総括表と明細を検算可能な形へ整え、確認すべき増減や執行差の候補を作る。PDF から取り出した数値をそのまま分析へ渡さず、原典位置、単位、定義、取得日時を保持し、階層ごとの合計が差額ゼロになったデータだけを利用する。

## 構成

| ファイル | 役割 |
|---|---|
| `schema.sql` | 総括表、歳入の款項明細、歳出の款項目節明細を格納する |
| `verify_totals.py` | 歳入の項から款、歳出の節から目、目から款を差額ゼロで突合する |
| `insight_spec.md` | v0.2 で生成する分析候補と証拠台帳への受け渡しを定義する |
| `tests/create_fixtures.py` | 合格用と不合格用の小さな SQLite を再生成する |

`schema.sql` は、原表の複数金額列を名前付きの `value` として保持し、原行全体を `raw_value` に残す。すべての行は原単位を持つ。歳出明細は節粒度であり、同じ目合計が複数の節行に反復される前提なので、款集計の前に款、項、目で重複を除く。

PDF の判定、取得、転記、見開き結合は [Tier 3 手順書](../../bootstrap/local-documents/)を参照する。データ層と判断層の境界は[データと判断](../../way-of-working/03-data-vs-judgment.md)、検証状態は[根拠データ契約](../../data-contracts/evidence_schema.md)に従う。

## 利用計画

```sh
sqlite3 settlement.db < modules/settlement-review/schema.sql
python3 modules/settlement-review/verify_totals.py settlement.db
```

検算は読み取り専用で実行する。単位不一致、総括表の欠落、明細の欠落、反復された目合計の不一致、差額がゼロでない項目のいずれかがあれば終了コード `1` を返す。全件が一致した場合は `0` を返す。検算結果と人の原典確認を証拠台帳へ記録した後に、対象行を `reconciled` として扱う。

合成フィクスチャは次の手順で再生成して実行できる。

```sh
python3 modules/settlement-review/tests/create_fixtures.py
python3 modules/settlement-review/verify_totals.py \
  modules/settlement-review/tests/passing.db
python3 modules/settlement-review/verify_totals.py \
  modules/settlement-review/tests/failing.db
```

## 状態

v0.1 では、格納スキーマ、来歴要件、差額ゼロの検証仕様、分析出力仕様を公開する。取込処理の参照実装は、実際の9月決算審査で版面差と人の確認点を記録した後、v0.2 で追加する。現時点では人間併走による転記と正規化を前提とし、自治体横断の完全自動 OCR を約束しない。
