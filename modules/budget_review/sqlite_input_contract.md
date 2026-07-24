# 予算レビュー SQLite 入力契約

## 目的

この契約は、予算書、補正予算書、予算説明資料などから確認した数値を、検算可能なSQLiteへ入れるための入力契約である。CSVは正本ではなく、AIや人手で抽出・確認した値をSQLiteへ投入するための中間入力形式である。PDFの読み取り方法は自治体、年度、会計、資料種別によって違うため、本モジュールは汎用抽出器を提供しない。

## 行の粒度

`grain` は次のいずれかとする。

| grain | 粒度 | 必須コード |
|---|---|---|
| `total` | 歳入または歳出の総額 | なし |
| `kan` | 款 | `kan_code` |
| `ko` | 款・項 | `kan_code`, `ko_code` |
| `moku` | 款・項・目 | `kan_code`, `ko_code`, `moku_code` |
| `setsu` | 款・項・目・節 | `kan_code`, `ko_code`, `moku_code`, `setsu_code` |

## 予算段階

`budget_stage` は次のいずれかとする。

| 値 | 意味 |
|---|---|
| `initial` | 当初予算 |
| `supplemental` | 補正予算 |
| `current` | 現計予算、補正後予算など |

補正予算では `proposal_no` に議案番号や補正番号を入れる。

## 共通必須列

| 列 | 内容 |
|---|---|
| `fiscal_year` | 年度。西暦整数 |
| `account_name` | 会計名 |
| `budget_stage` | `initial` / `supplemental` / `current` |
| `side` | `revenue` / `expenditure` |
| `grain` | `total` / `kan` / `ko` / `moku` / `setsu` |
| `raw_value` | 原表行またはセル群の生表記 |
| `unit` | 原表の単位 |
| `as_of` | 対象年度・資料時点 |
| `definition` | 表、列、会計範囲、予算段階の説明 |
| `source_name` | 公式資料名 |
| `source_url` | 公式に到達できるURL |
| `source_locator` | PDFページ、印刷ページ、表名、行、列などのJSON文字列 |
| `fetched_at` | 原典取得または確認時刻 |
| `verification_state` | `draft` / `discovered` / `verified` / `reconciled` / `rejected` |
| `print_page` | 資料に印字されたページ |
| `pdf_page` | PDFビューア上のページ。1始まり |

## 金額列

| 列 | 用途 |
|---|---|
| `current_year_amount` | 本年度予算額、予算額、補正後額など、その行の主金額 |
| `previous_year_amount` | 前年度予算額 |
| `comparison_amount` | 本年度予算額 - 前年度予算額 |
| `pre_supplement_amount` | 補正前額 |
| `supplement_amount` | 補正額 |
| `post_supplement_amount` | 補正後額 |

当初予算では `current_year_amount`、`previous_year_amount`、`comparison_amount` を中心に使う。補正予算では `pre_supplement_amount`、`supplement_amount`、`post_supplement_amount` を中心に使う。

## 完了条件

SQLiteへ投入しただけでは検証済みではない。少なくとも次を満たすまで、対外利用可能な予算DBと呼ばない。

1. `verify_totals.py` が終了コード0である。
2. 歳入歳出総額が一致している。
3. 可能な範囲で、款、項、目、節の積み上げが一致している。
4. 前年度比較列、補正前後列が原表と整合している。
5. `source_locator` から原典位置へ戻れる。
