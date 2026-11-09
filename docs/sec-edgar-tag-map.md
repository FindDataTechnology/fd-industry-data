# SEC EDGAR XBRL tag 映射表（逐家留档）

> change `add-restaurant-vertical` · 任务 3.3 · 实测于 2026-11-07/08
> 数据面：`spiders/sec-edgar-facts`（run 4812, `quarters_back=26`, 2,257 行）
> 口径：只取 `us-gaap` **精确 tag**（无前缀/模糊匹配），只取 10-Q/10-K，只取单季窗（70–110 天）

## 1. 概念映射（tag → 既有概念码）

| 概念码 | 接收的 tag | 说明 |
|---|---|---|
| `financials.revenue` | `Revenues` / `RevenueFromContractWithCustomerExcludingAssessedTax` / `SalesRevenueNet` | 三家 tag 是同一经济量的不同申报习惯，合并到同一概念；**同一期末只保留一条** |
| `financials.operating_profit` | `OperatingIncomeLoss` | 营业利润 |
| `financials.net_income` | `NetIncomeLoss` | 净利润（含少数股东权益口径随申报原文） |
| *（未映射）* | `RestaurantRevenue` | 分部/餐厅收入口径，属细分面 3.5，**不做推断**；payload 保留 |
| *（未映射）* | `RevenueFromContractWithCustomerIncludingAssessedTax` | **含代收税**口径（与 Excluding 差一个销项税）。2026-11-08 因 HYFM 换用该 tag 而加入白名单：payload 留证，但不并入同一序列——拼接会伪造一个不存在的营收跳升 |

## 2. 逐家可用性（实测）

| 公司 | 营收 tag | 营收期数 | 营业利润期数 | 净利期数 | 首个期末 |
|---|---|---|---|---|---|
| MCD | `Revenues` | 24 | 24 | 24 | 2019-06-30 |
| SBUX | `Revenues` | 24 | 24 | 24 | 2019-06-30 |
| YUM | `Revenues` | 24 | 24 | 24 | 2019-09-30 |
| CMG | `Revenues` | 24 | 24 | 24 | 2019-09-30 |
| QSR | `Revenues` | 24 | 24 | **8** | 2019-09-30 |
| DRI | `RevenueFromContractWithCustomerExcludingAssessedTax` | 24 | 24 | 24 | 2019-05-26 |
| DPZ | `Revenues` | 24※ | 24 | 24 | 2019-06-16 |
| WEN | `Revenues` | 24 | 24 | 24 | 2019-09-29 |
| TXRH | `RevenueFromContractWithCustomerExcludingAssessedTax` | 24 | 24 | **16** | 2019-09-24 |
| BROS | `RevenueFromContractWithCustomerExcludingAssessedTax` | **19 ✗** | **19 ✗** | **19 ✗** | 2020-09-30 |
| WING | `Revenues` | 24 | 24 | 24 | 2019-06-29 |
| EAT | `Revenues` | 24 | 24 | 24 | 2019-06-26 |

※ DPZ 的财报期为 83 天（52/53 周财年），在 `quarters_back=24` 窗口下单季窗只有 23 个期末 → 差 1 期未过趋势门；把窗口放到 **26 季度**后正常入库（这是把窗口放宽，**没有放宽门槛**）。

✗ **未入库的（显式记录，不是静默丢弃）**：

- `BROS` 营收/营业利润/净利：19 期 < 24 期门（2021 年上市，申报历史不足）；
- `QSR` 净利 8 期、`TXRH` 净利 16 期：这两家用不同 tag/期间报净利，单季窗内可归一的期数不足 24；
- 以上待季度累积或按 `short_window` 另行裁定，**不插值、不补季**。

## 3. 与 10-Q 的核对（4 家，最新季度）

| 公司 | 期末 | tag | 值（USD） | 交叉核对 |
|---|---|---|---|---|
| MCD | 2026-09-30 | `Revenues` | 7,157,000,000 | companyfacts `filed=2026-11-06`，与 10-Q 一致（原始文档 `mcd-20260930.htm` 可按 accession 回指） |
| SBUX | 2026-06-28 | `Revenues` | 9,322,700,000 | 财年 Q3（SBUX 财年 9 月结束） |
| YUM | 2026-09-30 | `Revenues` | 2,054,000,000 | 与 10-Q 一致 |
| CMG | 2026-09-30 | `Revenues` | 3,293,162,000 | 与 10-Q 一致 |

净利同期末：MCD 2,486,000,000 / YUM 1,623,000,000 / CMG 339,874,000 / SBUX 1,045,300,000。

> 核对方式：companyfacts 的 `filed` + `accn` 与 `primary_doc_url` 指向同一份申报；数值取自申报本身（XBRL 是申报的机器可读形态），没有二次计算。

## 4. 分部字段（RevenueBySegment）真实结论

**不可得**。`companyfacts` 的 XBRL 事实不带维度成员（dimension/member），无法按分部切开。
要分部/同店/系统销售额等口径，必须解析 10-Q 正文（`primary_doc_url` 已提供入口）——
属**细分面 3.5**，本车道不做、也不假装有。

## 5. 重述与去重

- spider：同一 `(tag, start, end)` 多条时按 `filed` 取最新（重述最新申报胜出）；
- extractor：再按 `(concept, entity, end)` 取 `filed` 最新一次，防两条不同 tag 在同一期末打架；
- YTD 行（跨度 >110 天）显式剔除并计数：混入会造成同键冲突与量级误读（如 9 个月累计 VS 单季）。

## 3. 花卉线（add-flower-vertical，2026-11-08 实测）

宇宙 5 家美股（`docs/flower-universe.yaml` 中 `lanes` 含 `sec-edgar-facts` 者）：FLWS / CENT / SMG / HYFM / EDBL。
底账 run 4836（`quarters_back=26`, 831 行）与 4837（加 Including tag 后 835 行）；抽取产出 **329 个观测点**，零条被趋势门剔除。

| 公司 | 营收 tag | 营收期数 | 营业利润期数 | 净利期数 | 区间 |
|---|---|---|---|---|---|
| FLWS | `Revenues` | 26 | 26 | 26 | 2018-04-01..2026-09-27 |
| CENT | `RevenueFromContractWithCustomerExcludingAssessedTax` | 26 | 24 | 26 | 2018-12-29..2026-06-27 |
| SMG | `RevenueFromContractWithCustomerExcludingAssessedTax` | 26 | 24 | 20 | 2018-12-29..2026-06-27 |
| HYFM | `…ExcludingAssessedTax` →（2026 起）`…IncludingAssessedTax` | 18 | 20 | 20 | 2020-03-31..2026-06-30 |
| EDBL | `RevenueFromContractWithCustomerExcludingAssessedTax` | 17 | 13 | 17 | 2021-03-31..2026-06-30 |

口径要点（每一条都影响「能不能画」）：

1. **HYFM 营收有一条口径断点**：2025-09-30 之后该 filer 改用 `IncludingAssessedTax`，与既往前序不同源，
   故 2026 三个季度在 `financials.revenue` 上是 **gap**（有证据行、无观测行），不是缺数据、更不是营收归零。
2. **短史不套 24 期门**：SMG 20 期、HYFM 20 期、EDBL 17 期（营业利润仅 13 期）是上市/申报历史短，
   按事实保留，看板层以 `short_window` 声明；不插值、不补季。
3. **分部收入仍不可得**：companyfacts 已剥维度成员，须解析 10-K instance（`awaken-flower-report-ingest`
   的 `xbrl-segment-extraction`）。FLWS 的 `flower_revenue_share=1.0` 来自宇宙行的分部构成结论，
   不来自本单元。
4. **Q4 缺口照旧**：12 月财年（FLWS）的 Q4 只出现在 10-K 的年度 duration 里，frame 过滤后无季度 Q4 行——
   这是既有覆盖洞，前端按季度看会少一格，不能视为断流。
