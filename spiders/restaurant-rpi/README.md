# restaurant-rpi — 美国餐馆业绩指数（NRA Restaurant Performance Index, RPI）

美国全国餐馆协会（National Restaurant Association）发布的月度综合指数，跟踪美国餐饮
行业健康度。每月最后一 个工作日发布；指数以 **100 为中性水平**（>100 扩张、<100 收缩；
源文口径，非旧版 1995=100）。由 Current Situation Index 与 Expectations Index 两个等权
分项构成，各含 4 项指标。

## 实测端点（2026-11-07 测）

| 端点 | 状态 | 说明 |
|---|---|---|
| `https://restaurant.org/research-and-media/research/restaurant-economic-insights/restaurant-performance-index/` | 200（直连 2.1s） | 官方页（正文含当月 RPI 叙述 + 「Read the full RPI report」PDF 链接）。**工单给的旧链 `.../economists-notebook/additional-research/performance-index/` 已 404**；404 页导航给出旧路径 `.../economists-notebook/restaurant-performance-index/`（200，与现行页同内容，88051 字节双页一致） |
| `https://restaurant.org/NRA/media/Research/RPI/2024/RPI-September-2026.pdf` | 200（直连，647 KB，3 页） | 当月 RPI 报告 PDF = 本单元结构化数据面。URL 规律 `/NRA/media/Research/RPI/2024/RPI-<Month>-<Year>.pdf`（目录段实测恒为 `2024`，CMS 惯例不随报告年份变） |

鉴权：无（公开页面与 PDF，无需 token/cookie）。
网络模式：**直连**（本机 2026-11-07 全部探测直连即通，未动用代理）。spider 实现了
「直连失败 → 经 `http://127.0.0.1:7890` 重试一次」兜底，每 URL 至多两次尝试、30s 超时。

## 历史回补（`months_back`，有界）

单版报告只有最近 5 个月滚动窗，但同目录的旧版 PDF 可按 URL 规律回取：

```
https://restaurant.org/NRA/media/Research/RPI/2024/RPI-<Month>-<Year>.pdf
```

目录段恒为 `2024`（CMS 惯例，不随报告年份变），`<Month>` 为英文月份全名、
`<Year>` 为报告版次年份。2026-11-07 实测 Sep-2025…Sep-2026 共 13 版全部 200
（`RPI-January-2020` 及更早 404，归档深度未到 2007 年）。

入口参数 `run_restaurant_rpi(limit, months_back=0)`：

- `months_back=0`（缺省）：与旧行为完全一致——只取当期报告（页面发现的 PDF），
  返回其 5 个月滚动窗。
- `months_back=N`（1..18）：除当期报告外，按 URL 规律再取往前 N 期月报，
  每期**只发一次请求**（直连、不重试、不走代理兜底），把各版 5 个月窗合并。
- **硬上限 18**：`months_back > 18` 或为负 → `ValueError`（防止误传大数把单次
  运行变成 hammer）。
- **去重取舍（确定性）**：按「越新的报告版越优先」——当期版先入，随后按期次
  从新到旧逐版 `setdefault` 到 `month` 键上，即重叠月份保留**最新版**的值
  （新版会修订最近月份，旧版值不覆盖新版；同时也不会用旧值填补新版缺测）。
  每行的 `report_month` / `url` 记录实际供值的版次。最终按 `month` 升序、
  `[:limit]` 截取。实测 `months_back=12` 得 2025-05…2026-09 共 17 个连续月
  （13 版中 March-2026 因源表年份笔误整版跳过，其余 12 版全部 200）。
- **404 / 失败容错**：历史某期 404、网络失败或解析不出数据表 → **跳过该期**，
  stderr 打印一行缺口清单（含期次与失败摘要），不重试、不抛红、不造数；当期
  报告成功即整次运行成功。当期报告自身失败仍按原语义抛 `RuntimeError`。
- **窗口自洽校验（回补版）**：要求解析出的月份升序连续、无缺月、末月 = 版次月，
  否则整版跳过并留痕。实测 `RPI-March-2026.pdf` 表头把 `January 2026` 误印成
  `January 2025`（窗口 Nov-2025…Mar-2026 里出现 2025-01），该版被跳过；2026-03
  由 `RPI-April-2026.pdf` 覆盖，序列仍连续。不猜年份、不改源数据。
- 回补只做「有限次 URL 直取」，不遍历归档、不猜更早年份。

## 归档深度（探测性结论）

对 URL 规律的单发探测（每 URL 一次）：

- `RPI-September-2025` … `RPI-September-2026` 共 13 版 → 全部 200
- `RPI-December-2025.pdf` → 200
- `RPI-January-2020.pdf` → 404
- `RPI-March-2008.pdf` → 404

即该目录至少回溯到 2025-09（本次实测范围），更早未验证；回补能力限定在
`months_back ≤ 18`，不做无界历史抓取。

## 解析规则（spider.py 实现，2026-11-07 对 Sep-2026 版逐格验证 5×11 全对）

1. 取官方页 → 正则发现现行 PDF 链接（`href="…/NRA/media/Research/RPI/….pdf"`）；
   发现失败落固定兜底 URL（`FALLBACK_PDF_URL`，**随版本推进需人工更新**）。
2. PDF 全量 FlateDecode 内容流逐个试解析；认表标准 = 某 ≥2 纯月份名行与相邻 20XX
   年份行配出 ≥2 个月份列，且表体同时出齐 rpi / current_situation_index /
   expectations_index 三条主干序列（页 1/2 图表页不满足）。不写死页码/坐标。
   同一解析器复用于回补的历史版 PDF。
3. 表页（第 3 页「Restaurant Performance Index and Its Components」）按 Tm 坐标聚类成行、
   行内按起点 x 间距 <25pt 合并单元格（PDF 把 `100.7` 拆成 `100.`+`7`、`2026` 拆成
   `20`+`2`+`6`），数值格校验 `-?\d+(\.\d+)?`（环比列带 % 自然排除，另有 30pt 归列阈值兜底）。
4. 同名双序列（Same-Store Sales / Capital Expenditures）按「Current Situation Indicators: /
   Expectations Indicators:」分节标记落位 `cs_*` / `exp_*` 前缀。
5. 宽表：每月一行 × 11 列序列；缺值 = None，不补 0。行序按 month 升序，`[:limit]`。

## 证据（首爬 2026-11-07，真实运行 `python3 spiders/restaurant-rpi/spider.py`）

首爬 **5 行**（Sep-2026 版滚动窗 2026-05…2026-09）。逐字样本（前 2 行，`scraped_at` 略）：

```json
{"month": "2026-05", "rpi": 100.1, "current_situation_index": 99.7, "cs_same_store_sales": 101.7, "cs_customer_traffic": 98.4, "cs_labor": 98.9, "cs_capital_expenditures": 99.8, "expectations_index": 100.4, "exp_same_store_sales": 100.7, "exp_staffing": 101.3, "exp_capital_expenditures": 100.8, "exp_business_conditions": 98.9, "report_month": "2026-09", "source": "restaurant-rpi", "url": "https://restaurant.org/NRA/media/Research/RPI/2024/RPI-September-2026.pdf"}
{"month": "2026-06", "rpi": 100.2, "current_situation_index": 100.1, "cs_same_store_sales": 102.0, "cs_customer_traffic": 99.1, "cs_labor": 99.1, "cs_capital_expenditures": 100.1, "expectations_index": 100.4, "exp_same_store_sales": 101.1, "exp_staffing": 101.2, "exp_capital_expenditures": 101.0, "exp_business_conditions": 98.4, "report_month": "2026-09", "source": "restaurant-rpi", "url": "https://restaurant.org/NRA/media/Research/RPI/2024/RPI-September-2026.pdf"}
```

交叉核对：`rpi`=100.7 / `current_situation_index`=100.7 / `expectations_index`=100.7
（2026-09）与官方页正文「stood at 100.7 in September」一致。

## golden 断言策略

`golden/001-restaurant-rpi.json`（params `limit=12`，`months_back` 缺省 0）：min_rows 3 +
三条结构锚——`source=restaurant-rpi`、`month=2026-05`（Sep-2026 版窗口首月）、
`rpi=100.1`（该月 RPI 定稿值，读自 PDF）。**只锚历史定稿值，不锚最新月**
（2026-09 可能被下版报告修订）。

`golden/002-restaurant-rpi-backfill.json`（params `limit=60, months_back=12`）：
锚一个**只在回补版**里出现的更早月份（2025-09 窗口），验证 URL 回补、跨版合并
与 `report_month` 溯源。回补月份是历史定稿值，通常长期稳定（新报告不再修订），
比 001 的滚动窗锚更耐老化。

⚠️ 滚动窗寿命：缺省 `months_back=0` 时只给最近 5 个月，`2026-05` 将在报告推进
约 5 版后（约 2027-02 版起）滑出窗口，届时 001 replay 失败属**预期老化**而非
数据异常——重录 001 对准当期窗口首月即可（同时核对上版窗口末月值未被修订）。
002 若因回补期滑出 `months_back` 覆盖范围而失败，同理调整参数或锚点。
`scraped_at` 在 whitelist。

## 已知坑

1. 工单旧链 404：RPI 页现行路径在 `restaurant-economic-insights/` 下（404 页导航可发现）。
2. PDF 文本切块：数值/年份/部分标签被拆成多块（x 间距合并解决）；且 Same-Store Sales /
   Capital Expenditures 两节同名（分节标记落位）。
3. 无全史：单版报告只有 5 个月窗；缺省只取当期，需要更长序列时显式传
   `months_back`（≤18）。历史回补期 404/失败只留痕跳过，不重试、不判红。
4. 回补请求是串行的、每期一次；`months_back=12` 约 13 个 PDF（~650 KB/个），
   端到端约 1–2 分钟，属可接受的月度作业成本。
