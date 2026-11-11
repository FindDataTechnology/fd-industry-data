# 静默纪律漂移 · 2026-11-11（发现记录，未处置）

> 状态：**只记录，未改动任何他人文件**。需要该线负责人（pharma/医药方向的那个会话）裁定。

## 一、被违反的条款（canon，已归档）

`openspec/specs/source-generation-flow/spec.md`（本仓 change `industry-source-solution-fleet`
于 2026-11-11 归档时合入；原文 requirement 标题：*生成单 SHALL 仅由人工或工具创建且产物守静默与人工门*）：

> 生成单产物 PR 出具前 SHALL 通过 `check_manifest_commands` 与 conformance gate；
> 产物 SHALL 遵守**静默纪律（不写 schedule、永不自动点亮）**且沿用人工 merge 门。

## 二、实测到的漂移

**观察时点**：2026-11-11 13:1x，本地 `main` 领先 `origin/main` 的 4 笔提交
（**另一个会话的活线，非本线产物**；该批随后经 merge 汇合推到 github+gitee 双端，
故现在两端都含这些单元——行号/提交清单以当时观察为准）：

```
b4afd81 feat(pharma): 事实定型与修订窗绑定层（第 4 阶段）
212b5a5 fix(nrdl): 列名 payment_scope → coverage_restriction；新增医药概念落库脚本
52aa506 feat(pharma): 新增临床试验与医保目录两个源单元
```

新增两个单元，**都写了 `schedule:`**：

| 单元 | schedule 行 | 是否有生成单 |
|---|---|---|
| `spiders/clinicaltrials-cn/` | `schedule: "47 2 6 * *"` | **有** —— `reports/health-tickets/20261006-clinicaltrials-941ede99.yaml`（`kind: generate`） |
| `spiders/nrdl-national-catalog/` | `schedule: "19 3 8 1 *"` | 未查到对应 generate 工单（可能是人工新建，见下） |

**判据**：`clinicaltrials-cn` 来自 `kind=generate` 工单 → 其产物受上述 requirement 约束 →
「不写 schedule」被违反。`nrdl-national-catalog` 若无生成单，则不受该条约束（人工新建的源
本就可以直接带 schedule），**不要一并处理**。

## 三、为什么这条规矩不是形式主义

`永不自动点亮` 的目的不是流程洁癖，而是**留一个人工核验点**：单元在合入时双门只验"声明与契约
对得上"，**验不了"这段逻辑取到的数对不对"**。本线自己的例子就摆在那：
`usaspending-awards` 双门全绿、manifest 校验 0 错，但逐行读才审出静默截断
（>12 个月窗口只返回最早 12 个月）；后来又量出上游回填滞后 6.5 倍（2026-10 只有 2025-10 的 15%）。
两类缺陷**自动门都拦不住**，只有"点亮前有人看一眼"能拦。

自己写 schedule 等于把这个核验点删掉：单元会按 cron 静默开始往生产库写数，
而没有任何人看过它的取数语义。

## 四、我做了什么 / 没做什么

**做了**：
- 记录本文件（证据、判据、边界）
- 核对两个新单元的 manifest 都通过门（`validate_manifests` 165 manifests / 0 violations；
  `conformance_gate` 167 units / 0 violations）——即**门没红，红的是纪律**

**没做**（刻意）：
- 没删他人 manifest 里的 `schedule:` —— 那是别人未提交的活线，改它会造成冲突，
  且我无法确认是否已有明确授权（若主人明确要求点亮，则该条不成立）
- 没把「生成单产物不得带 schedule」做成 conformance 规则 —— 见下，这会让在途工作变红，
  需先取得同意

## 五、建议的机械约束（待裁定后再做）

现有双门都**不检查**这条纪律，所以它现在只是一句写在 spec 里的话。建议加一条
`conformance_gate.py` 规则：

> `generation-silence-violation`：若某单元存在 `kind=generate` 的工单
> （`reports/health-tickets/*.yaml` 中 `source` 与该单元对应），而该单元 `manifest.yaml`
> 含顶层 `schedule:` 键 → 报违规，点名单元与工单 id，修法=删 schedule 或注明 "owner 已授权点亮"。

**代价**：加上后，`clinicaltrials-cn` 会立刻变红，挡住那条第 3 笔/后续 push。
所以这是一次"用门逼回纪律"的动作，需要该线负责人同意（或主人裁决）后再落。

## 六、给后来人的一句

如果你正在读这篇并准备新建源单元：**人工新建的源可以直接写 `schedule`**（这是既有惯例，
`conformance gate` 也接受）；**但由 `kind=generate` 工单产出的单元不行**——先静默合入，
跑通、验过、再点亮。区别不在"谁写的文件"，在"这个单元有没有经过那个核验点"。
