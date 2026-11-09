# 农业行业垂直线（申万农林牧渔）— W1 精选序列清单

> change：`add-agriculture-industry-sources` · 任务 5.1 · 定稿日期 2026-11-08
> 数据面：生产观测库 `fd_open_data`（mesh `100.64.0.3:30432`），长形表 `semantic_observations`
> 口径契约：`industry-observation-supply`（自然键 `(concept_id, entity_type, entity_id, date, granularity, source_used)`，
> `INSERT ... ON CONFLICT DO NOTHING` 只增不改，缺测不插值不补零）

## 0. 先说清一件事：本波的「真实窗口」来源与餐饮波0不同

餐饮波0 的单元**已在产**，所以那份清单的窗口是**查库所得**。农业 W1 的 6 个新单元
（`agri-casde` / `moa-fishery` / `moa-machinery` / `usda-wasde` / `cncotton` /
`moa-variety-approval`）**还没 push、还没点亮**，crawl 库里没有 payload。

因此本清单每条窗口都是 **2026-11-08 本机真跑实测**（`NO_PROXY='*'`，逐单元执行入口函数，
统计真实返回的 period 集合与行数），**不是查库值，也不是估算值**。
5.3 入库后必须**重新查库复核一次**，并以库值为准改写本文件——这一步不许省。

## 1. 这份清单管什么

把 W1 建成的单元里**真实存在**的历史抽成观测、过命名门，先把「产能景气 / 供需平衡 /
成本链」三条研究线立起来。每条序列标注：

- **源单元（`source_used`）**：行级溯源，一条序列只能有一个来源；
- **源侧序列 ID**：上游真实标识（`casde item` / `short_desc` / `item` / `product+flow`），不是自造编号；
- **实测窗口**：本机真跑的起止期与期数；
- **趋势门**：≥24 期正常入库；12–23 期按 `short_window` 登记并带声明；<12 期本波不入库；
- **口径注记**：「看起来像 A 实际是 B」的地方必须写在旁边。

**本波合计 41 条序列**（其中 3 条复用既有概念、1 条为事件面不入库）。

---

## 2. 国内官方口径（MOA / CASDE）

### 2.1 CASDE 农产品供需平衡（`source_used = agri-casde`，月频，20 条）

通道 `www.agri.cn/sj/gxxs/`（**不在 moa.gov.cn**）。一期 = 五张表（玉米/大豆/棉花/
食用植物油/食糖），每表并列「上月预测 / 本月预测 / 实际」三态。本波**只取
`forecast_kind=current`（本月预测）**——三态混进同一条序列会造出锯齿假象。

实体：`entity_type=country`，`entity_id=1`（中国）——沿用库内既有农业概念的实体词表（`agri.hog.price.spot` 等均为 `country`）。

| # | 概念码 | 源侧 `item` | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `agriculture.cn.corn.production.monthly` | 产量 | month | 2024-05 → 2026-10（**25 期**，HTML 表深度，见 §6） | 万吨 | verified |
| 2 | `agriculture.cn.corn.import.monthly` | 进口 | month | 同上（25 期） | 万吨 | verified |
| 3 | `agriculture.cn.corn.consumption.monthly` | 消费 | month | 同上（25 期） | 万吨 | verified |
| 4 | `agriculture.cn.corn.balance_change.monthly` | 结余变化 | month | 同上（25 期） | 万吨 | verified |
| 5 | `agriculture.cn.soybean.production.monthly` | 产量 | month | 同上（25 期） | 万吨 | verified |
| 6 | `agriculture.cn.soybean.import.monthly` | 进口 | month | 同上（25 期） | 万吨 | verified |
| 7 | `agriculture.cn.soybean.crush_consumption.monthly` | 压榨消费 | month | 同上（25 期） | 万吨 | verified |
| 8 | `agriculture.cn.soybean.balance_change.monthly` | 结余变化 | month | 同上（25 期） | 万吨 | verified |
| 9 | `agriculture.cn.cotton.production.monthly` | 产量 | month | 同上（25 期） | 万吨 | verified |
| 10 | `agriculture.cn.cotton.import.monthly` | 进口 | month | 同上（25 期） | 万吨 | verified |
| 11 | `agriculture.cn.cotton.ending_stocks.monthly` | 期末库存 | month | 同上（25 期） | 万吨 | verified |
| 12 | `agriculture.cn.cotton.sown_area.monthly` | 播种面积 | month | 同上（25 期） | 千公顷 | verified |
| 13 | `agriculture.cn.edible_oil.production.monthly` | 生产 | month | 同上（25 期） | 万吨 | verified |
| 14 | `agriculture.cn.edible_oil.import.monthly` | 进口 | month | 同上（25 期） | 万吨 | verified |
| 15 | `agriculture.cn.edible_oil.consumption.monthly` | 国内消费 | month | 同上（25 期） | 万吨 | verified |
| 16 | `agriculture.cn.edible_oil.balance_change.monthly` | 结余变化 | month | 同上（25 期） | 万吨 | verified |
| 17 | `agriculture.cn.sugar.production.monthly` | 食糖产量 | month | 同上（25 期） | 万吨 | verified |
| 18 | `agriculture.cn.sugar.import.monthly` | 食糖进口量 / 进口量 / 进口（同名三别名） | month | 同上（25 期，别名并集） | 万吨 | verified |
| 19 | `agriculture.cn.sugar.consumption.monthly` | 食糖消费量 / 消费量 / 消费（同名三别名） | month | 同上（25 期，别名并集） | 万吨 | verified |
| 20 | `agriculture.cn.sugar.balance_change.monthly` | 结余变化 | month | 同上（25 期） | 万吨 | verified |

⚠ **食糖表与其余四表不同构（2026-11-08 实测纠错）**：
1. **没有「期末库存」行**，只有「结余变化」——故第 20 条取结余变化，不是库存。
   初版按对称结构把 sugar 写成 `产量/进口/消费/期末库存`，结果产量与库存两条
   一条都匹配不上、进口/消费各只有 20 期被 24 期趋势门整组丢掉（一次真跑暴露出来的）。
2. **行标签改名两次**：同一批数值（500 / 1580 / 16）依次印作
   `食糖进口量`（2024-05…11）→ `进口量`（2024-07/08）→ `进口`（2024-09 起），
   消费、出口同理。三者为**同一条序列的三个名字**，靠 `(code, period)` 去重并集，
   重叠期数值完全一致（已逐期核对），最终 25 期连续。

**不取的三类**：
- `_low` / `_high` 价格区间行（同一 `item` 拆三行，属区间端点，不是独立序列）；
- 价格类行（`国内玉米产区批发均价` 等）——CASDE 是**预测**价不是成交价，
  与「官方统计监测口径可接」的现货序列不同构，本波不入库（要看走势请直接看板展示预测区间）；
- `forecast_kind=previous/actual` 两态（同上，避免同序列锯齿）。

### 2.2 生猪产能（`source_used = moa-hog-price`，季频，3 条）

入口 `ztzl/szcpxx/jdsj/YYYY/YYYYMM/`，**只有季末月 03/06/09/12 有数值**（页脚原文：每年
1/4/7/10 月 20 日发布）。实测 `2021Q4 → 2026Q3` 连续 **20 个季度**。

| # | 概念码 | 源侧序列 | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 21 | `agriculture.cn.hog.breeding_sow_inventory.quarterly` | `能繁母猪存栏`（序号 1） | quarter | 2021Q4 → 2026Q3（20） | 万头 | verified |
| 22 | `agriculture.cn.hog.inventory.quarterly` | `生猪存栏`（序号 2） | quarter | 同上（20） | 万头 | verified |
| 23 | `agriculture.cn.hog.breeding_sow_vs_target.quarterly` | 页面自带「相当于调控目标/正常保有量的 X%」 | quarter | 2021Q4 → 2026Q3，**实有 15 期**（见下方缺口说明） | % | verified · **阈值主判据** |

注：口径措辞**逐年变过**——2026 年写「相当于调控目标的 96.0%」，2025 年写
「相当于正常保有量的 103.5%，产能正常，处于绿色区域」。本单元两种都能取到，序列不断；
但看板注释必须写明「2025 年为正常保有量口径、2026 年为调控目标口径」，**不可直接跨年比大小**。

⚠ **2026-11-08 实测到的源侧缺口**：#23 这条比率在 `2021Q4–2022Q4` 有值、
**`2023Q1–2024Q1` 整五季源站根本没印**、`2024Q2` 起恢复。所以 #23 真实是 **15 期**，
不是 20 期；存栏 (#21/#22) 才是完整 20 季。抽取器会把缺口按季度名逐条写进 notes
（`GAP 2023Q1, 2023Q2, 2023Q3, 2023Q4, 2024Q1`），**不补齐、不外推**。
阈值层读 #23 时必须知道 2023–2024Q1 无从比较——那段只能退回用 #21 的绝对量。

### 2.3 生猪与猪肉周价（`source_used = moa-hog-price`，周频，**复用既有概念**）

这三个概念**已存在库中**（餐饮波0 注册，各 15 条，2026-07-20 → 2026-10-26）：
`agri.hog.price.spot`、`agri.pork.price.market`、`agri.pork.price.wholesale`。
本波**不新建**，只把 W1 扩展后的更长历史按同一 `source_used` 追加。

| # | 概念码 | 监测口径 | 粒度 | 本机实测（`max_pages=1`） | 单位 |
|---|---|---|---|---|---|
| 24 | `agri.hog.price.spot` | 活猪（500 个县集贸市场和采集点） | week | 2026-09 第4周 → 2026-10 第5周（6 周） | 元/公斤 |
| 25 | `agri.pork.price.market` | 猪肉集贸（同上监测面） | week | 同上（6 周） | 元/公斤 |
| 26 | `agri.pork.price.wholesale` | 猪肉批发（**200 个批发市场**，另一口径） | week | 同上（6 周） | 元/公斤 |

注：`date` 取 `date_range` 的**起始日**（如 `2026-10-26~2026-11-01` → `2026-10-26`），
`granularity='week'`；`obs_month` 按起始日归月。集贸与批发**两个监测面绝不混**——
这是既有单元的 `monitor_scope` 纪律，沿用。

### 2.4 渔业（`source_used = moa-fishery`，6 条）

`yyj.moa.gov.cn` 正文内嵌表。**两种粒度绝不合并**：`year` = 年度公报，
`cumulative_month` = 年内累计到第 N 月。

| # | 概念码 | 源侧 `item` | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 27 | `agriculture.cn.fishery.output.annual` | 总产量 | year | 2020 → 2025（5 期，缺 2022，见 §6） | 万吨 | **不入库（<12）** |
| 28 | `agriculture.cn.fishery.output.cumulative_month` | 总产量 | month | 2024-07 → 2026-09（18 期） | 万吨 | verified · **short_window(18)** 见 §6 |
| 29 | `agriculture.cn.fishery.mariculture.cumulative_month` | 海水养殖 | month | 同上（18 期） | 万吨 | verified |
| 30 | `agriculture.cn.fishery.mariculture.annual` | 海水产品 | year | 2020 → 2025（5 期） | 万吨 | **不入库（<12）** |
| 31 | `agriculture.cn.fishery.freshwater_culture.cumulative_month` | 淡水养殖 | month | 同上（18 期） | 万吨 | verified |
| 32 | `agriculture.cn.fishery.capture.cumulative_month` | 海洋捕捞 | month | 同上（18 期） | 万吨 | verified |

### 2.5 农机（`source_used = moa-machinery`，年频，3 条）

`njhs.moa.gov.cn/nyjxhqk/` 农业机械化发展统计公报，实测 **2022–2025 四期 × 19 指标 = 76 行**。

| # | 概念码 | 源侧 `item` | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 33 | `agriculture.cn.machinery.operated_area.annual` | `operated_area_mu_times`（作业面积） | year | 2022 → 2025（4） | 亿亩次 | verified · **short_window(4)** |
| 34 | `agriculture.cn.machinery.mechanization_rate.annual` | `mechanization_rate`（综合机械化率） | year | 2022 → 2025（4） | % | verified · **short_window(4)** |
| 35 | `agriculture.cn.machinery.tractor_count.annual` | `tractor_count`（拖拉机保有量） | year | 2022 → 2025（4） | 万台 | verified · **short_window(4)** |

注：4 期**不到趋势门 24 期的六分之一**，本波**不入观测库**，只在看板以「年度公报快照」展示
（见 §7）。其余 16 个指标（收割机、无人机、合作社数、分作物机械化率等）同样只在
payload 里留存，不注册。

---

## 3. 国际官方口径

### 3.1 USDA WASDE（`source_used = usda-wasde`，月频，6 条）

`.txt` 定宽表；实测两期（`WASDE-675`=2026-09、`WASDE-676`=2026-10），8 个品类 × 3 区域 ×
5 项 = 960 行/两期。本波只取 **`region=World`** 的 `output` 与 `ending_stocks`。

| # | 概念码 | 源侧 `commodity` | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 36 | `agriculture.world.wheat.production.monthly` | Wheat | month | 2026-09 → 2026-10（2） | 百万吨 | verified · **short_window(2)** |
| 37 | `agriculture.world.wheat.ending_stocks.monthly` | Wheat | month | 同上 | 百万吨 | verified · **short_window(2)** |
| 38 | `agriculture.world.coarse_grains.production.monthly` | Coarse Grains | month | 同上 | 百万吨 | verified · **short_window(2)** |
| 39 | `agriculture.world.rice.production.monthly` | Rice, milled | month | 同上 | 百万吨 | verified · **short_window(2)** |
| 40 | `agriculture.world.cotton.production.monthly` | Cotton | month | 同上 | 百万 480 磅包 | verified · **short_window(2)** |
| 41 | `agriculture.world.oilseeds.production.monthly` | Oilseeds | month | 同上 | 百万吨 | verified · **short_window(2)** |

注：`season` 有三个（2024/25 / 2025/26 / 2026/27），**同一期会同时出三条季节线**。
本波只取**当期主预测季**（`forecast_month` 等于发布月的那个 `basis=forecast` 行），
否则一条序列里混三个年度。**2 期远不到门槛**，本波不入库，仅看板展示最新一期截面。

### 3.2 WFP 全球粮价（`source_used = wfp-food-price`，月频，**本波不注册**）

新库 `global-wfp-food-prices` 是按年分片（40 片 ≈620MB），实测 2026 年分片覆盖
**2026-01 → 2026-11 共 11 个月**，单月 5763 行 / 16 国 / 6 大类。
粒度是「国家 × 市场 × 品种 × 购销环节」，**每条序列只有十几个点**且国家覆盖不全
（16 国而非 90+），不满足趋势门。**本波只作价格指数看板的横向对照层**，
概念注册推到 W2 之后（等积累满 24 个月）。

---

## 4. 成本链（化肥 / 棉花）

### 4.1 化肥月度进出口（`source_used = fert-cn`，月频，**0 条入库**）

`authority: industry`（中肥网是**商业门户**，不是协会）。实测窗口 `2026-06 → 2026-08`
（3 期，76 行，17 个品种 × 进/出两向）。

| 候选概念码 | 源侧 | 实测期数 | 判定 |
|---|---|---|---|
| `agriculture.cn.fertilizer.urea_export.volume.monthly` | `product=尿素, flow=export, volume_wan_t` | 3 | **不入库**（3 期 ≪ 24） |
| `agriculture.cn.fertilizer.urea_export.avg_price.monthly` | 同上 `avg_price_usd_t` | 3 | **不入库** |
| `agriculture.cn.fertilizer.dap_export.volume.monthly` | `product=磷酸二铵, flow=export` | 3 | **不入库** |

注：全档深度未全量实测（列表 43 页，逐页 sleep，单次跑超 10 分钟未走完），
**如实记为「近期三期已实测，历史深度未测」**。要看成本链走势，本波先走既有在产的
`imf-pcps`（化肥价格指数）与 DCE/ZCE 期货（§5）。

### 4.2 棉花市场报告（`source_used = cncotton`，月频，4 条）

实测 `limit=400` → **214 行 / 36 期（2022-08 → 2025-08）**。PDF 文本层抽取，不 OCR。

| # | 概念码 | 源侧 `item` | 粒度 | 实测窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 42 | `agriculture.cn.cotton.production.cottonnet.monthly` | `production` | month | 2022-08 → 2025-08（36） | 万吨 | **与 #9 同概念不同源，须并存不入同一序列** |
| 43 | `agriculture.cn.cotton.ending_stocks.cottonnet.monthly` | `ending_stocks` | month | 同上（34 期中 34 有条目） | 万吨 | 同上 |
| 44 | `agriculture.cn.cotton.cotlook_a.monthly` | `cotlook_a_index`（仅 `prev_month_avg`） | month | 18 期 | 美分/磅 | **short_window(18)** |
| — | `agriculture.cn.cotton.import.cottonnet.monthly` | `import_cum_volume` | month | 6 期 | 万吨 | **不入库**（6 期 ≪ 24） |

**同概念双源并存的处置**：CASDE 的棉花产量（#9）与 cncotton 转述 USDA 的产量是
**两个口径**，自然键带 `source_used` 所以能并存；但看板**只能选一个口径做主线**，
另一个作对照，且必须标出「cncotton 为转述 USDA 预测、非中国官方口径」。
**cotlook_a_index 只有 22 期**，按 short_window 登记（12–23 区间）。

---

## 5. 事件面与复用（不产序列）

| 源 | 处置 |
|---|---|
| `moa-variety-approval` | **事件面，零观测**（详情页 `<table>`=0，清单在 `.doc` 附件）。W1 实测 2026-11 六条事件。不进观测库，走柏讯事件条 |
| `dce` / `zce` 期货（玉米/豆粕/白糖/棉花） | **复用既有单元**，本波不新建。成本链看板取它们作价格侧 |
| `un-comtrade` | 海关替代口径，**复用**（海关站加速乐 412/503/504，见留痕 ②） |
| `imf-pcps` | 化肥/糖/棕榈油价格指数，**复用**（成本链价格侧参考） |
| `fao-food-price` | 粮价指数，**复用**（宏观对照） |
| `usda-psd` / `amis-market` | 供需平衡的历史长序列，**复用**（CASDE 只到 2025-01，更长历史看它们） |

---

## 6. 趋势门与 short_window 判定（逐条对账）

| 序列 | 实测期数 | 门档 | 处置 |
|---|---|---|---|
| #21–22 存栏（季） | 20 | 12–23 | **short_window(20)**，登记并带「2021Q4 起」声明 |
| #23 能繁 vs 目标（季） | **15**（2023Q1–2024Q1 源站未印） | 12–23 | **short_window(15)**，缺口逐季留痕、不补齐 |
| #1–20 CASDE | 25 | ≥24 | 正常入库（刚好过门槛，见缺口说明） |
| #27 渔业年度 | 5（2020/21/23/24/25） | <12 | 不入库，看板作年度快照 |
| #28–32 渔业月累计 | 18 | 12–23 | **short_window(18)** |
| #33–35 农机 | 4 | <12 | 不入库 |
| #36–41 WASDE | 2 | <12 | 不入库 |
| 棉花 cotlook_a | 18 | 12–23 | **short_window(18)** |
| 棉花 production/ending_stocks | 36 / 34 | ≥24 | 正常入库 |
| 化肥 | 3 | <12 | 不入库 |
| WFP | 单国十几期 | <12 | 不入库 |

**两处源侧缺口（如实记录，不补造）**：
1. **CASDE 有两个不同的「深度」，别混**：
   - **列表期数 = 117 期**（No.1 = 2016-07 → No.124 = 2026-10，缺 7 期：`No.30` 2018-12、
     `No.37` 2019-07、**`No.114–118` 2025-12 → 2026-04 连缺五期**）；
   - **可解析期数 = 25 期**（**实测只有 No.95 = 2024-05 起是 HTML 表**；更早期次是
     正文 + PDF，本单元不解析）。**入库深度按 25 期算**，趋势门刚好过线。
   - 另有季节维度：一期之内并列两个营销年度（实测 2026-08：玉米 2025/26=30124 estimate、
     2026/27=30600 forecast），本波**只取 `basis=forecast` 的当期季**，否则同序列每月两个点。
2. **渔业缺月**：2025 年 3 月、6 月无发布（另 2024 年只有 4 个月、2026 年只有 6 个月）；
   年度只有 2020/2021/2023/2024/2025 **五个年度，没有 2022、也没有 2026**。同样不内插。

## 7. 未入库序列怎么展示（诚实边界）

不入库 ≠ 不展示，但**必须标清性质**：

- 农机 / WASDE / 渔业年度 / 化肥 → **「年度公报或周月公报快照」**，图上标注「单期截面，
  非时间序列」，不画趋势线；
- WFP → 「横向对照」，不是趋势；
- 事件面（品种审定）→ 柏讯事件条，不进任何数值图。

## 8. 复现路径（本机实测）

```bash
cd /Users/chengsishi/finddata/fd-industry-data
export NO_PROXY='*' no_proxy='*'

# CASDE（窗口钉单期，8 秒级）
.venv/bin/python -c "import importlib.util as u; s=u.spec_from_file_location('m','spiders/agri-casde/spider.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print(len(m.run_agri_casde(limit=5000, window_start='2026-05-01', window_end='2026-05-31')))"
# → 343

# 产能（季，20 季）
.venv/bin/python -c "import importlib.util as u; s=u.spec_from_file_location('m','spiders/moa-hog-price/spider.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print(len(m.run_moa_hog_capacity(limit=40)))"
# → 40

# 渔业 / 农机 / WASDE / 棉花
# → 115 / 76 / 960（两期）/ 214（36 期）
```

## 9. 不可得项（详见侦察簿「W1 留痕」六条 + 待裁决一条）

NDRC 猪粮比价 / 海关农产品贸易 / 林业 / 糖业协会 / 农药 / OECD-FAO + **饲料工业协会（待用户裁决 OCR）**。
本清单**不为它们造任何序列**；阈值层主判据已改用 #23（能繁母猪 vs 保有量）。
