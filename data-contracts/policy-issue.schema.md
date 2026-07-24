# 見立てデータ契約（policy-issue）

## 目的

この契約は、地域で見つけた困りごとを、公開情報で検証できる制度課題として保存するための最小仕様である。見立ては要望書の下書きではない。何が、どこで、どの制度層で詰まっているかを記述し、後段の手当て（[policy-pr](policy-pr.schema.md)）が正しいツボ（働きかける先）へ向くための土台とする。

## 1レコードの責務

1レコードは、一つの地域課題を表す。複数の分野、複数の制度層の結論、事実と要望を一つのレコードへ詰め込まない。

`current_problem` は現象の記述に留め、原因の断定、評価、変更提案を含めない。提案は手当て側で扱う。

## 必須フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `title` | string | 何が、どこで詰まっているかを表す一文 |
| `municipality` | string | 課題が生じている市町村 |
| `policy_area` | string | 政策分野 |
| `problem_type` | enum | 詰まりの型（下記） |
| `primary_lever_level` | enum | 一番効くツボの層（`national` / `prefecture` / `municipality`） |
| `affected_people` | list | 影響を受ける人のカテゴリ。個人を特定しない |
| `current_problem` | string | 現象の事実記述。意見・原因の断定・提案を混ぜない |
| `evidence` | list | 現象を支える根拠。各要素は下記の5点を持つ |
| `administrative_level` | list | 課題が関係する層（`municipality` / `prefecture` / `national`） |
| `status` | enum | 見立ての状態（下記）。手当ての状態とは独立に持つ |

## `problem_type`（詰まりの型）

- `対象外`：制度はあるが、対象条件から地域の実態が外れる
- `未整備`：必要な計画・制度・仕組みがまだ無い
- `運用`：制度はあるが、運用・執行の設計で届かない
- `撤退`：担い手・事業者・サービスが縮小・撤退している
- `財政`：財源の算定式や配分が実コストを反映しない
- `不作為・未活用`：使える制度が既にあるのに、担当層が使っていない

## `primary_lever_level`（ツボの層）

一番効く働きかけ先を、`national`（国）、`prefecture`（県・圏域）、`municipality`（市町村）のいずれかで示す。上流ほど効くとは限らない。上位層で制度が既に整っている場合、ツボは市町村の運用・判断側に出る。決め打ちを避け、制度のつながりをたどった結果として記録する。

## `evidence` の5点

見立て段階の根拠は、次の5点を持つ簡易形とする。外部利用へ進める根拠は、[根拠データ契約](evidence_schema.md)の完全形へ昇格させる。

```yaml
evidence:
  - type: "<根拠の種類>"          # 例: 町一次データ / 公式統計 / 制度事実 / 住民さんの声（公開・匿名）
    detail: "<根拠が示す一つの事実>"
    source: "<公表主体・文書・索引位置>"
    as_of: "<YYYY-MM または YYYY-MM-DD>"
    verification: "<public | verified | 要裏取り | 一次推計>"
```

## `status`（見立ての状態）

- `investigating`：現象を確認中で、根拠がそろっていない
- `framed`：現象と根拠が固まり、手当てへ渡せる
- `archived`：取り下げ、統合、または他ノートへ引き継いだ

見立てと手当ては別の時計で動く。見立てが `framed` でも、手当ての実施状況は手当て側の `status` で管理する。

## 完全なレコード形

```yaml
title: "<何が、どこで詰まっているか>"
municipality: "<市町村名>"
policy_area: "<政策分野>"
problem_type: "<対象外 | 未整備 | 運用 | 撤退 | 財政 | 不作為・未活用>"
primary_lever_level: "<national | prefecture | municipality>"
affected_people:
  - "<影響を受ける人のカテゴリ>"
current_problem: >
  <現象の事実記述>
evidence:
  - type: "<根拠の種類>"
    detail: "<根拠が示す一つの事実>"
    source: "<公表主体・文書・索引位置>"
    as_of: "<YYYY-MM-or-YYYY-MM-DD>"
    verification: "<public | verified | 要裏取り | 一次推計>"
administrative_level:
  - "<municipality | prefecture | national>"
status: "<investigating | framed | archived>"
```

## 安全境界

見立ては公開情報を中心に構成する。住民の声・現場観察を入口にする場合も、`affected_people` はカテゴリに留め、`current_problem` と `evidence` に個人・事業者を特定できる記述を書かない。詳細は[安全境界](../principles/safety-boundary.md)に従う。
