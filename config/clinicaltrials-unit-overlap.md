# 同站点两个单元的分工：`clinicaltrials` 与 `clinicaltrials-cn`

实测发现仓库里已有一个 `spiders/clinicaltrials`（2026-10-06 建，11-10 落 `data_period_field`），
与本批新增的 `spiders/clinicaltrials-cn` **同站点同 API**。此文件记录两者分工，避免后来者
误判为重复而删掉一个。

## 分工（不是重复）

| 维度 | `clinicaltrials`（既有） | `clinicaltrials-cn`（本批新增） |
|---|---|---|
| 数据面 | CT.gov API v2 `countTotal` | CT.gov API v2 `filter.advanced` 明细 |
| 切月字段 | `StudyFirstPostDate`（**注册/首发**月） | `StartDate`（试验**起始**月） |
| 地理范围 | 全球 | `AREA[LocationCountry]China` |
| 粒度 | 每自然月 **一行聚合量**（`totalCount`） | **每个试验一行**明细（NCT 号、分期、状态、申办者） |
| 回答的问题 | 全球每月新登记多少试验 | 中国境内有哪些试验、各自什么状态 |
| 事实地址 | 月份 | NCT 注册号 |
| golden 锚 | 固定历史月 `totalCount`（2020-01=2867） | 月内 NCT 数值最小者（`NCT03062332` @2020-01） |

**为什么不能合并成一个**：
- 聚合与明细的**列不同构**（一个是 `event_count`，一个是 11 列明细），合进一个
  `columns[]` 就得让其中一半永远为 null，列声明会变成谎话。
- 切月字段不同：全球"注册量"与中国"起始量"是**两个不同的指标**，混在一个 source 下会
  让下游按 source 分组统计时口径互串。
- 上游回补行为不同：`StudyFirstPostDate` 的历史月基本冻结（既有单元 golden 可用等值断言），
  `StartDate` 的历史月会因补登记而增长（我的 golden 因此不作等值断言）。两类断言策略
  写在同一单元里会互相打架。

## 需要留给后来者的两条判据

1. **看 column 的粒度，不看 slug 像不像**：slug 都含 clinicaltrials 并不代表重复；
   判据是「同一切月字段 + 同一地理范围 + 同一粒度」三者全同才算重复。
2. **重复的判定成本低于删错的成本**：删掉一个已被下游引用的 source 会让历史观测失去来源，
   而多留一个 source 只是多一个调度。有疑问时先记分工，不要删。

## 本条的产出物

- 就绪面 `scripts/pharma_readiness.py` 只声明五个**指标族**的落位，不涉及此分工。
- 本文件属于 change 归档后的补充说明，放在 `config/` 便于与 `pharma-families.yaml` 同处查阅。
