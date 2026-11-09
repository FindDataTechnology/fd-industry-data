# 港股细分 KPI — 逐家逐指标披露口径（task 3.5 一期）

> change `add-restaurant-vertical` · 实测 2026-11-08 · 载体 run **4829**
> 实现：`spiders/hkex-disclosure/hkex_segment_kpi.py`（纯函数，无网络）+
> `spiders/hkex-disclosure/spider.py::_enrich_headlines`（复用同一份 PDF 文本层）
> 载体：`crawl_items.payload` 的 `kpi_<slug>_*` 字段（与 `headline_*` 同一条记录）
> 取文：`fd_industry_data.pdftext`（`pdftotext -layout`，两家中报均有真实文本层）

## 1. 定位：这是「白名单标签行 + 表脚注口径」抽取，不是全文解析

本单元只做一件事：在业绩公告里，按**已登记的分区标题 + 行标签 + 脚注号**读指定
KPI，并把「报告期 + 分部/范围 + 单位 + 逐字标签行 + 脚注原文」一并写进事件 payload。
它**不是** PDF 全文解析：

- 不做版面重建：只用 `-layout` 的字符列位判断数值落在哪一年列；
- 不做叙述理解：业务回顾段（「新开 24 家 / 关停 32 家」这类句子）**一律不读、不引用**；
- 不做 OCR：无文本层即 `no_text_layer`；
- 不做推算：同店增长率没有独立百分比行 → `no_labelled_line`，**绝不**用两年同店
  销售额相除；半年值不按 ÷2 折成季度。

**硬规则（逐条可验证）**

1. 每个 KPI 必须带口径：报告期（起..止，或时点 `as_of`）+ 分部定义（spec 声明 +
   公告脚注原文）+ 单位 + 逐字标签行。缺任一项 → 不注册数值，写显式不可得原因码。
2. 只取逐字披露值，不做算术派生（金额的 ×1000 只是量级归一，与 `headline_*` 同规）。
3. 百分比按**百分数**存（`7.4` = 7.4%，unit=`percent`），不折成 0.074。
4. 不写 `semantic_observations`（半年粒度词表无 `half`；见 §8）。

## 2. 匹配契约（为什么不会张冠李戴）

| 环节 | 规则 | 实测锚点 |
|---|---|---|
| 分区标题 | 规范化后**逐字相等**（去脚注号、取括号内单位短语、其余括号保留为词） | `Table turnover rate(5) (times/day)` |
| 脚注号 | 脚注号是**匹配键的一部分**：同文档里 `Table turnover rate(2)` 与 `Table turnover rate(3)` 是两张不同的表（09922 的绩效表 vs 摘要表） | 09922 p5 vs p2 |
| 行标签 | 数值列之前的整段文字规范化后逐字相等；点线引导符（`. . . .`）先剥离 | `Tai Er (self-operated)` / `Overall` |
| 数值列 | 先按「从某数字起到行尾全是数字」切出值区，再按**字符列位**落列：列界 = 相邻年份 token 起点中点；要求表头首列=本期年、次列=上年 | 06862 `1,163` 落在 2026 列 |
| 四列表 | `2026 2025 2025 2024` 只取前两组（本年 vs 上年）；第二组的 2025 是「2025 vs 2024」对照组，**不得**当作本期上年值 | 09922 p6 同店表 |
| 单位 | 表头括号短语（`(times/day)` / `(RMB)` / `(in thousands of RMB)` / `(RMB ’000)` / `(%)`）必须与 spec 一致，否则 `unit_mismatch` | 两家分别用 `in thousands of RMB` 与 `RMB ’000` |
| 脚注 | 分区标题的 `(n)` 必须在表后**第一段** `Notes:` 里找到，且正文含 spec 声明的证据词（如 `tables served` / `150 days`） | 防脚注错号 |
| 多义 | 同一 spec 命中多个分区/行且**同日多页**、或同一行标签重复但值不同 → `ambiguous_*` 不可得 | 06862 翻台率表有两个 `Overall` 行，值相同（3.9/3.8）→ 接受 |

**未登记公司不猜标签**：对 06862 / 09922 以外的港股，payload 一律写
`kpi_status='not_applicable'` + `kpi_reason='no_phase1_kpi_target'` +
`kpi_targets=[]`（显式，不是静默省略）。目标公司里**非业绩公告行**（月报表 /
盈警 / 一般公告）写 `not_applicable` + `kpi_reason='ann_type_not_results'`，
不写 null——让读端一眼区分「公告类型不对」与「公司不在范围」。

## 3. 海底捞 06862（2026 中期业绩公告，2026-08-25 披露；期末 2026-06-30）

报告期：**2026-01-01..2026-06-30（6 个月累计）**；上年列 2025-01-01..2025-06-30。
币种：人民币（表头 `(RMB)` / `(in thousands of RMB)`）。
PDF：<https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501119.pdf>

| # | slug | 指标 | 分部 / 范围（口径） | 单位 | 本期 | 上年 | 逐字标签行 |
|---|---|---|---|---|---|---|---|
| 1 | `table_turnover_overall` | 翻台率 | 自营海底捞餐厅整体（含大陆 + 港澳台） | 次/天 | **3.9** | 3.8 | `Table turnover rate(5) (times/day)` / `Overall` |
| 2 | `table_turnover_mainland` | 翻台率 | 中国大陆自营餐厅 | 次/天 | **3.9** | 3.8 | 同上 / `Chinese mainland restaurants` |
| 3 | `table_turnover_hmt` | 翻台率 | 香港、澳门、台湾自营餐厅 | 次/天 | **4.5** | 4.3 | 同上 / `Hong Kong, Macau and Taiwan regions` |
| 4 | `table_turnover_same_store_overall` | 同店翻台率 | 自营同店整体（同店基数=两期均开业满 150 天） | 次/天 | **3.9** | 3.9 | `Average same store table turnover rate(4) (times/day)` / `Overall` |
| 5 | `spending_per_guest_overall` | 人均消费 | 自营海底捞餐厅整体 | 元/人 | **97.0** | 97.9 | `Average spending per guest(1) (RMB)` / `Overall` |
| 6 | `spending_per_guest_mainland` | 人均消费 | 中国大陆自营餐厅 | 元/人 | **95.0** | 95.8 | 同上 / `Chinese mainland restaurants` |
| 7 | `spending_per_guest_hmt` | 人均消费 | 香港、澳门、台湾自营餐厅 | 元/人 | **196.6** | 205.8 | 同上 / `Hong Kong, Macau and Taiwan regions` |
| 8 | `same_store_sales_overall` | 同店销售额 | 自营同店整体（同店餐厅经营总收入） | 人民币元（源 `in thousands of RMB`） | **16,348,861,000** | 16,571,065,000 | `Same store sales(2) (in thousands of RMB)` / `Overall` |
| 9 | `same_store_count_overall` | 同店家数 | 自营同店整体（两期均开业满 150 天） | 家 | **1,163** | —（源表上年列空白，不推断） | `Number of same stores(1)` / `Overall` |
| 10 | `restaurant_count_self_operated` | 自营餐厅数 | 自营海底捞餐厅（不含特许经营） | 家 | **1,290** | 1,322 | `Number of self-operated Haidilao restaurants 1,290 1,322` |
| 11 | `restaurant_count_franchised` | 特许餐厅数 | 特许经营海底捞餐厅 | 家 | **99** | 41 | `Number of franchised Haidilao restaurants 99 41` |

> #10/#11 是时点指标：口径写 `as_of=2026-06-30`（公告表头 `As of and for the six
> months ended June 30`），不写「起..止」区间，避免把库存量当流量。

### 3.1 海底捞不可得（显式，原因码）

| slug | 原因码 | 说明 |
|---|---|---|
| `same_store_sales_growth_overall` | `no_labelled_line` | 公告只给同店销售额两年数值，没有标签化的「同店销售增长率」行；两值相除属推算，按纪律不取 |
| `new_opened_restaurants` | `narrative_only` | 新开餐厅数只出现在业务回顾叙述段（24 家自营 + 14 家特许），不是标签行/摘要行 |
| `closed_restaurants` | `narrative_only` | 关停餐厅数只出现在业务回顾叙述段（32 家），不是标签行/摘要行 |

`kpi_status='partial'`（11 项落地 + 3 项显式不可得）。

## 4. 九毛九 09922（2026 中期业绩公告，2026-08-28 披露；期末 2026-06-30）

报告期同上；币种：人民币（表头 `(RMB)` / `(RMB ’000)`）。
PDF：<https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0828/2026082802450.pdf>

| # | slug | 指标 | 分部 / 范围（口径） | 单位 | 本期 | 上年 | 逐字标签行 |
|---|---|---|---|---|---|---|---|
| 1 | `restaurant_count_group` | 门店数 | 集团餐厅总数（脚注拆分为自营 573 + 特许 33） | 家 | **606** | 729 | `Number of restaurants(1) ... 606 729`（时点 as_of 2026-06-30） |
| 2 | `table_turnover_tai_er` | 翻台率 | 太二自营餐厅 | 次/天 | **3.5** | 3.1 | `Table turnover rate(3)` / `Tai Er (self-operated)` |
| 3 | `table_turnover_song_hot_pot` | 翻台率 | 怂火锅自营餐厅 | 次/天 | **2.0** | 2.5 | 同上 / `Song Hot Pot` |
| 4 | `table_turnover_jiu_mao_jiu` | 翻台率 | 九毛九自营餐厅 | 次/天 | **2.3** | 2.5 | 同上 / `Jiu Mao Jiu (self-operated)` |
| 5 | `average_spending_per_customer_tai_er` | 顾客人均消费 | 太二自营餐厅 | 元/人 | **74** | 73 | `Average spending per customer (RMB) (4)` / `Tai Er (self-operated)` |
| 6 | `average_spending_per_customer_song_hot_pot` | 顾客人均消费 | 怂火锅自营餐厅 | 元/人 | **106** | 99 | 同上 / `Song Hot Pot` |
| 7 | `average_spending_per_customer_jiu_mao_jiu` | 顾客人均消费 | 九毛九自营餐厅 | 元/人 | **58** | 57 | 同上 / `Jiu Mao Jiu (self-operated)` |
| 8 | `same_store_sales_tai_er` | 同店销售额 | 太二自营同店（同店基数=2025 与 2026 上半年均开业满 150 天） | 人民币元（源 `RMB ’000`） | **1,636,299,000** | 1,524,357,000 | `Same store sales (RMB ’000) (5)` / `Tai Er (self-operated)` |
| 9 | `same_store_sales_song_hot_pot` | 同店销售额 | 怂火锅自营同店 | 人民币元 | **242,672,000** | 316,214,000 | 同上 / `Song Hot Pot` |
| 10 | `same_store_sales_jiu_mao_jiu` | 同店销售额 | 九毛九自营同店 | 人民币元 | **162,092,000** | 184,217,000 | 同上 / `Jiu Mao Jiu (self-operated)` |
| 11 | `same_store_sales_change_tai_er` | 同店日均销售同比 | 太二自营同店（2026 上半年 vs 2025 上半年） | % | **7.4** | —（该表第二列属 2025-vs-2024 对照组） | `Change in same store average daily sales(6) (period-on-period) (%)` / `Tai Er (self-operated)` |
| 12 | `same_store_sales_change_song_hot_pot` | 同店日均销售同比 | 怂火锅自营同店 | % | **−22.9** | — | 同上 / `Song Hot Pot` |
| 13 | `same_store_sales_change_jiu_mao_jiu` | 同店日均销售同比 | 九毛九自营同店 | % | **−11.8** | — | 同上 / `Jiu Mao Jiu (self-operated)` |

`kpi_status='ok'`（13/13 全部带口径落地）。

> #11–#13 的 `_prior` 刻意留空：源表是 `2026 | 2025 | 2025 | 2024` 四列，
> 第二列是「2025 vs 2024」的同比对照，**不是**本期（2026 vs 2025）的上年值；
> 按位置落列后它落在第 3 列，因此不登记为 prior（宁可缺，不混口径）。

## 5. PDF 原文片段（逐条可回指）

海底捞 p7（KPI 表；`Overview` 段仅作页码定位，不消费）：

```
                                                                                       For the six months ended
                                                                                                June 30,
                                                                                              2026           2025
Average spending per guest(1) (RMB)
  Tier 1 cities(2)                                                                              104.2               105.2
  Tier 2 cities(3)                                                                               96.0                96.9
  Tier 3 cities and below(4)                                                                     90.9                91.6
 Chinese mainland restaurants                                                                    95.0                95.8
  Hong Kong, Macau and Taiwan regions                                                           196.6               205.8
Overall                                                                                          97.0                97.9
Table turnover rate(5) (times/day)
  ...
 Chinese mainland restaurants                                                                      3.9                 3.8
  Hong Kong, Macau and Taiwan regions                                                              4.5                 4.3
Overall                                                                                            3.9                 3.8
  Newly-opened restaurants(6)                                                                      4.2                 4.1
  Other restaurants                                                                                3.9                 3.8
Overall                                                                                            3.9                 3.8

Notes:
(1)      Calculated by dividing gross revenue generated from restaurant operation for the period by total number of
         guests served for the period.
(5)      Calculated by dividing the total tables served for the period by the product of total number of operation days for
         the period and average table count during the period.
```

海底捞 p8（同店表）：

```
                                                                                              2026           2025
Number of same stores(1)
  ...
Overall                                                                                              1,163
Same store sales(2) (in thousands of RMB)
  ...
Overall                                                                                 16,348,861           16,571,065
...
Average same store table turnover rate(4) (times/day)
  ...
Overall                                                                                             3.9               3.9

Notes:
(1)      Includes restaurants that had commenced operations prior to the beginning of the periods under comparison and
         opened for 150 days and above in both the six-month periods ended June 30, 2025 and 2026.
(2)      The gross revenue from restaurant operation at our same stores for the period indicated.
```

海底捞 p2（运营摘要表）：

```
                                                                      As of and for the six months
                                                                             ended June 30,
                                                                               2026           2025
Number of self-operated Haidilao restaurants                                  1,290            1,322
Number of franchised Haidilao restaurants                                        99               41
Average table turnover rate for self-operated Haidilao
  restaurants (times/day)                                                        3.9             3.8
Average spending per guest for self-operated Haidilao
  restaurants (RMB)                                                            97.0             97.9
System sales growth of Haidilao restaurants                                             0.8%
```

九毛九 p2（KEY OPERATIONAL HIGHLIGHTS）：

```
                                                                                                       2026           2025
Number of restaurants(1) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .                 606             729
Table turnover rate(3)
Tai Er (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .             3.5              3.1
Song Hot Pot. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .          2.0              2.5
Jiu Mao Jiu (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .                2.3              2.5

Average spending per customer (RMB) (4)
Tai Er (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .              74               73
Song Hot Pot. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .          106               99
Jiu Mao Jiu (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .                 58               57

Same store sales (RMB ’000) (5)
Tai Er (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .       1,636,299       1,524,357
Song Hot Pot. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .      242,672         316,214
Jiu Mao Jiu (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .            162,092         184,217

Change in same store average daily sales(6)
   (period-on-period) (%)
Tai Er (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .               7.4
Song Hot Pot. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .           (22.9)
Jiu Mao Jiu (self-operated) . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .                 (11.8)

Notes:
(1)      As of June 30, 2026, we had 573 self-operated restaurants and 33 franchised restaurants.
(3)      Calculated by dividing total tables served by the product of total restaurant operation days and average table
         count during the period.
(4)      Calculated by dividing revenue generated from the restaurant operations for the period by total customer traffic
         for the period.
(5)      Same store sales for the period refers to the revenue of all restaurants that qualified as same stores during that
         period. The Group defines its same store base to be those restaurants that opened for at least 150 days in the
         first half of both 2025 and 2026.
(6)      Same store average daily sales for the period refers to the average daily revenue of all restaurants that qualified
         as same stores during that period.
```

括号在港股财报里表示负数（`(22.9)` = −22.9%），已按此归一为 `-22.9`。

## 6. 不注册的字段（显式台账）

| 项 | 状态 | 原因 |
|---|---|---|
| 上述 24 项写入 `semantic_observations` | **不入库** | 报告期为半年（6M），现役 `granularity` 词表只有 `quarter/day/year/month/hour/week`，没有 `half`；用 `quarter`/`year` 记半年即口径造假。见 §8 |
| 海底捞 分城市层级翻台率（一线/二线/三线及以下） | **未取（一期范围外）** | 同机制可取（同一张表），但一期只登记 Overall / 大陆 / 港澳台三档；不是不可得，是范围裁剪 |
| 海底捞 `System sales growth`（0.8%） | **不注册** | 只有单值、表头无 `(%)`、且口径描述只出现在正文段落（「total sales generated by all Haidilao-branded restaurants…」），按「不读叙述」不取 |
| 九毛九 席次周转率（`Seat turnover rate`） | **未取（一期范围外）** | 同机制可取（p2/p5 两张表都有标签行 + 脚注）；一期先做翻台率，席次周转留二期 |
| 九毛九 分品牌门店明细（太二 477 / 怂 54 / 九毛九 61 …） | **未取（一期范围外）** | 门店数已用集团总数 606 覆盖任务点名的「门店数」；品牌明细属另一张表，留二期 |
| 九毛九 `Number of same store`（422 / 53 / 55） | **不可得（`column_position_mismatch` 类）** | 源表四列 `2026 2025 2025 2024` 下计数只有两列；按位置落列时上年计数落在第 3 列（2025-vs-2024 组），与「本期上年」不是同一对照，故不登记 |
| 台股 3 家、日股 4 家细分 KPI | **不可得** | 台股快照式月营收无 KPI；日股 EDINET key 未到位（`docs/restaurant-lane-mapping.md` §2） |
| 其余 8 家港股（呷哺/达势/特海/百胜中国/大家乐/大快活/奈雪/周黑鸭） | **未取（二期）** | 一期按任务书点名先做两家；其余家 payload 显式写 `not_applicable` + `no_phase1_kpi_target`，不假装已覆盖 |

## 7. 其余 8 家的 payload 实况（显式 not_applicable）

实测 run 4829 的 results 行：00520 / 01405 / 01458 / 02150 / 09658 / 09987 各 2 行
（业绩公告 + 正文报告）全部为 `kpi_status='not_applicable'` +
`kpi_reason='no_phase1_kpi_target'` + `kpi_targets=[]`；00341 / 00052 的年度业绩
落在 90 天窗口外（2026-06 披露），本窗口无 results 行。**没有空串/空数组冒充数值。**

## 8. 交付层：payload（不是 `semantic_observations`）

两个既有阻断，均先于本任务存在：

1. **粒度词表没有 `half`**。两家都是 6 个月累计；写 `quarter`/`year` 都是口径造假。
   任务 3.5 已把「新增 `half`」列为待裁项，本单不越权预判。
2. **趋势门 ≥24 期**（12–23 期需 `short_window` 声明）。单次 90 天窗口内每家只有
   1 期业绩（±1 期正文报告），远不够。

因此本期交付形态 = **事件 payload 携带结构化 KPI**（`kpi_<slug>_*`），与
`headline_*` 同一条记录。查询路径：

```sql
SELECT payload->>'stock_code'                  AS code,
       payload->>'release_date'                AS released,
       payload->>'kpi_status'                  AS status,
       payload->>'kpi_reason'                  AS reason,
       payload->>'kpi_targets'                 AS targets,
       payload->>'kpi_table_turnover_overall'  AS turnover,
       payload->>'kpi_table_turnover_overall_unit'    AS turnover_unit,
       payload->>'kpi_table_turnover_overall_caliber' AS turnover_caliber
FROM crawl_items ci JOIN crawl_runs r ON r.id = ci.run_id
WHERE r.source = 'hkex-disclosure' AND r.status = 'success'
  AND payload->>'ann_type' = 'results'
ORDER BY payload->>'stock_code', payload->>'release_date' DESC;
```

### 字段契约（每个登记的 slug 七个字段）

| 字段 | 含义 |
|---|---|
| `kpi_<slug>` | 值（字符串）。金额=原币单位整数（千元 ×1000）；百分比=百分数；次数/家=原值 |
| `kpi_<slug>_unit` | `times_per_day` / `percent` / `CNY` / `CNY_per_person` / `count` |
| `kpi_<slug>_caliber` | 单行口径：`period=起..止 (nM)`（或 `as_of=`）+ `segment=` + `unit=` + `label=` + `row=` + `definition=`（脚注原文）+ `prior_period=` + `source_scale=` |
| `kpi_<slug>_status` | `ok` / `unavailable` |
| `kpi_<slug>_reason` | 不可得原因码（`ok` 时为 null） |
| `kpi_<slug>_prior` | 同表上年列（逐字；无上年列或不属同一对照组时为 null） |
| `kpi_<slug>_evidence` | `[section]` / `[period]` / `[column]` / `[row]` / `(n) 脚注原文` 逐字行，最多 8 条 |

行级汇总：`kpi_status`（`ok` / `partial` / `unavailable` / `not_applicable`）、
`kpi_reason`、`kpi_targets`。**只有被选中的那条业绩公告行**带 `kpi_<slug>_*`；
同期正文报告行写 `unavailable` + `not_selected`（点名被选中公告日期）；非 results
行写 `not_applicable` + `ann_type_not_results`；未解析行上的每一条 slug（含
`_EXPLICIT_UNAVAILABLE` 那三条）都带**行级**原因，不把「只在叙述段」这类针对某一期
文档的断言挪用到没抽过的行上。

## 9. 实测与确定性

| 项 | 值 |
|---|---|
| run | **4829**（2026-11-08 01:52 UTC，`FD_IMAGE_TAG=local-hk-kpi`） |
| status | **success**，rows=226（默认 90 天窗口，94.5 s） |
| 06862 | `kpi_status=partial`：11/11 登记指标全部落地 + 3 项显式不可得；非业绩公告 9 行 = `not_applicable` + `ann_type_not_results` |
| 09922 | `kpi_status=ok`：13/13 落地；非业绩公告 6 行 = `not_applicable` + `ann_type_not_results` |
| 非目标 6 家 | `not_applicable` + `no_phase1_kpi_target`（results 行 12 条） |
| 正文报告行 | `unavailable` + `not_selected`（点名 2026-08-25 / 2026-08-28） |

**crawl 层每次运行都会整批重写 payload 行**（同一 source 多次 run 各存一份），
所以库里同一事件会看到多份记录——这是既有爬取层行为，不是本单元重复写入；
读路径按 `crawl_runs.id` 取最新一次，或按 `payload->>'scraped_at'` 去重。

## 10. 挂账 / 待裁

1. **`half` 粒度是否进观测表**（本单不裁）：新增 `half`（推荐）/ 只做年度 /
   折季度（不采纳）。裁前 KPI 与头条都只落 payload。
2. **`financials.*` 无币种维度**：同名概念跨市场混写（A股 CNY / 美股 USD / 港股
   CNY+HKD+USD）。KPI 面用 `_unit` 字段逐条带币种，不受影响；入观测前须裁。
3. **一期公司范围**：还差 8 家港股；建议二期按「同一 spec 引擎 + 逐家脚注登记」
   扩展（呷哺/达势/奈雪等的摘要表是否同构需逐份实测，不假设）。
4. **`same_store_count_overall` 的单值列**：源表上年列空白，已按
   `allow_single` 只登记本期值（`prior=null`），不推断上年同店家数。
5. **`System sales growth`**：只有正文段落给定义，按「不读叙述」不取；若业务上
   需要，应作为「允许消费正文定义」的口径变更单独裁决，而不是放宽本单元。
