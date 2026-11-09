# hkex-disclosure — HKEXnews 披露易港股公告标题搜索

> 本车道属性：金融车道扩展。**2026-10-07 侦察簿边界注记**：裁决理由 = 防重叠；港股（HKEX 披露易）在平台此前**无既有财报车道**，本扩展不构成与既有财报线的重叠。本单元取公告**标题与元数据**（事件面），并对 results 类公告做**最小化头条抽取**（只读固定标签行，不解析财报正文全文）；另对 06862 海底捞 / 09922 九毛九做**白名单细分 KPI 抽取**（翻台率/同店销售/人均消费/门店数，每项带报告期+分部+单位+原文标签+脚注口径）。口径全文：头条 `docs/hkex-results-headline-caliber.md`；细分 KPI `docs/hkex-segment-kpi-caliber.md`。

- 源：HKEXnews 披露易标题搜索 `https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh`
- 通道：两个免鉴权 HTTPS JSON 接口（`prefix.do` + `titleSearchServlet.do`）+ 业绩公告 PDF 的**文本层**（`fd_industry_data.pdftext`，`pdftotext -layout`，只取表头与两个标签行）；**不抓 HTML 列表页、不做 PDF 全文/版面解析、不做 OCR**
- 监控宇宙：港股餐饮 10 家（见下表）
- 状态：**未写 schedule**（生成单 `reports/health-tickets/20261107-hkex-disclosure-82e7ef63.yaml`；
  按生成流先静默合入、试跑通过后再由维护者点亮）
- 入口：`run_hkex_disclosure(limit=500, stocks=DEFAULT_STOCKS, days_back=90, max_pages=5)`

## 1. 数据通路（2026-11-07 实测，UTC+8）

### ① `prefix.do` — 股票代码 → stockId

```
GET https://www1.hkexnews.hk/search/prefix.do
    ?callback=callback&lang=ZH&type=A&name=06862&market=SEHK
→ callback({"more":"1","stockInfo":[
     {"stockId":199151,"code":"06862","name":"海底撈"},
     {"stockId":1000309158,"code":"68628","name":"紫金摩通七一牛F"}, ...]});
```

- `stockId` 是 HKEX **内部数字 id**，不是股票代码；`titleSearchServlet.do` 只认它。
- 定谳方式：**只接受 `code` 精确相等**（`str(info["code"]).strip() == code`），绝不按数组位置取；
  找不到精确匹配 → `SpiderError`（宇宙契约不静默缩水）。
- 缓存于**进程内 dict** `_STOCK_ID_CACHE`（code → (stockId, 上游名称)），一次进程内复用。
- 代码先 `zfill(5)` 归一到 5 位（`"6862" → "06862"`）。

实测 stockId（2026-11-07，`lang=ZH`）：

| 代码 | 上游名称 | stockId | 180 天公告数 |
|---|---|---|---|
| 06862 | 海底撈 | 199151 | 17 |
| 09922 | 九毛九 | 1000018449 | 55 |
| 00520 | 呷哺呷哺 | 115713 | 15 |
| 01405 | 達勢股份 | 1000176932 | 22 |
| 09658 | 特海國際 | 1000179492 | 21 |
| 09987 | 百勝中國 | 1000055939 | 161 |
| 00341 | 大家樂集團 | 627 | 55 |
| 00052 | 大快活集團 | 81 | 22 |
| 02150 | 奈雪的茶 | 1000100020 | 75 |
| 01458 | 周黑鴨 | 147421 | 81 |

> **宇宙校正（诚实记录）**：任务单给的「00965 特海国际」代码有误。实测 `prefix.do` 对
> `00965` / `0965` / 名称「特海」都只返回 **09658 特海國際**（Super Hi International，
> stockId 1000179492）。本单元按上游事实使用 **09658**；若照抄 00965 会直接取不到数据。

### ② `titleSearchServlet.do` — 公告列表

```
GET https://www1.hkexnews.hk/search/titleSearchServlet.do
    ?sortDir=0&sortByOptions=DateTime&category=0&market=SEHK
    &stockId=199151&documentType=-1&fromDate=20260511&toDate=20261107
    &title=&searchType=1&t1code=-1&t2Gcode=-2&t2code=-2
    &rowRange=100&lang=EN
```

响应（纯 JSON；也兼容 `callback({...})` 包裹）：

```json
{"result":"[{\"NEWS_ID\":\"12340980\",\"STOCK_CODE\":\"06862\",\"STOCK_NAME\":\"HAIDILAO\",
  \"TITLE\":\"2026 INTERIM REPORT\",\"DATE_TIME\":\"22/09/2026 16:31\",
  \"LONG_TEXT\":\"Financial Statements&...\",\"FILE_LINK\":\"/listedco/listconews/sehk/2026/0922/2026092200356.pdf\",
  \"FILE_TYPE\":\"PDF\", ...}]",
 "hasNextRow":false,"recordCnt":1,"loadedRecord":1,"rowRange":100,"lang":"E"}
```

- `result` 是**字符串化 JSON**，必须二次 `json.loads`。
- **分页没有 page offset**：`rowRange` 是「最多返回多少行」的杠杆。本单元第 N 页请求
  `rowRange = 100*N`，按 `NEWS_ID` 归并去重；`hasNextRow` 在 `max_pages` 用尽后仍为 true
  → **判红**（不返回截断/部分数据）。
- `recordCnt` 与归并行数不一致 → **判红**（short read）。
- 空结果（`recordCnt=0` 且无行）→ **判红**（空 feed 视为故障，不返回空壳成功）。

### ③ EN / ZH 双语标题

`lang=EN` 与 `lang=ZH` 是**两条不同的文档行**（NEWS_ID 不同；中文 PDF 文件名带 `_c` 后缀），
不能按 NEWS_ID 连接。实测 10 家公司在 2025-05-01..2026-11-07 区间内，EN 与 ZH 的
**行数、顺序、DATE_TIME 序列完全一致**（逐家比对），因此：

- 默认：**顺序位置配对**（同一时刻的行按顺序一一对应），`title` = EN，`title_zh` = ZH；
- 兜底：一旦计数/顺序不一致，改用**按 DATE_TIME 的 FIFO 队列**配对，未配上的行
  `title_zh = null`（宁缺不错配），并记 warning；
- ZH 整体为空但 EN 非空 → **判红**（否则 `title_zh` 会静默全空）。

### ④ `t1code`：与任务单 URL 的有据偏离

任务单给的是 `t1code=40000`。实测该值把 feed **限死在「Financial Statements/ESG Information」**：

| t1code | 06862 2026-01-01..11-07 `recordCnt` | 内容 |
|---|---|---|
| `40000` | 3 | 只有 2026 中报、2025 年报、ESG 报告 |
| `-1` / `-2` / `40100` | 36 | 全部类型：一般公告、月报表、关联交易、中报、年报… |

`40000` 会静默丢掉**盈警/盈喜、月報表、一般公告**——与本单元要求的 `ann_type` 事件面
直接冲突。故本单元请求 **全部类型**（`T1CODE_ALL = "-1"`），并在 spider.py 模块文档与
本表留证。

## 2. `ann_type` 显式规则表

规则写死在 `spider.py` 的 `ANN_TYPE_RULES`（元组顺序 = 优先级），对 **EN 标题 + ZH 标题**
合并文本做字面匹配，**不猜、不推断语义**。命中多个 → 取优先级最高者，全部命中写入
`ann_type_hits`（形如 `["results:interim results"]`）。

| 优先级 | ann_type | 字面关键词（任一命中） |
|---|---|---|
| 1 | `profit-warning` | `盈警` / `盈利警告` / `盈利預警` / `盈利预警` / `profit warning` |
| 2 | `profit-alert` | `盈喜` / `正面盈利预告` / `正面盈利預告` / `positive profit alert` |
| 3 | `results` | `中期業績`/`中期业绩`/`年度業績`/`年度业绩`/`季度業績`/`季度业绩`、`interim results`/`annual results`/`quarterly results`、`年報`/`年报`/`年度報告`/`年度报告`、`中期報告`/`中期报告`、`interim report`/`annual report` |
| 4 | `monthly-returns` | `月報表` / `月报表` / `monthly return` |
| 5 | `other` | 以上都不命中（`ann_type_hits` 为空列表） |

设计说明（均为实测，不是猜）：

- 披露易中文是**繁体**：「盈警」实际写作 `盈利預警`、「盈喜」实际写作 `正面盈利預告`，
  故必须包含繁体形，否则会漏掉真实的盈警/盈喜。
- **裸 `盈利預告` / `PROFIT ALERT` 故意不映射**：该体裁词本身不含方向（盈警还是盈喜），
  方向只出现在后半句（如「預期虧損大幅減少」）。按任务要求「规则不许靠猜」，这类行
  归入 `other` 并在 `ann_type_hits` 留空；官方分类 `LONG_TEXT`（如 `[Profit Warning /
  Inside Information]`）**未被消费**，不据此反推方向。180 天窗口内仅 1 行落入此边界
  （00520 呷哺呷哺 2026-08-03 `Profit Alert - Expected Significant Reduction in Loss`）。
- `is_profit_warning = (ann_type == "profit-warning")`，`profit-alert` 不置位。

## 3. 列与字段

| 列 | 类型 | 说明 |
|---|---|---|
| `stock_code` | string | 5 位港股代码（如 06862） |
| `stock_name` | string | 上游 `stockInfo.name`（繁体，如 海底撈）；缺失回退调用方标签 |
| `stock_id` | int | 上游内部 stockId（如 199151）——检索接口唯一认的 id |
| `release_date` | string | `YYYY-MM-DD`，由 `DATE_TIME "DD/MM/YYYY HH:MM"`（**香港时间**）解析；解析失败判红 |
| `title` | string | 英文标题（`lang=EN` `TITLE`），HTML 实体反转义 + 空白折叠 |
| `title_zh` | string | 繁体中文标题（`lang=ZH` `TITLE`）；无法对齐时为 null（不误配） |
| `ann_type` | string | 见 §2 规则表 |
| `ann_type_hits` | array\<string\> | 全部命中的规则，`"<ann_type>:<字面量>"`，按优先级；`other` 时为空列表 |
| `is_profit_warning` | bool | 仅 `profit-warning` 为 true |
| `doc_url` | string | `https://www1.hkexnews.hk` + `FILE_LINK` 的完整公告链接（PDF，偶有 HTML）；results 行的头条数字也从这条链接的 PDF 抽 |
| `headline_status` | string | `ok`（本行 PDF 抽出营收 + 归母利润）/ `unavailable`（显式不可得 + 原因码）/ `not_applicable`（非 results 行）。每个 (股票, 期别) 只抽最新一条**业绩公告** |
| `headline_reason` | string | `headline_status != ok` 时的原因码（`no_text_layer` / `unit_not_found` / `ambiguous_profit` / `not_selected` …），全表见 `docs/hkex-results-headline-caliber.md` §6 |
| `headline_period_end` | string | 报告期末 `YYYY-MM-DD`，来自事件标题并回验 PDF 正文出现该日期 |
| `headline_period_months` | int | 期长月数：6（中报）/ 12（年报）/ 3、9（季度/三季度累计）。**半年 ≠ 季度** |
| `headline_period_label` | string | 标题里被解析的原文期别短语（如 `for the six months ended June 30, 2026`） |
| `headline_currency` | string | 表头声明的报告币种：`CNY` / `HKD` / `USD` |
| `headline_unit` | string | 表头声明的量级：`thousand` / `million`；下面两个金额已换算成**原始币种单位** |
| `headline_revenue` | string | 营收（原始币种单位的整数字符串，如 RMB'000 22,336,906 → `22336906000`） |
| `headline_net_income` | string | **归母**利润（本公司拥有人应占；不含少数股东权益），与美股 `NetIncomeLoss` 同口径 |
| `headline_evidence` | array\<string\> | 数字所依据的 PDF 原文行 + `[unit] …` 单位表头行；不可得时给出触发原因的行，便于人工复核 |
| `kpi_status` | string | 细分 KPI 面（3.5 一期）状态：`ok`（全部登记 KPI 带口径落地）/ `partial`（部分落地 + 部分显式不可得）/ `unavailable`（一项没落地，原因见 `kpi_reason`）/ `not_applicable`（非 results 行 → `ann_type_not_results`；或该公司无一期 KPI 目标 → `no_phase1_kpi_target`；`kpi_reason` 不为 null）。逐指标字段命名 `kpi_<slug>`、`_unit`、`_caliber`、`_status`、`_reason`、`_prior`、`_evidence`，只落在 06862 / 09922 被选中的业绩公告行 |
| `kpi_reason` | string | 行级原因码：`no_phase1_kpi_target`（公司不在范围）/ `ann_type_not_results`（目标公司但这份公告不是业绩公告）/ `not_selected` / `no_doc_url` / `no_text_layer` / `period_not_stated_in_title` / `period_end_not_in_document` / `pdf_too_large` / `not_a_pdf` / `pdf_unavailable` / `cancelled` / `internal_error` / `unavailable: <slugs>` / `explicit_unavailable: <slugs>` |
| `kpi_targets` | array\<string\> | 该公司一期登记的 KPI slug（06862 11 个、09922 13 个）；其余 8 家为空列表 + `not_applicable` + `no_phase1_kpi_target`（显式，不是静默省略） |
| `source` | string | 常量 `hkex-disclosure` |
| `scraped_at` | string | 本次运行的抽取起点（UTC ISO） |

行序：全宇宙按 `release_date` 倒序后截断到 `limit`。

## 4. 事件面与「头条数字」的真实边界

**本单元能回答的（3.2 验收点）**：

- 「海底捞 / 九毛九最新中报**事件**」——能。实测 180 天窗口内：
  - 06862 海底撈：`2026-08-25` `ANNOUNCEMENT OF INTERIM RESULTS FOR THE SIX MONTHS ENDED
    JUNE 30, 2026 AND INTERIM DIVIDEND...`（`ann_type=results`），
    `doc_url=https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501119.pdf`；
    中报正文 `2026-09-22` `2026 INTERIM REPORT`。
  - 09922 九毛九：`2026-08-28` `ANNOUNCEMENT OF INTERIM RESULTS FOR THE SIX MONTHS ENDED
    JUNE 30, 2026`；中报正文 `2026-09-29` `INTERIM REPORT 2026`。
- **最新一期业绩公告的营收 / 归母利润**——能，且与原文一致（2026-11-08 实测，
  逐家见 `docs/hkex-results-headline-caliber.md` §7）：
  - 06862 海底捞 2026 中期：营收 `22,336,906` 千元人民币（= 22,336,906,000）、
    归母利润 `1,766,871` 千元（= 1,766,871,000）；原文行
    `Revenue 22,336,906 20,703,294` / `Profit attributable to owners of the Company 1,766,871 1,758,525`。
  - 09922 九毛九 2026 中期：营收 `2,391,313` 千元（= 2,391,313,000）、归母利润
    `75,804` 千元（= 75,804,000）；原文行 `Revenue . . . 2,391,313 2,753,436` /
    `Profit for the period attributable to equity shareholders of the Company . . . 75,804 60,691`。
- 标题 + 发布日期 + `ann_type` + PDF 链接 + **头条数字/期别/币种/单位/原文行**，
  可用于事件触发、时间线、财报日历、公司板头条 KPI。

**本单元的抽取姿态（不是全文解析）**：

- 只读公告 PDF 文本层里的**两个固定标签行**（`Revenue` 系列、`Profit attributable
  to owners/equity shareholders` 系列）与它们的表头（期别、币种、量级）。
  正文叙述、分部表、EBITDA/调整项一律不读。
- **`Profit for the period`（含少数股东权益）不取**，只取归母口径；两者在海底捞
  2026 中期就差 2,892 千元（1,763,979 vs 1,766,871），混用即口径造假。
- 期别只认标题明写的 `six months/year ended <date>`，并回验 PDF 正文出现该期末；
  标题不写期末（如 `2026 INTERIM REPORT`）→ 不可得，不按发布年份猜。
- **任何歧义 → 不可得**：单位缺失、同一概念两个值、营收与利润不在同一张表、双期
  表头对不上、扫描件无文本层……都落 `headline_status=unavailable` + 原因码，
  绝不挑「看起来对」的数。

**本单元仍不能回答的（诚实的边界）**：

- **头条数字与细分 KPI 都不写 `semantic_observations`**。原因两条：① 8 家中报是 6 个月累计，
  观测表粒度词表没有 `half`（写成 `quarter`/`year` 即口径造假）；② 单窗口内每家
  只有 1 期业绩，远低于 `_common` 的 ≥24 期趋势门（12–23 期需 short_window 声明，
  <12 期按纪律不入库）。可查询路径 = `crawl_items.payload.headline_*` / `kpi_*`
  （事件与数字同一条记录），SQL 见两份口径文档。
- **未选择的同期报告**（`2026 INTERIM REPORT` 这类正文）标 `not_selected`，不重复
  解析；正文报告标题不写期末，本身也不满足期别可判。
- **盈警/盈喜**不给完整收入表，头条数字不可得（`ann_type` 非 results →
  `not_applicable`），只保留事件与类型。
- **细分 KPI 只覆盖 06862 / 09922 两家（一期）**。其余 8 家 payload 显式
  `not_applicable` + `no_phase1_kpi_target`，二期按同一引擎逐家登记脚注后再扩；
  详见 §4.1 与 `docs/hkex-segment-kpi-caliber.md`。

## 4.1 细分 KPI（task 3.5 一期，实测 run 4829）

**能回答的**：海底捞 11 项、九毛九 13 项，逐项带「报告期 + 分部/范围 + 单位 +
逐字标签行 + 公告脚注原文」——翻台率（整体/大陆/港澳台/同店）、人均消费、同店销售额、
同店家数、自营/特许餐厅数；九毛九的翻台率/人均消费/同店销售额/同店日均销售同比按
太二、怂火锅、九毛九三个品牌分部各自登记。

| 公司 | `kpi_status` | 落地 | 显式不可得 |
|---|---|---|---|
| 06862 海底捞 | `partial` | 11 项（`table_turnover_*` 4、`spending_per_guest_*` 3、`same_store_sales_overall`、`same_store_count_overall`、`restaurant_count_self_operated`/`_franchised`） | 3 项：`same_store_sales_growth_overall`（`no_labelled_line`）、`new_opened_restaurants` / `closed_restaurants`（`narrative_only`） |
| 09922 九毛九 | `ok` | 13 项（`restaurant_count_group`、`table_turnover_*` 3、`average_spending_per_customer_*` 3、`same_store_sales_*` 3、`same_store_sales_change_*` 3） | 0 |

**抽不出来的姿态（不猜）**：分区标题 + 脚注号 + 行标签三者逐字相等才取；数值按
`-layout` 字符列位落列（四列表只取前两组）；金额的千元量级归一为原币整数；
百分比按百分数存；同店增长率没有独立标签行 → 不拿两年销售额相除；新开/关停只出现
在叙述段 → 不取。逐家逐项口径表、PDF 原文片段、不可得原因码见
`docs/hkex-segment-kpi-caliber.md`。

## 5. 试跑（2026-11-08，含头条抽取与细分 KPI）

```
source ~/.config/finddata/dashboards.env
FD_CRAWL_DB_URL="$FD_OPEN_DATA_DSN" FD_IMAGE_TAG=local-hk-kpi FD_WRITER_MAX_ITEMS=100000 \
  ./.venv/bin/python -m fd_industry_data.runner_cli hkex-disclosure --limit 500
```

| 项 | 值 |
|---|---|
| `crawl_runs.id` | **4829**（含细分 KPI；头条专项 4815 = 180 天窗口 522 行） |
| status | **success**（`error_head` 空） |
| rows_written | **226**（默认 90 天窗口） |
| 耗时 | 94.5 s |
| 头条 `headline_status=ok` | 8/8 窗口内 results 股票（中报 6M；00341/00052 的年度业绩落在 90 天窗口外） |
| 细分 KPI | 06862 `partial`（11 落地 + 3 显式不可得）/ 09922 `ok`（13/13）；非目标公司 `not_applicable` + `no_phase1_kpi_target`；目标公司非业绩公告行 `not_applicable` + `ann_type_not_results` |

`ann_type` 分布（run 4803，524 行；header 抽取上线前的基线）：

| ann_type | 行数 | 占比 |
|---|---|---|
| `other` | 441 | 84.2% |
| `monthly-returns` | 61 | 11.6% |
| `results` | 20 | 3.8% |
| `profit-alert` | 2 | 0.4% |
| `profit-warning` | **0** | 0% |

- `profit-alert` 2 行：00341 大家樂 2026-11-04、00052 大快活 2026-10-20（均 `Positive Profit Alert` / `正面盈利預告`）。
- `profit-warning` 0 行是**窗口事实**，不是漏抓：本期 10 家的 `盈利預警` 类公告都落在
  2026-05-11 之前（如 00341 大家樂 2025-11-06、00520 呷哺呷哺 2026-03-06）。规则自测对
  `盈利預警`/`Profit Warning` 均判 `profit-warning`。

**头条数字落库实况**（run 4815，SQL 见 `docs/hkex-results-headline-caliber.md` §8）：

| 代码 | 事件 | 期别 | 币种/单位 | 营收（原币） | 归母利润（原币） |
|---|---|---|---|---|---|
| 06862 | 2026-08-25 中报 | 6M | CNY 千元 | 22,336,906,000 | 1,766,871,000 |
| 09922 | 2026-08-28 中报 | 6M | CNY 千元 | 2,391,313,000 | 75,804,000 |
| 00520 | 2026-08-27 中报 | 6M | CNY 千元 | 1,493,047,000 | -35,990,000 |
| 01405 | 2026-08-26 中报 | 6M | CNY 千元 | 3,133,808,000 | 81,045,000 |
| 09658 | 2026-08-26 中报 | 6M | USD 千元 | 444,754,000 | 2,191,000 |
| 09987 | 2026-08-10 中报 | 6M | USD 百万 | 6,409,000,000 | 553,000,000 |
| 00341 | 2026-06-15 年报 | 12M | HKD 千元 | 8,183,143,000 | 164,053,000 |
| 00052 | 2026-06-30 年报 | 12M | HKD 千元 | 3,098,831,000 | 41,560,000 |
| 02150 | 2026-08-26 中报 | 6M | CNY 千元 | 1,892,498,000 | -97,493,000 |
| 01458 | 2026-08-20 中报 | 6M | CNY 千元 | 1,461,327,000 | 91,496,000 |

- **确定性**：run 4814（90 天）与 run 4815（180 天）两次独立运行，8 家重叠公司的
  `headline_revenue` / `headline_net_income` **逐条完全一致**（`ok`→`ok` 全等，2026-11-08 实测）。
- `semantic_observations` 中 `source_used='hkex-disclosure'` 的行数为 **0**（设计如此，见 §4）。

`ann_type` 分布（run 4815，522 行，与 4803 同口径）：`other` 439 / `monthly-returns` 61 /
`results` 20 / `profit-alert` 2 / `profit-warning` 0。

## 6. 网络与容错口径

- HTTPS 直连 `www1.hkexnews.hk`，无鉴权、无 cookie、无验证码；`verify=False`（仓内惯例：
  容器不带 CA 链），`retries=1`（单次尝试，不做重试风暴、不绕反爬）。
- 浏览器 UA + `Referer: https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh`。
- 请求间 0.4–0.9 s 抖动限速。
- 判红清单（`SpiderError`，`RuntimeError` 子类；仓内无共享 `SpiderError` 基类）：
  stockId 精确匹配失败 / 请求非 200 / 响应不可解析 / `result` 非 JSON 数组 /
  `recordCnt` 与实际行数不符 / `hasNextRow` 在 `max_pages` 后未收敛 / 空结果 /
  `DATE_TIME` 不可解析 / EN 有行而 ZH 全空。**任何一条都不返回部分假数据。**

## 7. golden

`golden/001-hkex-disclosure.json` — 锚真实值（海底捞已披露公告）：

- 参数：`{"limit": 200, "days_back": 365, "stocks": [["06862","海底捞"]]}`
- 断言：`source == "hkex-disclosure"`、`ann_type == "results"`、`release_date == "2026-08-25"`
- `whitelist_fields: ["scraped_at"]`
- 重放（`fd_industry_data.health.guard.replay_sample`，2026-11-08，头条抽取上线后再次实测）：
  `ok=True, rows=37, diffs=[]`

> 说明：golden 只取海底捞一只股票，是为了让重放成本稳定（~6 次列表请求 + 2 份业绩
> 公告 PDF：中报 1 份、年报 1 份；都在一年窗口内）且锚点必在结果集内；它验证的是
> 抽取契约，不是宇宙覆盖。`days_back=365` 让 2026-08-25 锚点在一年内可重放。

## 8. 已知遗留

1. **`profit-warning` 康乐面偏薄**：裸 `PROFIT ALERT` / `盈利預告` 方向不明，按「不猜」规则
   归 `other`。若后续需要 100% 覆盖盈警/盈喜，应在 3.5 立项时消费官方 `LONG_TEXT`
   分类或解析 PDF 首段，而不是放宽关键词。
2. **头条数字与细分 KPI 都不入观测**：见 §4/§4.1。两个前置裁决在
   `docs/hkex-results-headline-caliber.md` §8/§9 与
   `docs/hkex-segment-kpi-caliber.md` §8/§10：半年粒度 `half` 是否进词表、
   事件级指标是否豁免 ≥24 期趋势门；以及 `financials.*` 缺币种维度的问题。
3. **细分 KPI 只做两家（一期）**：06862 / 09922；其余 8 家显式
   `no_phase1_kpi_target`。二期逐家实测标签结构后再扩，不假设同构。
4. **分页是 rowRange 递增重取**（无 offset）：每次翻页重复下载前缀，`max_pages=5` 上限
   500 行/股/语言。单股 180 天内未触顶（最大 161 行 → 2 页）。
5. **每轮最多多下 8–10 份 PDF**（每个 (股票, 期别) 一份业绩公告，~0.15–3 MB）：
   先按 `limit` 截断再下载，只为最终返回的行付流量；30 MB 以上一律跳过（`pdf_too_large`）。
   **同一份 PDF 只下/抽一次，头条与细分 KPI 共用文本层**。
6. **未点亮**：`schedule` 按生成流留空；点亮前需按 §5 重跑一次心跳并确认 rows。
