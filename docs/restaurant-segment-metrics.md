# 餐饮细分面一期 — 港股 KPI 口径档案（task 3.5）

> change `add-restaurant-vertical` · 实测 2026-11-08
> 取源：`spiders/hkex-disclosure` 已抓到的业绩公告 PDF（`crawl_items.payload.doc_url`）
> 取文：`pdftotext -layout -enc UTF-8`（两份 PDF 均有真实文本层：`/Font` 存在、`/Image` 0，非扫描件）
> 纪律：**每个指标必须带「报告期 + 分部定义 + 公司口径原文」**；无口径的字段不注册、不呈现。

## 1. 已取到口径的指标（4 家·公司级 6 项）

| # | 公司 | 指标 | 报告期 | 分部 / 范围 | 值 | 单位 | 口径原文（公告脚注） |
|---|---|---|---|---|---|---|---|
| 1 | 海底捞 06862 | 整体翻台率 | 2026-01-01~2026-06-30（中期） | 自营海底捞餐厅 Overall（含大陆 + 港澳台） | **3.9**（上年同期 3.8） | 次/天 | 「總桌數 ÷（本期營業天數 × 期內平均桌數）」— `Calculated by dividing the total tables served for the period by the product of total number of operation days for the period and average table count during the period.` |
| 2 | 海底捞 06862 | 大陆翻台率 | 同期 | 中国大陆自营餐厅 | **3.9**（3.8） | 次/天 | 同上；分部=Chinese mainland restaurants |
| 3 | 海底捞 06862 | 港澳台翻台率 | 同期 | 香港、澳门、台湾 | **4.5**（4.3） | 次/天 | 同上；分部=Hong Kong, Macau and Taiwan regions |
| 4 | 海底捞 06862 | 同店翻台率（整体） | 同期 | 自营同店 Overall | **3.9**（3.9） | 次/天 | 「同店總桌數 ÷（本期營業天數 × 期內平均桌數）」— `Calculated by dividing the total tables served at our same stores for the period by the total number of operation days for the period and average table count during the period.` |
| 5 | 海底捞 06862 | 人均消费 | 同期 | 自营海底捞餐厅 | **97.0** | 元/人 | 「餐廳經營總收入 ÷ 期內服務顧客總數」— `Calculated by dividing gross revenue generated from restaurant operation for the period by total number of guests served for the period.` |
| 6 | 九毛九 09922 | 同店平均日销售同比（太二自营） | 2026 H1 vs 2025 H1 | 太二自营同店（期内两个半年度均营业满 150 天） | **+7.4%** | % | 「同店 = 2025 上半年与 2026 上半年均开业满 150 天的餐厅；同店平均日销售 = 该等餐厅期内日均收入」— `The Group defines its same store base to be those restaurants that opened for at least 150 days in the first half of both 2025 and 2026.` |
| 7 | 九毛九 09922 | 同店平均日销售同比（怂火锅） | 同期 | 怂火锅同店 | **−22.9%** | % | 同上；分部=Song Hot Pot |
| 8 | 九毛九 09922 | 同店平均日销售同比（九毛九自营） | 同期 | 九毛九自营同店 | **−11.8%** | % | 同上；分部=Jiu Mao Jiu |

**同店销售额（口径完备的绝对值，RMB 千元）**：太二 1,636,299（上年 1,524,357）、怂火锅 242,672（316,214）、九毛九 162,092（184,217）。

原始 PDF：
- 海底捞中期业绩公告 <https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501119.pdf>
- 九毛九中期业绩公告 <https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0828/2026082802450.pdf>

## 2. 原文片段（可回指，非二次计算）

海底捞（`06862L.txt:327-336` 翻台率表；`353-354` 脚注 5）：

```
Table turnover rate(5) (times/day)
  Tier 1 cities(2)                                   3.9        3.8
  Tier 2 cities(3)                                   3.9        3.8
  Tier 3 cities and below(4)                         3.9        3.9
 Chinese mainland restaurants                        3.9        3.8
  Hong Kong, Macau and Taiwan regions                4.5        4.3
Overall                                              3.9        3.8

(5)  Calculated by dividing the total tables served for the period by the product of
     total number of operation days for the period and average table count during the period.
```

九毛九（`09922L.txt:73-82`）：

```
Same store sales (RMB ’000) (5)
Tai Er (self-operated)                   1,636,299    1,524,357
Song Hot Pot                               242,672      316,214
Jiu Mao Jiu (self-operated)                162,092      184,217

Change in same store average daily sales(6) (period-on-period) (%)
Tai Er (self-operated)                         7.4
Song Hot Pot                                 (22.9)
Jiu Mao Jiu (self-operated)                  (11.8)
```

> 括号在港股财报里表示负数（`(22.9)` = −22.9%），已按此归一。

## 3. 未注册 / 不入库（显式，非静默省略）

| 项 | 状态 | 原因 |
|---|---|---|
| 上述 8 项写入 `semantic_observations` | **不入库** | 报告期为**半年**（2026-01-01~06-30）。现役 `granularity` 词表只有 `quarter/day/year/month/hour/week`（实测全库分布），**没有 `half`**；用 `quarter` 记半年度、或用 `year` 记半年段，都是口径造假。故本期只落口径档案，**不注册概念、不写观测** |
| 概念注册 | **不注册** | 同上：概念一经注册即进读路径，而读路径尚无正确粒度承载半年值。`grep` 确认此前无人注册 `restaurant.table_turnover*` 类概念 |
| 太二/怂火锅/九毛九 同店**销售额**绝对值 | 记录在案但**不入库** | 同为半年期；且「同店销售额」是公司自定义同店集合的汇总，跨公司不可比 |
| 海底捞 分城市层级翻台率（一线/二线/三线及以下） | 已读到，**不逐条入库** | 分部口径依赖公告脚注 2/3/4 的城市名单定义；本期只取 Overall / 大陆 / 港澳台三档 |
| 台股 3 家、日股 4 家细分 KPI | **不可得** | 台股快照式月营收无 KPI；日股 EDINET key 未到位（见 `restaurant-lane-mapping.md` §2） |
| 其余港股 8 家（呷哺/达势/特海/百胜中国/大家乐/大快活/奈雪/周黑鸭）细分 KPI | **未取** | 一期只做海底捞与九毛九两家（任务书点名）；其余家留二期，**不假装已覆盖** |

## 4. 待裁：粒度词表缺 `half`

半年报是港股餐饮的主要披露节奏（中期 + 年度）。现状三种选择，需拍板其一：

1. **新增 `half` 粒度**（推荐）：`granularity='half'`，`date`=报告期末（如 `2026-06-30`），与 `quarter/year` 并列；读路径按 periodicity 过滤时一并纳入。改动面：观测表无粒度 CHECK 约束（`granularity` 仅 varchar(8)，实测无约束），故**不需要 DDL**，但须在 `fd-open-data-protocol` 的粒度词表与读侧口径里登记。
2. **只做年度**：等年报（2026-12-31 期末）一次性入 `year`，中期只留档案 —— 数据时效损失半年。
3. **折算成季度**：把半年值按半年÷2 记成 `quarter` —— **不采纳**，属口径造假（翻台率不可线性拆分）。

在裁决前，本档案即为 3.5 的交付形态：**每项指标都有报告期 + 分部定义 + 原文口径，无口径字段一律未注册**。
