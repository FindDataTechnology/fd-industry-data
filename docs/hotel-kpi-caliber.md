# 酒店 KPI 口径登记表（add-hotel-vertical 任务 4.4）

**这份表回答一个问题：`hotel.*` 这条数，量的是谁、按什么口径、能不能横向比。**

酒店业的 RevPAR 是个陷阱词：同一串数字可以是「全系统 / 可比 / 公司自营 / 自有租赁」，
可以是「固定汇率 / 本币 / 不变币值」，可以是「当季 / 年初至今 / 单月」，
可以是「含筹建 / 不含筹建」。这些差异**不是精度问题，是量级问题**——
混着比就是错的。所以每个 KPI 必须带一行口径；**没有口径的字段不注册**。

读路径纪律：本表是口径的**唯一真源**，代码里的 `CALIBER` / `AVAILABILITY` 常量、
板面标注、MCP 工具的 `caliber` 字段都必须与这里逐字一致（测试钉住）。

## 字段定义

| 字段 | 含义 | 取值 |
|---|---|---|
| `market` | 上市地 / 监管披露体系 | CN-A / HK / US / TW / JP / EU |
| `metric` | 指标 | RevPAR / ADR / 出租率（occupancy）/ 租金 / 房间数 |
| `口径语义` | 统计边界：谁的房、哪些房 | 全系统 system-wide / 可比 comparable / 公司自营 company-operated / 自有及租赁 owned & leased / 分部 |
| `汇率` | 币值与是否固定汇率 | 本币 as-reported / constant-$ / currency-neutral / 不变币值 |
| `含筹建` | pipeline（筹建 / 签约未开业）是否计入 | 计入 / 不计入 / 披露未说明 |
| `频率` | 数据粒度 | 月 / 季 / 半年 / 年 |
| `载体` | 数字从哪来 | 逐季营运公告 / 业绩新闻稿 exhibit 99.1 / 月度经营简报 / 月度营收 |

`含筹建` 默认 **不计入**：RevPAR / ADR / 出租率按定义只算已开业客房（Smith Travel
口径的 "available rooms"）。披露方明确把 pipeline 单列的，记「不计入（单列）」；
披露方没说清房间数口径的，记「披露未说明」——**不替它假设**。

## 汇总：33 家 × 三通道

| 市场 | 家数 | 有营运 KPI 的家数 | 通道 |
|---|---|---|---|
| CN-A | 6 | 1（锦江，且仅 2023 年两份简报） | `cninfo-operating` |
| HK | 5 | 1（大酒店 00045） | `hkex-disclosure` |
| US | 14 | 5（MAR/HLT/WH/H/CHH） | `sec-edgar-8k-operating` |
| TW | 4 | 0 | 仅 `twse-monthly-revenue`（月度营收，非营运 KPI） |
| JP | 2 | 0 | `edinet-api`（未接通） |
| EU | 2 | 0 | 无车道 |
| **合计** | **33** | **7** | —— |

7 家之外的 26 家**都不可得**，各自理由不同（见表末分组）；不可得是显式状态，
不是空值、不是 0、不拿同业补。

---

## 一、A 股（CN-A，6 家）— `cninfo-operating`

车道：巨潮公告全文检索 → 东方财富正文（纯文本，不解析 PDF）。
36 个概念 = 3 指标 × 2 地域（境内 / 境外）× 3 分档（总体 / 中端 / 经济）× 2 口径期（月 / 年初至今）。
实测落库 54 条（2023 年两份简报）。

| 实体 | 状态 | 可解析 KPI | 口径语义 | 汇率 | 含筹建 | 频率 | 证据 |
|---|---|---|---|---|---|---|---|
| 600754 锦江酒店 | `parsed` | RevPAR / ADR / 出租率 × 境内/境外 × 总体/中端/经济 | 境内 = 境内开业酒店（含中端、经济型分档）；境外 = 境外开业酒店 | 本币（境内 CNY；境外按原币汇总，简报不分币种列示） | 不计入（按开业酒店） | **单月** + **年初至今** 两种，必须分开 | 2023-09-27 简报（7 月 / 8 月单月，4 张表）、2023-12-22 简报（1-11 月累计，2 张表） |
| 600258 首旅酒店 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 无独立月度/季度经营数据简报；营运指标只在定期报告 MD&A 文字里（不在 `cninfo-operating` 类目） |
| 601007 金陵饭店 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 同上 |
| 000428 华天酒店 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 同上 |
| 301073 君亭酒店 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 同上 |
| 000610 *ST西旅 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 同上，且已被实施退市风险警示 |

### A 股的两个必读口径坑

1. **「1-11 月」是年初至今累计，不是 11 月单月。** 2023-12-22 简报的 RevPAR 是
   `1-11 月累计`（2 张表：境内 / 境外），而 2023-09-27 简报的「7 月」「8 月」是
   **单月**（4 张表：境内/境外 × 7/8 月）。同一家公司、同一年、同一个指标，
   累计与单月是两个概念（`...monthly` vs `...ytd`），**绝不并期、绝不互相换算**。
2. **平均行是房间数加权值，不是分档算术平均。** 总体行（`overall`）是加权结果：
   实测 169.26 ≠ 中端与经济型两档的算术平均 231.375。所以三条口径各自入库，
   不去重算、不用分档倒推总体。

**锦江 2024 年后为什么不可得**：该公司自 2024 年起停发独立经营数据简报，
营运指标只出现在定期报告正文里 —— 那是财报正文，按红线不解析。
读路径对 2024 年及以后呈现 `unavailable`，不是 `0`。

---

## 二、港股（HK，5 家）— `hkex-disclosure`

车道：HKEX 披露易公告类目过滤 → PDF 文本层（有文本层才解析，扫描件跳过）。
10 个概念 = RevPAR 4 个分区 + 租金 3 类物业 + 出租率 3 类物业。
实测落库 90 条（2024Q1–2026Q3 共 11 个季度，8 份公告）。

| 实体 | 状态 | 可解析 KPI | 口径语义 | 汇率 | 含筹建 | 频率 | 证据 |
|---|---|---|---|---|---|---|---|
| 00045.HK 大酒店（半岛） | `parsed` | RevPAR × 大中华/亚洲（除大中华）/美国/欧洲；租金与出租率 × 住宅/商场/写字楼 | 酒店 = 旗下开业酒店分区；物业 = 已出租物业（与酒店分表，不混算） | 本币 HKD（租金 HKD/平方呎/月） | 不计入 | 季 | 独立季度「未經審核營運數據」公告，2024Q4 起连续；中英双版逐条解析结果一致（各 30 条，值零差异） |
| 01179.HK 华住集团－Ｓ | `embedded-in-results` | 有 RevPAR / ADR / 出租率，但**写在季度业绩公告正文内**，无独立营运公告 | 混合（Legacy Huazhu / Legacy DH 分列） | 本币 CNY | 不计入 | 季 | 需在业绩公告正文里定位，非独立载体；本车道按「独立营运公告」类目过滤，故不覆盖 |
| 00069.HK 香格里拉（亚洲） | `no-operating-data-table` | —— | —— | —— | —— | —— | 22 条类目命中均为通用「Overseas Regulatory Announcement」；实测一份 1,189,868 字节 PDF 全文 100,339 字符中 **RevPAR 命中 0** |
| 01270.HK 朗廷－ＳＳ | `no-operating-disclosure` | —— | —— | —— | —— | —— | 无营运数据公告 |
| 00071.HK 美丽华酒店 | `no-operating-disclosure` | —— | —— | —— | —— | —— | 无营运数据公告（本席 2026-11-08 顶替已退市的 01992.HK 复星旅游文化） |

### 港股口径坑

1. **公告的「vs 上年变动」是校验器，不是装饰。** 每个当年值都必须复现公告自己披露的
   同比变动，不通过即丢弃（有 tamper 测试证明这条真的会拦）。这是港股 PDF 里
   唯一能自动发现「列错位」的独立证据。
2. **空白列不是 0。** 英文版空白会把单元格间距压扁导致等距歧义，实测退回
   「上年四列完整 + 当年自 Q1 顺序」并留 note；空白 2026Q4 不落 0。
3. **中英双版是必需项**：运行时只存英文版 `doc_url`，中文版是另一个 `NEWS_ID`，
   无法从英文编号推导 —— 所以双版解析必须一致，否则会漏期。

---

## 三、美股（US，14 家）— `sec-edgar-8k-operating` + `sec-edgar-facts`

两个数据面**不重叠、不互补**：`sec-edgar-facts` 取 XBRL 无维度事实与申报事件面；
`sec-edgar-8k-operating` 取 8-K Item 2.02 的 exhibit 99.1 **分地区/分品牌营运表**
（companyfacts 里没有这些元素）。

39 个概念 = 3 指标 × 13 序列（全系统 9 分区 + 公司自营 3 分区 + 自有租赁 1）。
实测落库 297 条（5 家 × 4 期，2025-03-31 ~ 2026-09-30）。

| 实体 | 状态 | 可解析 KPI | 口径语义 | 汇率 | 含筹建 | 频率 | 表形态 |
|---|---|---|---|---|---|---|---|
| MAR 万豪 | `parsed` | RevPAR/ADR/出租率 × {公司自营, 全系统} × {全球, 美加, 国际} | **可比口径（Comparable，非全量库存）** | **constant-$** | 不计入 | 季 | `Comparable Company-Operated / Systemwide` 两段 × `(US & Canada / International)`，品牌行 + 合计行 |
| HLT 希尔顿 | `parsed` | 同上 × {全球, 美国, 美洲除美, 欧洲, 中东非, 亚太} | **全量 system-wide** | **as-reported**（该表未声明 constant $） | 不计入 | 季 | `System-wide / Region / Brand / Segment` 四段；列序为 出租率·ADR·RevPAR |
| WH 温德姆 | `parsed` | 仅 RevPAR × {全球, 美国, 国际} | **Total System** | 本币 USD（表内未声明固定汇率） | 不计入 | 季（同一表给当年已过各季） | `HISTORICAL REVPAR` 表：指标行 + `2025`/`2024` 两行 × 5 值槽（Q1–Q4 + 全年）；**表内无 ADR/出租率**，无法用恒等式自校，仅结构校验 |
| H 凯悦 | `parsed` | RevPAR/ADR/出租率 × {全球, 美国, 美洲除美, 大中华, 亚太除大中华, 欧洲, 中东非, 自有及租赁} | **constant-$** | **constant-$** | 不计入 | 季 | `(in constant $)` 表，表头永远是 `RevPAR Occupancy ADR` 且常与口径行同格；另有 reported-$ 的 all-inclusive（Net Package）表，**本批不注册**以免同名列混口径 |
| CHH 精选国际 | `parsed` | RevPAR/ADR/出租率 × {全系统, 美国, 国际} | **currency-neutral system-wide** | **currency-neutral** | 不计入 | 季 | `CURRENCY-NEUTRAL SYSTEM-WIDE` 表；出租率同比以 **bps** 计；同稿另有 royalty rate 表（行名重复，靠「表头与数据行同表」排除） |
| HTHT 华住（ADR） | `not-an-8k-filer` | —— | —— | —— | —— | —— | 外国私人发行人，只报 20-F/6-K：实测 **271 份 6-K、0 份 8-K**（任务书「至少 4 家」含 HTHT 一项就此证伪） |
| IHG 洲际 | `not-an-8k-filer` | —— | —— | —— | —— | —— | 同上（6-K 申报人） |
| ATAT 亚朵 | `not-an-8k-filer` | —— | —— | —— | —— | —— | 同上 |
| GHG 格林酒店 | `not-an-8k-filer` | —— | —— | —— | —— | —— | 同上 |
| TCOM 携程 | `not-an-8k-filer` | —— | —— | —— | —— | —— | 同上（且属 OTA 平台，本无酒店营运三指标） |
| HGV 希尔顿度假 | `no-revpar-disclosure` | —— | —— | —— | —— | —— | 分时度假（timeshare），exhibit 890,755 字节全文 RevPAR 命中 0、出租率命中 0，8-K 正文无文件链接 |
| BKNG Booking | `ota-no-hotel-kpi` | —— | —— | —— | —— | —— | OTA 平台：披露间夜量/总预订额，不披露酒店口径 RevPAR/ADR/出租率 |
| EXPE Expedia | `ota-no-hotel-kpi` | —— | —— | —— | —— | —— | 同上 |
| ABNB 爱彼迎 | `ota-no-hotel-kpi` | —— | —— | —— | —— | —— | 同上 |

### 口径原文（读路径必须逐字引用，不许改写）

代码里 `scripts/hotel_extractors/sec_8k_operating.py` 的 `CALIBER` 常量就是这五行，
板面 / MCP 工具的 `caliber` 字段直接取用；测试钉住「代码与本文一致」：

* 万豪：constant-$ 可比口径（Comparable，非全量库存）；RevPAR＝ADR×出租率；跨 Company-Operated 与 Systemwide 两个口径分列，不混写
* 希尔顿：system-wide 全量口径，按披露原文为 as-reported（该表未声明 constant $）；RevPAR＝ADR×出租率；出租率同比以百分点计
* 温德姆：Total System 口径；RevPAR 为逐季水平值（该表不披露 ADR/出租率，故无法用恒等式自校，仅结构校验）；同一公告会给当年已过各季，取最新申报值
* 凯悦：constant-$ 口径；RevPAR＝ADR×出租率；同一公告另有 reported $ 的 all-inclusive（Net Package）表，本批不注册以免同名列混口径
* 精选国际：currency-neutral system-wide 口径；RevPAR＝ADR×出租率；出租率同比以 bps 计

### 美股口径坑（按危险程度排）

1. **累计块冒充当季**（最危险，已修）：五家的 FY（Q4）业绩稿把「Three Months Ended」
   与「Year Ended / Twelve Months Ended」并进同一张表，季度块后面紧跟行名一模一样的
   累计块。只认 3M 会让年度值按当季入库（HLT FY 114.39 顶掉 Q4 110.89）。现在累计期
   **先判掉**并清表头，且同一份 exhibit 内 `(序列, 指标, 期间)` 有唯一性守卫。
2. **同稿多套「报告基准」**（已修）：温德姆 FY2025 稿同时有本期基准表与
   `PRE Q2 2025 REPORTING BASIS` 对照表（Global 2025 Q1 一个 38.44、一个 36.13）。
   按「已披露季度数最多」取本期基准，旧基准整块不写。
3. **列归属不按顺序猜**：由 `RevPAR = ADR × 出租率` 恒等式裁决，只接受唯一解；
   同值时用表头列序作第二条独立证据（HLT 亚太出租率 71.9 与 RevPAR 71.78 几乎同值）。
4. **`reportDate` 不是财季末**：8-K 的 `submissions.reportDate` 是申报日
   （MAR 2026Q3 = 2026-11-02），财季末必须从正文「quarter ended September 30, 2026」
   解析。单元里 `period_end`（期间）与 `event_date`（事件日）是两列，绝不互换。
5. **`—` 不是缺失值，是「不适用/无变动」**：H 自有酒店出租率同比是「—」，量值仍在。
   按数字个数判形状会整行丢弃，按槽解析才不丢真数据。

### 跨公司可比性（用之前必读）

| 想做的事 | 能不能 | 原因 |
|---|---|---|
| MAR 与 HLT 的全球 RevPAR 直接比 | **不能** | MAR 是 constant-$ **可比**口径，HLT 是 as-reported **全量**口径 |
| HLT 与 WH 的美国 RevPAR 直接比 | 谨慎 | 两家都是全量，但 HLT as-reported、WH 未声明汇率处理 |
| 同一家跨季比 | 能 | 同一口径同一载体 |
| 分地区横向（亚太 vs 欧洲） | 能 | 同一张表内同口径 |

---

## 四、台股（TW，4 家）— 显式不可得

| 实体 | 状态 | 理由 |
|---|---|---|
| 2707.TW 晶华酒店 | `upstream-unreachable` | 仅接 `twse-monthly-revenue`（月度**营收**，非营运 KPI）；TWSE OpenAPI 从本机超时（与餐饮线 1.4 同一阻塞），`verified: {ticker: false, lanes: false}` |
| 2748.TW 云品国际 | 同上 | 同上 |
| 2739.TW 寒舍餐旅 | 同上 | 同上 |
| 2704.TW 国宾大饭店 | 同上 | 同上 |

台股没有 RevPAR 类公开月度营运 KPI 载体（月营收是财务面，不是经营面）。
读路径呈现 `unavailable`，原因写 `upstream-unreachable`，**不写「无数据」冒充已查**。

## 五、日股（JP，2 家）— 显式不可得

| 实体 | 状态 | 理由 |
|---|---|---|
| 9722.T 藤田观光 | `no-credential` | `edinet-api` 车道存在但需 EDINET API key（本机无）；有价证券报告书里也无月次 RevPAR |
| 9616.T 共立维护 | 同上 | 同上 |

## 六、欧股（EU，2 家）— 显式不可得

| 实体 | 状态 | 理由 |
|---|---|---|
| AC.PA 雅高集团 | `no-lane` | 宇宙内登记但无车道（Euronext 无对应披露车道）；`verified: {ticker: false, lanes: false}` |
| WTB.L 惠特贝瑞 | `no-lane` | 同上（LSE） |

欧股是 5.3 板面上「显式不可得」的两行：呈现为两条带原因的空白，不是两个 0。

---

## 七、硬纪律（写读两侧都要遵守）

1. **不跨公司补**：某家不可得时，绝不拿同业值填。板面上是不可得行，不是插值。
2. **不重算**：总体行用披露的加权值，不用分档倒推；RevPAR 用披露值，不用
   `ADR × 出租率` 重算（恒等式只在**校验**时用，不在**生成**时用——它是裁判，不是工厂）。
3. **不补 0**：空白 / `n/a` / `—` / 未披露 = 不写行。`0` 只在披露明确为 0 时出现。
4. **不并期**：月 / 年初至今 / 季 / 年 是四个粒度，各走各的概念（`...monthly` /
   `...ytd` / `...quarterly`）。
5. **口径随行**：每条观测都带 `caliber` 文本（美股）或由概念 code 编码（A 股 / 港股），
   没有口径的数字不入库。
6. **改了这里必须同步代码常量**：`sec_8k_operating.py` 的 `CALIBER` / `AVAILABILITY`、
   `ashare_operating.py` / `hkex_operating.py` 的状态常量、以及 `docs/hotel-universe.yaml`
   的 `lanes` —— 三处与本文冲突时，以本文为准并改代码，不是反过来。

## 附：概念清单（可直接查库）

```sql
-- 各通道概念数与观测数
SELECT source_used, count(DISTINCT concept_id) AS concepts, count(*) AS observations
FROM semantic_observations
WHERE concept_id IN (SELECT id FROM concepts WHERE code LIKE 'hotel.%')
GROUP BY source_used ORDER BY 1;
-- cninfo-operating | 36 |  54
-- hkex-disclosure  | 10 |  90
-- sec-edgar-8k-operating | 39 | 297
```
