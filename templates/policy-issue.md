---
title: "{{課題名}} 見立て"
description: "{{地域の困りごとが、どの制度のどこで詰まっているかを1行で記入}}"
case_id: "{{CASE-YYYY-NNN}}"
status: investigating
visibility: internal
updated: "{{YYYY-MM-DD}}"
tags:
  - policy-issue
  - 見立て
---

# {{課題名}}（見立て）

> [!warning] 内部から公開への境界
> 住民の声・現場観察は課題を見つける入口として扱う。この見立てを外部で使うときは、匿名化した相談文をそのまま載せず、公式公開情報で独立に確認できる形へ変換する。`affected_people` は「移住希望者」等のカテゴリに留め、個人・事業者が特定される記述を書かない。

## 見立て（policy-issue）

```yaml
title: "{{何が、どこで詰まっているか}}"
municipality: "{{市町村名}}"
policy_area: "{{政策分野}}"
problem_type: "{{対象外 / 未整備 / 運用 / 撤退 / 財政 / 不作為・未活用}}"
primary_lever_level: "{{national / prefecture / municipality}}"   # 一番効くツボの層
affected_people:
  - "{{影響を受ける人のカテゴリ}}"
current_problem: >
  {{現象を事実として記述する。意見・要望・原因の断定を混ぜない}}
evidence:
  - type: "{{根拠の種類}}"
    detail: "{{根拠が示す一つの事実}}"
    source: "{{公表主体・文書・索引位置}}"
    as_of: "{{YYYY-MM または YYYY-MM-DD}}"
    verification: "{{public / verified / 要裏取り / 一次推計}}"
administrative_level:
  - "{{municipality / prefecture / national}}"
status: "{{investigating / framed / archived}}"
```

## この見立てで確認したいこと

- [ ] {{現象を裏づける公開原典があるか}}
- [ ] {{数値に値・時点・定義・出典があるか}}
- [ ] {{一番効くツボ（働きかける先）は町・県・国のどこかを、配線をたどって特定したか}}
