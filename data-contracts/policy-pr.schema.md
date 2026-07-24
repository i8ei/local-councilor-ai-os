# 手当てデータ契約（policy-pr）

## 目的

この契約は、[見立て](policy-issue.schema.md)で特定したツボへ、効果と副作用を含む変更案を作るための最小仕様である。手当ては相手を責める文書ではない。町・県・国の制度のつながりをたどり、地域の課題を動かせる場所へ、実施可能な最小差分を示す。

## 1レコードの責務

1レコードは、一つの見立てに対する一つの手当てを表す。国・県・町へ複数の働きかけが必要でも、第一のツボを一つ明記し、残りは並行対応として区別する。

要求だけを書かず、期待する効果、想定される副作用・反対論、対策、手当て後の観測方法を必ず一組で持つ。

## 必須フィールド

| フィールド | 型 | 説明 |
|---|---|---|
| `title` | string | 手当ての名称 |
| `issue_ref` | string | 元になる見立てへの安定した参照 |
| `primary_lever_level` | enum | 第一のツボ（`national` / `prefecture` / `municipality`） |
| `lever_reason` | string | その層をツボと判断した理由 |
| `current_system` | list | 国・県・町の現行制度と地域で生じる差 |
| `observed_problem` | list | 公開根拠で確認できる現象 |
| `proposed_changes` | list | 実施主体と最小差分を含む変更案 |
| `expected_effects` | list | 誰に何が届き、何で効果を測るか |
| `side_effects_and_objections` | list | 費用、負担、公平性、反対論など |
| `mitigations` | list | 対象限定、段階導入、観測、撤退条件など |
| `outreach_options` | list | 一般質問、意見書、照会等への変換候補 |
| `follow_up` | object | 観測する変化、確認時期、見直し条件 |
| `status` | enum | 手当ての状態。見立ての状態とは独立に持つ |

## 現行制度の層別記録

`current_system` は国・県・町の層ごとに書く。すべての層に問題がある前提を置かず、制度が既に整っている層には `gap: null` または「問題なし」と記録してよい。

```yaml
current_system:
  - level: national
    rule_or_operation: "<法律・省令・通知・交付要件など>"
    gap: "<地域で届かない点、または null>"
  - level: prefecture
    rule_or_operation: "<計画・補助制度・広域運用など>"
    gap: "<地域で届かない点、または null>"
  - level: municipality
    rule_or_operation: "<条例・要綱・事業・運用など>"
    gap: "<地域で届かない点、または null>"
```

## 変更案の最小単位

各 `proposed_changes` は、誰が、何を、どの範囲で変えるかを含む。

```yaml
proposed_changes:
  - actor: "<national | prefecture | municipality | other>"
    change: "<現行からの変更差分>"
    scope: "<対象地域・対象者・期間・実証範囲>"
    evidence_refs:
      - "<evidence_id>"
```

## 副作用・反対論と対策

副作用・反対論は任意項目にしない。手当ての不利益、実施主体の制約、制度を変えない方がよいという立場を先に記録し、対応する対策を結び付ける。

```yaml
side_effects_and_objections:
  - id: R-001
    statement: "<想定される副作用または反対論>"
mitigations:
  - risk_ref: R-001
    action: "<対象限定・第三者確認・段階導入・撤退条件など>"
    observation: "<何を測るか>"
```

## `status`（手当ての状態）

- `drafting`：差分案を作成中
- `reviewing`：根拠・副作用・宛先を人が確認中
- `ready`：働きかける文書へ変換できる
- `submitted`：人が確認し、外部へ提出・照会済み
- `accepted`：制度・運用へ採用された
- `partially_accepted`：一部が採用された
- `declined`：採用されなかった
- `monitoring`：実施後の効果・副作用を観測中
- `closed`：追跡を終了した

`submitted` への更新はAIが自動で行わない。誰が、いつ、どの形式で提出したかを人が記録する。

## 完全なレコード形

```yaml
title: "<手当ての名称>"
issue_ref: "<見立てへの参照>"
primary_lever_level: "<national | prefecture | municipality>"
lever_reason: "<その層をツボと判断した理由>"
current_system:
  - level: "<national | prefecture | municipality>"
    rule_or_operation: "<現行制度・運用>"
    gap: "<地域で生じる差、または null>"
observed_problem:
  - claim: "<確認済みの現象>"
    evidence_refs: ["<evidence_id>"]
proposed_changes:
  - actor: "<実施主体>"
    change: "<変更差分>"
    scope: "<対象・期間・実証範囲>"
    evidence_refs: ["<evidence_id>"]
expected_effects:
  - beneficiary: "<届く相手>"
    effect: "<期待する変化>"
    indicator: "<観測指標>"
side_effects_and_objections:
  - id: "<risk-id>"
    statement: "<副作用または反対論>"
mitigations:
  - risk_ref: "<risk-id>"
    action: "<対策>"
    observation: "<観測方法>"
outreach_options:
  - "<general-question | committee-proposal | opinion-letter | prefecture-request | ministry-inquiry>"
follow_up:
  first_check_on: "<YYYY-MM-DD>"
  observation: "<予算化・制度化・実施・利用・効果>"
  review_or_exit_condition: "<修正または中止する条件>"
status: "<drafting | reviewing | ready | submitted | accepted | partially_accepted | declined | monitoring | closed>"
```

## 外部利用条件

外部へ変換する事実は、[根拠データ契約](evidence_schema.md)で原則 `verified` または `reconciled` とする。出力先が一般質問、意見書、県要請、省庁照会のいずれでも、自動送信しない。最終的な宛先、表現、提出、公開は議員本人が決める。
