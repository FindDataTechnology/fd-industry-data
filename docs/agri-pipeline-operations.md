# 农业数据管道运维记录

## 2026-11-08 · 七个农业单元补排期

**背景**：预警巡检发现 10 个农业单元里只有 `moa-hog-price`、`usda-psd` 有排期，其余
8 个「未点亮」——`未点亮不判停更` 的设计正确（避免误报），但副作用是**源不排期就永远不再抓**，
数据会静默变旧（实测 `cncotton` 停在 2025-08-31、`moa-fishery` 停在 2026-09-30）。

**处置**：按各源 `data_period` 在 `spiders/<unit>/manifest.yaml` 补 `schedule`
（排期由平台从 manifest 同步；首次点亮会自动回补前 12 个月，无需手工补历史）：

| 单元 | 排期 (cron) | data_period | 依据 |
|---|---|---|---|
| agri-casde | `10 6 3 * *` | month | CASDE 月度供需平衡表，每月 3 号抓 |
| cncotton | `25 6 5 * *` | month | 棉花月度数据 |
| moa-fishery | `40 6 7 * *` | month | 渔业月度 |
| usda-wasde | `55 6 9 * *` | month | WASDE 月度报告 |
| fert-cn | `10 7 11 * *` | month | 化肥月度 |
| moa-machinery | `25 7 15 4 *` | year | 农机年度统计，每年 4 月 15 |
| moa-variety-approval | `40 7 * * 3` | irregular | 品种审定不定期，按周轮询（周三） |

分时段错开（3/5/7/9/11 号 + 不同分钟），避免同一时刻排队。

**未动的**：`wfp-food-price` 标着 `parked: true`（有意挂起），保持不排期；
巡检里它仍显示「无排期」，属预期。

**验证**：`conformance_gate` → `PASS — 162 units, 0 violations`（manifest 有 schedule
必须同时声明 data_period 的规则已满足）；巡检汇总从「在册 3 / 已点亮 2」变成
**「在册排期源 9 / 已点亮 2 / 未点亮 1 / 遥测静默 7」**——7 个新源已点亮但还没跑过，
平台同步后首次运行会带 12 个月回补窗口。

**注意**：仓库改 manifest ≠ 平台立即生效，要等 reconciler 同步；同步后首轮是回补运行，
会占队列，观察 `crawl_runs` 的成功率再看是否需要调整时段。

## 2026-11-08 · 排期补齐后的冒烟验证（7 个单元逐个真跑）

只加 cron 不算数——**排期一个跑不动的源，只会把失败刷进队列**。逐个用真实网络跑了一遍：

| 单元 | 冒烟结果 | 备注 |
|---|---|---|
| agri-casde | ✅ 20 行 | `casde_no/period/commodity/item/value` |
| fert-cn | ✅ 20 行 | `period/product/flow/volume_wan_t` |
| moa-machinery | ✅ 76 行 | 年度表 |
| moa-variety-approval | ✅ 20 行 | 公告面（含 attachment 计数） |
| moa-fishery | ✅ 20 行 | 含 `yoy_pct` |
| usda-wasde | ✅ 30 行 | 海外直连，取 `.txt` 定宽文本 |
| cncotton | ⚠ **上游停更** | 见下 |

**cncotton 的口径要改**：列表页 129 条里**最新一期就是 2025 年 8 月**（`2025年8月棉花国际市场价格与贸易分析报告`），
之后没有新报告。所以库里 `cncotton` 最新值停在 2025-08-31 **不是我们没抓，是上游没再发**。
- 排期保留（每月 5 号继续探，一旦恢复发布就能自动接上）；
- 但健康巡检会判它「数据超期」——**这个报警的含义是「上游停更」，不是「我们抓挂了」**，
  排障时先看列表页最新期号，别先查容器；
- 业务上若还需要棉花权威价，得另找来源（Cotlook A 指数在本单元里本就取自报告正文，
  报告停了就没有更新渠道），不要用别的源凑数。

## 2026-11-08 · 真正的上线阻断：7 个单元从未入库

补完排期后核对 git 状态才发现关键事实——**这 7 个单元目录整个都是 untracked（`??`），
从来没有提交过**，因此：

- 平台是从仓库同步 `spiders/*/manifest.yaml` 的，**仓库没有它们 → 平台永远看不到**；
  这解释了为什么巡检里它们是「无排期」，而不是「排期错」。
- 我在本地 manifest 里补的 `schedule` 是**必要但不充分**的条件：不提交推送，什么都不会发生。

| 单元 | git 状态 | 平台在册 | 本地冒烟 |
|---|---|---|---|
| moa-hog-price | 已入库 | ✅ 已点亮 | — |
| usda-psd | 已入库 | ✅ 已点亮 | — |
| wfp-food-price | 已入库 | 在册但 `parked: true` | — |
| agri-casde | **未入库** | ❌ | ✅ |
| cncotton | **未入库** | ❌ | ⚠ 上游停更（最新 2025-08）|
| moa-fishery | **未入库** | ❌ | ✅ |
| usda-wasde | **未入库** | ❌ | ✅ |
| fert-cn | **未入库** | ❌ | ✅ |
| moa-machinery | **未入库** | ❌ | ✅ |
| moa-variety-approval | **未入库** | ❌ | ✅ |

未入库文件合计 **35 个**（spider.py / manifest.yaml / README / CHECKLIST / golden / 缓存）。

**结论**：要让这 7 个源真正持续更新，需要 ① 提交这 7 个目录并推送（本仓默认不自动提交，
须人工点头）；② 平台 reconciler 同步后首轮带 12 个月回补窗口。二者缺一不可。
在此之前，农业线实际在跑的数据源只有 `moa-hog-price`、`usda-psd`、`dce`（规则源）。

## 2026-11-08 · 首轮回补实跑结果：运行成功 ≠ 落数成功

7 个源的首轮（`requested_by=manual-agri-backfill`，窗口 2025-11-01→2026-11-08）实跑结果：

| 源 | 运行 | rows_written | 观测落地 | 说明 |
|---|---|---|---|---|
| agri-casde | success | 100（items） | ✅ 500 条，到 2026-10 | 已有抽取器 `agriculture_extractors/agri_casde.py` |
| moa-fishery | success | 40 | ✅ 90 条，到 2026-09 | 已有抽取器 |
| usda-wasde | success | 100 | ❌ **无抽取器** | 只落 `crawl_items` |
| fert-cn | success | 100 | ❌ **无抽取器** | 同上 |
| moa-machinery | success | 38 | ❌ **无抽取器** | 同上（年度源） |
| moa-variety-approval | success | 74 | ❌ **无抽取器** | 同上（公告流，可能本就不该出观测） |
| cncotton | **failed** | — | — | 窗口内零报告（上游 2025-08 停更）→ 已 `parked: true` |

**架构事实（这次才看清）**：runner 只写 `crawl_items`（`writer.finish_run` 里
`INSERT INTO crawl_items`），**items → `semantic_observations` 由
`scripts/agriculture_extractors/<unit>.py` 完成**。没有抽取器 = 抓得到、看不见。

**为什么没当场补四个抽取器**：模板里有硬门 `gate_periods(rows, 24)`——每条序列需
≥24 期才登记（防「三期就出图」）。首轮回补窗只有 12 个整月，**新写抽取器大概率全被判期数不足**。
补的正确顺序是：先让源按月累积（或手工带更大 `window_start` 回补），期数够了再写抽取器。
`moa-variety-approval` 是公告流（notice_type/province/附件），与 `hkex-disclosure` 同类，
**本就不该写观测**，只作事件面。

**结论**：排期与抓取链路已经通了（6/7 成功、窗口参数进 metrics），
**观测面目前只有 casde / fishery 两源在更新**；其余四源要等抽取器或更长回补。
