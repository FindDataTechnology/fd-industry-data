# 港股业绩公告头条数字 — 抽取口径（逐家留档）

> change `add-restaurant-vertical` · 任务 3.2 · 实测 2026-11-08
> 实现：`spiders/hkex-disclosure/hkex_headline.py`（纯函数，无网络）
> 载体：`spiders/hkex-disclosure` 的 `crawl_items.payload.headline_*` 字段
> 数据面：HKEXnews 披露易业绩公告 PDF 的**文本层**（`fd_industry_data.pdftext`，`pdftotext -layout`）

## 1. 定位：这是「最小化头条抽取」，不是 PDF 全文解析

本单元只做一件事：在一份业绩公告里，按**固定标签行**读两个数（营收、归母利润），
连同报告期、币种、单位、原文行一起写进事件 payload。不做版面重建、不做表格结构
推断、不做 OCR、不做分部口径。

变更要求的边界（`行业爬虫线单元 MUST NOT 解析 A股财报文档全文`）在港股按同一
姿态执行：**只碰「关键财务摘要 / 合并收益表」里两个白名单标签行**，正文叙述一律
不读、不引用、不入库。任何需要逐段理解正文才能得到的字段仍属细分面（3.5）——细分面
一期已另立同姿态的白名单抽取（翻台率/同店销售/人均消费/门店数，逐项带报告期+分部+
脚注口径），见 `docs/hkex-segment-kpi-caliber.md`；本文只讲头条两个数，不重复其规则。

## 2. 哪些公告类型qualify

| ann_type | 标题字面 | 是否抽头条 | 说明 |
|---|---|---|---|
| `results` + 期别可判（interim / annual / quarterly） | `中期業績`/`年度業績`/`季度業績`、`interim results`/`annual results`/`quarterly results`，且标题写明 `six months ended <date>` / `year ended <date>` | ✅ 抽 | 每个 (股票, 期别) 只抽**最新一期业绩公告**；同期的正文报告（`INTERIM REPORT` / `ANNUAL REPORT`）标 `not_selected` |
| `results` 但标题不含报告期 | 如 `2026 INTERIM REPORT`（正文报告标题不写期末） | ❌ 不可得 | 原因码 `period_not_stated_in_title`——不按发布年份猜期末 |
| `profit-warning` / `profit-alert` | 盈警/盈喜 | ❌ 不抽 | 该类公告不给完整收入表；头条数字不可得（`ann_type` 不是 `results` → `not_applicable`） |
| `monthly-returns` / `other` | — | ❌ | `not_applicable` |

选择规则（`_select_headline_rows`）：按 (stock_code, 期别) 分组，**优先业绩公告
（标题含 results/業績 且不含 REPORT/報告），取 release_date 最新的一条**；组内
没有公告时才退到正文报告（通常因标题无期末 → 不可得，绝不拿旧一期顶替）。

## 3. 行项目 → 口径

| 概念（本文件口径名） | 接受的原文标签（规范化后逐字相等） | 映射去向（若入库） |
|---|---|---|
| 营收 | `Revenue` / `Revenues` / `Total revenue` / `Total revenues`（另留 `Turnover` / `Total turnover` 未触发） | `financials.revenue`（与美股 `Revenues` 同一概念） |
| 归母利润 | `Profit attributable to owners of the Company` / `Profit for the period attributable to owners of the Company` / `... attributable to owners of the parent` / `... attributable to equity shareholders of the Company` / `... attributable to shareholders` / `Profit attributable to: Equity holders of the Company` / `Attributable to: Equity shareholders of the Company`（亏损期同构 `Loss ...`）；09987 的 `Net Income – Yum China Holdings, Inc.` | `financials.net_income`（与美股 `NetIncomeLoss` 同为**归母**口径） |

**明确不取**：

- `Profit for the period`／`Total loss for the period`：**含少数股东权益**，与
  「归母」口径不同（海底捞 2026 中期：period 1,763,979 vs 归母 1,766,871；
  呷哺呷哺：-36,194 vs -35,990）——取了会把两条不同口径混成一条序列。
- `Profit before tax` / `Core operating profit` / `Adjusted Net Profit` /
  `Store-level operating profit` / `EBITDA`：非 IFRS 或非归母，均不在白名单。
- 叙述句里的「revenue of RMB1,493.0 million」：只认**标签行**（行首标签 + 同行
  数字列），不解析正文句子。

## 4. 报告期语义

期别只从**事件标题**解析，且期末日期必须在 PDF 正文真实出现（`period_end_not_in_document`
兜底）。期长与语义：

| 标题写法 | 期长 | 语义 |
|---|---|---|
| `six months ended <date>` / `中期業績` | 6 个月 | 中报（半年累计，**不是季度**） |
| `year ended <date>` / `年度業績` | 12 个月 | 年报（全年累计） |
| `three months ended <date>` / `季度業績` | 3 个月 | 季度 |
| `nine months ended <date>` | 9 个月 | 三季度累计（**不得当季度**，不得与 3 个月序列混写） |

写入观测时的日期约定沿用美股/A股既有车道：`date` = **报告期末**（如 2026-06-30），
`granularity` 需与期长一致——`half` 目前不在观测表粒度词表（现有只有
`quarter/day/year/month/hour/week`），这正是本期**不入观测**的原因之一（见 §8）。

## 5. 币种与单位

- 币种/量级只从**数字表头上方最近一次**声明读取：`RMB'000` / `HK$'000` /
  `US$'000`（含 `(RMB ’000)`、被 `-layout` 拆成三行的 `(US$ / ’ / 000)`）、
  `(in US$ millions, except per share data)`。
- 量级只认 `000` / `thousand(s)` / `million(s)`；金额换算为**原始币种单位**的整数
  （千元 ×1000，百万 ×1,000,000），**不做汇率换算**。
- 币种映射：`RMB → CNY`、`HK$/HKD → HKD`、`US$/USD → USD`。
- 找不到单位声明 → `unit_not_found`（不可得），不按「大概率是千元」补。

> **口径挂账（重要）**：`financials.revenue` / `financials.net_income` 在库内没有
> 币种维度，同一概念下 A股写 CNY、美股写 USD（既有事实）。本单元若入库会再混入
> HKD/USD。本期不入库；将来入库前必须先裁「币种是否进 widening/字段」。
> 当前 payload 的 `headline_currency` 字段保留了逐条币种，读路径可按币种分组。

## 6. 歧义 → 不可得（硬规则）

按顺序检查，任一条命中即 `headline_status=unavailable` + 原因码，**绝不挑一个
看起来对的数**：

| 原因码 | 触发条件 |
|---|---|
| `no_text_layer` | PDF 抽不出文本（扫描件） |
| `not_a_pdf` | 载荷魔数不是 `%PDF` |
| `pdf_too_large` | > 30 MB（只可能是正文报告，公告不会这么大） |
| `pdf_unavailable` | 下载/`pdftotext` 失败 |
| `period_not_stated_in_title` | 标题无 `six months/year ended <date>` |
| `period_end_not_in_document` | 标题期末在 PDF 正文找不到 |
| `no_revenue_line` | 白名单营收标签一次都没命中 |
| `no_profit_attributable_line` | 白名单归母利润标签一次都没命中 |
| `unit_not_found` | 命中行上方同页无币种/量级声明 |
| `column_header_not_found` | 命中行上方 8 行内无 `2026 2025` / `6/30/2026 6/30/2025` 双期表头（防列序错位） |
| `revenue_and_profit_not_in_same_table` | 两个数不在同一页、同一币种/量级的同一张表里 |
| `conflicting_units` | 多张合格表的币种/量级不一致 |
| `ambiguous_revenue` / `ambiguous_profit` | 同一概念在同一张表出现**两个不同值** |
| `not_selected` | 不是该 (股票, 期别) 选中的那条公告（payload 里写明被选中的日期） |

另外两条结构性保护：折行标签最多回接 2 行；`Attributable to:` 类下半句必须验证
上一行就是 `Attributable to:`（否则会误吃「Total comprehensive income …」或
「Total equity attributable to …」的值）。

## 7. 逐家覆盖（2026-11-08 实测，最新一期）

| 代码 | 公司 | 事件（披露日） | 期别 | 币种/单位 | 营收（原币） | 归母利润（原币） | 证据行（PDF 原文） |
|---|---|---|---|---|---|---|---|
| 06862 | 海底捞 | 2026-08-25 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 22,336,906,000 | 1,766,871,000 | `Revenue 22,336,906`；`Profit attributable to owners of the Company 1,766,871` |
| 09922 | 九毛九 | 2026-08-28 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 2,391,313,000 | 75,804,000 | `Revenue . . . 2,391,313`；`Profit for the period attributable to equity shareholders of the Company 75,804` |
| 00520 | 呷哺呷哺 | 2026-08-27 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 1,493,047,000 | **-35,990,000** | `Revenue 1,493,047`；`Total loss for the period attributable to owners of the Company (35,990)` |
| 01405 | 达势股份 | 2026-08-26 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 3,133,808,000 | 81,045,000 | `Revenue 3,133,808`；`Profit for the period attributable to owners of the Company 81,045` |
| 09658 | 特海国际 | 2026-08-26 中期业绩 | 6M → 2026-06-30 | USD 千元 | 444,754,000 | 2,191,000 | `Revenue 444,754`；`Profit for the period attributable to owners of the Company 2,191` |
| 09987 | 百胜中国 | 2026-08-10 中期业绩 | 6M → 2026-06-30 | USD 百万 | 6,409,000,000 | 553,000,000 | `Total revenues 6,409`；`Net Income – Yum China Holdings, Inc. $ 553` |
| 00341 | 大家乐 | 2026-06-15 年度业绩 | 12M → 2026-03-31 | HKD 千元 | 8,183,143,000 | 164,053,000 | `Revenue 6 8,183,143`；`Profit attributable to: Equity holders of the Company 164,053` |
| 00052 | 大快活 | 2026-06-30 年度业绩 | 12M → 2026-03-31 | HKD 千元 | 3,098,831,000 | 41,560,000 | `Revenue 3 3,098,831`；`Profit for the year attributable to equity shareholders of the Company 41,560` |
| 02150 | 奈雪的茶 | 2026-08-26 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 1,892,498,000 | **-97,493,000** | `Revenue 3 1,892,498`；`Attributable to: Equity shareholders of the Company (97,493)` |
| 01458 | 周黑鸭 | 2026-08-20 中期业绩 | 6M → 2026-06-30 | CNY 千元 | 1,461,327,000 | 91,496,000 | `Revenue 1,461,327`；`Profit for the period attributable to owners of the parent 91,496` |

**覆盖：10/10 有可辩护的头条数字**（8 家中报 6M、2 家年报 12M）。负数为亏损期
原文括号写法（`(35,990)`、`(97,493)`），按原文保留符号。

逐家的 PDF 入口（可点开人工核）：

| 代码 | doc_url |
|---|---|
| 06862 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501119.pdf |
| 09922 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0828/2026082802450.pdf |
| 00520 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0827/2026082701671.pdf |
| 01405 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0826/2026082600598.pdf |
| 09658 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0826/2026082601123.pdf |
| 09987 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0810/2026081000819.pdf |
| 00341 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0615/2026061500255.pdf |
| 00052 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0630/2026063000667.pdf |
| 02150 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0826/2026082601351.pdf |
| 01458 | https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0820/2026082001355.pdf |

## 8. 为什么没有写进 semantic_observations（显式，不假装）

两个硬约束，均先于本任务存在：

1. **粒度词表没有半年档**。现役 `granularity` 只有 `quarter/day/year/month/hour/week`
   （2026-11-08 库内实测）。8 家中报是 6 个月累计，写成 `quarter`/`year` 都是口径
   造假；任务 3.5 已把「新增 `half`」列为待裁项，本单不越权预判。
2. **趋势门 ≥24 期（12–23 期需 short_window 声明）**。单次 90 天窗口内每家公司
   只有 1 期业绩（±1 期正文报告）；即便回补 6 年也只有 12 期半年报，且仍受第 1 条
   阻断。按 `_common.gate_periods` 纪律，`<12` 期一律**不入库并留 SKIP 说明**。

因此本期交付形态 = **事件 payload 携带结构化头条数字**（`headline_*`），查询路径：

```sql
SELECT payload->>'stock_code'                AS code,
       payload->>'release_date'              AS released,
       payload->>'title'                     AS title,
       payload->>'headline_status'           AS status,
       payload->>'headline_period_end'       AS period_end,
       payload->>'headline_period_months'    AS months,
       payload->>'headline_currency'         AS ccy,
       payload->>'headline_revenue'          AS revenue,
       payload->>'headline_net_income'       AS net_income
FROM crawl_items ci JOIN crawl_runs r ON r.id = ci.run_id
WHERE r.source = 'hkex-disclosure' AND r.status = 'success'
  AND payload->>'ann_type' = 'results'
ORDER BY payload->>'stock_code', payload->>'release_date' DESC;
```

事件与数字在**同一条记录**里，满足 spec「财报披露事件与营收/利润头条可被查询」；
但**不在**长序列观测表里。要进观测表，需先裁 §8 的两条（建议：新增 `half` 粒度 +
对「事件级头条」豁免趋势门，或接受 6 年后才有趋势意义）。

## 9. 挂账（不粉饰）

1. **`half` 粒度**：需平台裁（3.5 同一挂账）；裁前港股半年报头条不进观测。
2. **币种维度**：`financials.revenue` / `net_income` 无币种字段，多市场混写；
   当前 payload 逐条带 `headline_currency`。入观测前须裁。
3. **正文报告兜底与期别**：正文报告标题不写期末 → 不可得。若将来要做「正文报告
   也带期别」，需从 PDF 首页叙述取期（本单刻意不做，避免猜）。
4. **US-GAAP 表头（09987）**：`Year to Date Ended 6/30/2026` 能对应 6 个月，依据是
   标题写明 `Six Months Ended June 30, 2026` **且**表头两列恰为相隔一年的 YTD；
   这是本文件唯一允许的「表头 + 标题互证」特例，已在 `PROFIT_LABELS` 的
   `Net Income – Yum China Holdings, Inc.` 条目旁注明。
5. **分部/同店/翻台率**：已由 3.5 一期在**同一个单元**内另立白名单抽取
   （`hkex_segment_kpi.py`，共用同一份 PDF 文本层），口径与不可得台账见
   `docs/hkex-segment-kpi-caliber.md`；本文的头条规则不受其影响。
