# 酒店行业垂直线 — 波0 序列精选清单

> change：`add-hotel-vertical` · 任务 1.1 · 定稿日期 2026-11-08
> 数据面：生产观测库 `fd_open_data`（mesh `100.64.0.3:30432`），长形表 `semantic_observations`
> 口径契约：`industry-observation-supply`（自然键 `(concept_id, entity_type, entity_id, date, granularity, source_used)`，
> `INSERT ... ON CONFLICT DO NOTHING` 只增不改，缺测不插值不补零）

## 0. 这份清单管什么

波0 的验收标准是「零新爬虫出序列」：从**已经在役单元**里把酒店/旅游相关的真实历史抽成观测、过命名门，
先把「行业景气 / 旅游需求 / 住宿业价格」三条研究线立起来。

**但本次实测的结论是：既有源里几乎没有可直接用的住宿业宏观序列。** 逐条证据见 §1。
因此本清单分两半，且**绝不混写**：

- **§1 既有源可得部分**：上游实测窗口 + 期数（2026-11-08 直连实测），部分是「确认不可得」；
- **§2 新源待接部分**：三个新单元（波1）已建好且能实跑，但**尚未入库 = 零观测**，
  只能写「产出面」不能写「已得序列」。

每条序列必填六项：`source_used` / 源侧序列 ID / 粒度 / 实测窗口与期数 / 单位 / 趋势门状态。
趋势门口径沿用餐饮线：**≥24 期入 verified；12–23 期按 `short_window` 登记并带声明；<12 期不入库**。

## 1. 既有源可得部分（实测 2026-11-08，直连，非声明值）

### 1.1 欧盟 · Eurostat —— **本次唯一能出宏观住宿序列的既有源**

上游：`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/<dataset>`（JSON-stat 2.0，免 key）。
下面六条**已在 2026-11-08 回填入库**：`scripts/extract_hotel_wave0.py` 写入 **745 条观测**
（139 + 188×3 + 21 + 21），六概念全部过命名门（`concepts.verified=true` +
`registry_entries.verified=true`），二次跑 `inserted=0`。入库路径仍是「既有单元 + 新命令面」，
零新爬虫 —— 四个命令由 `fd_industry_data.provider` 按名分发，不进 `run_eurostat_api` 的排期轮转。

| # | 概念码（拟） | 源侧序列 ID（逐维） | 粒度 | 上游实测窗口（期数） | 单位 | 趋势门 |
|---|---|---|---|---|---|---|
| E1 | `hotel.eu.turnover.accommodation.monthly` | `sts_setu_m` · `indic_bt=NETTUR` · `nace_r2=I55`（住宿业）· `s_adj=SCA` · `unit=I21` · `geo=EU27_2020` | month | **2015-01 → 2026-07（139）** | 指数 2021=100 | verified |
| E2 | `hotel.eu.nights.monthly` | `tour_occ_nim` · `c_resid=TOTAL` · `unit=NR` · `nace_r2=I551-I553` · `geo=EU27_2020` | month | **2011-01 → 2026-08（188）** | 夜数（次） | verified |
| E3 | `hotel.eu.nights.foreign.monthly` | `tour_occ_nim` · `c_resid=FOR` · `unit=NR` · `nace_r2=I551-I553` · `geo=EU27_2020` | month | **2011-01 → 2026-08（188）** | 夜数（次） | verified |
| E4 | `hotel.eu.nights.yoy.monthly` | `tour_occ_nim` · `c_resid=TOTAL` · `unit=PCH_SM` · `nace_r2=I551-I553` · `geo=EU27_2020` | month | **2011-01 → 2026-08（188）** | % 同比 | verified |
| E5 | `hotel.eu.capacity.bed_places.annual` | `tour_cap_nat` · `accomunit=BEDPL` · `unit=NR` · `nace_r2=I551` · `geo=EU27_2020` | year | **2005 → 2025（21）** | 床位（个） | **short_window(21)** |
| E6 | `hotel.eu.nights.annual` | `tour_occ_ninat` · `c_resid=TOTAL` · `unit=NR` · `nace_r2=I551` · `geo=EU27_2020` | year | **1990 → 2025（21，有缺年）** | 夜数（次） | **short_window(21)** |

**四条口径注记（必须跟序列一起走，不许让读者猜）**

1. **`sts_setu_m` 只服 I55，不服 I551**：`nace_r2=I551`（旅客住宿）实测 **0 值**，只有 I55
   （住宿业整体）有数。所以 E1 是「住宿业营业额」，不是「酒店营业额」。
2. **I55 序列起点是 2015-01，不是 2000-01**：同一数据集 `nace_r2=I56`（餐饮）有 319 值
   （2000-01→2026-07），I55 只有 139 值。同一数据集不同 NACE 分支的历史深度不一样，
   **不许按 I56 的深度写 I55 的口径说明**。
3. **HICP 住宿价格指数不存在**：`prc_hicp_midx` 的 `coicop=CP11`（餐馆与住宿）与 `CP112`
   （住宿服务）实测均 **404 `not available for dissemination`**；只有 `CP111`（餐饮服务）
   有 301 值（2000-12→2025-12，餐饮线已用）。故**酒店线没有欧盟住宿价格指数**，
   价格面只能走 E1 营业额指数或各国自有 CPI 分项 —— 不要拿 CP111 冒充住宿价格。
4. **E6 有缺年**：`tour_occ_ninat` 1990–2025 共 36 年却只有 21 个值（早期年份对 EU27 聚合为空）。
   空年**不补、不插值**，图上必须有断点。
5. **E5/E6 是 21 期 → 12–23 档**：入命名门时按 `short_window` 登记（`registry_entries.provenance.short_window`），
   可做水平与年度对比，**不得画趋势线**；E1–E4 均 ≥139 期，无此限制。
6. **价格侧缺失已进 provenance**：四条 `hotel.eu.nights*` 概念带 `provenance.pairing`
   = `{counterpart: accommodation_price_index, state: missing, debt: D1/D5, reason: ...}`，
   板面算「价量背离」时必须渲染缺失侧，不得用 CP111（餐饮）或 E1 营业额指数顶替。

### 1.2 中国 · `nbs-stats` —— **确认无住宿业目标**

代码级证据（不是猜测）：`spiders/nbs-stats/spider.py` 的 `INDICATOR_CATEGORIES` 只有五类 ——
`gdp` / `price_indices` / `industrial` / `population` / `employment`，**没有任何住宿业或旅游业条目**。
本机直连 `data.stats.gov.cn/easyquery.htm` 实测 **403 `reason:UrlACL`**（IPv6 出口被 ACL 拦），
与该源既有记录一致（集群侧走浏览器指纹通道）。

结论：**住宿业增加值 / 住宿业 CPI 分项本波不可得**。要补必须先在 `INDICATOR_CATEGORIES` 里
新增 `accommodation` 类目并实测 zbcode，属**新任务**，不在波0 范围。

### 1.3 美国 · `census-eits` —— **无住宿类目；NAICS 721 需 key**

代码级证据：单元只声明两类 `category_code` —— `mrts/44X72`（零售贸易含餐饮）与 `mwts/42`（批发）。
**没有 `721`（住宿）**。补 `721` 需要 `CENSUS_API_KEY`（`_api_key()` 缺失即硬失败），
本机 `~/.config/finddata/dashboards.env` 只有 `FD_OPEN_DATA_DSN` / `YEARBOOK_DSN` —— **无该 key**。

结论：**本波不可得，挂账**。注意即便补上，`721` 是住宿业零售口径的销售额，不是 RevPAR/出租率。

### 1.4 日本 · `jp-estat` —— 需 `ESTAT_APP_ID`；且单元是「首表」形态

代码级证据：`ESTAT_APP_ID` 从环境读，缺失即红；本机 env **无该变量**。
另外单元的实现是 `getStatsList?searchWord=...` → 取 `TABLE_INF[0]` 第一张表，
**没有「按统计表 ID 定向」的参数**。宿泊旅行统计（統計表 ID 固定）要用它必须先加参数。

结论：**本波不可得，挂账（两个独立阻塞：凭据 + 单元形态）**。

### 1.5 美国 · `bls` —— **更正：有 `spider.py`，但不是序列 API**

早前记录「`bls` 无 `spider.py`」**实测为误** —— `spiders/bls/spider.py` 存在，
入口 `run_bls(limit)`。但它是 scrapling 爬虫，`START_URLS` 只有 `https://www.bls.gov/data/` 索引页，
**没有系列 ID 参数**，取不到住宿业 CPI（`CUUR0000SEHB` 一类）的时间序列。

结论：**本波不可得**。要补必须给该单元加「BLS 公共 API v1 系列 ID」通道（`api.bls.gov` 对本机/集群
均 Akamai 403，见餐饮线 `dbnomics-api` 冻结记录），属新任务。

### 1.6 已在库：企业侧 85 条（波3 三通道产出，已实测）

| 车道 | `source_used` | 概念数 | 观测数 | 最长实测窗口（期数） | 备注 |
|---|---|---|---|---|---|
| A 股经营简报 | `cninfo-operating` | 36 | 54 | 2023-07 → 2023-11（2 个月 + 1 个累计期） | 单月 24 条 + 年初至今 12 条；只有锦江 2023 两期 |
| 港股经营数据 | `hkex-disclosure` | 10 | 90 | 2024-03-31 → 2026-09-30（11） | 大酒店 00045 独有载体；含 RevPAR/OCC/租金 |
| 美股 8-K | `sec-edgar-8k-operating` | 39 | 297 | 2025-03-31 → 2026-09-30（23） | 最长 23 期 —— **仍不足 24 期趋势门** |

**三条共同事实（对板面影响很大）**

1. 这 85 条已于 2026-11-08 全部过命名门（`verified=true`，`scripts/naming_gate_hotel.py --apply`），
   读路径可见；但每条都带 `provenance.trend_gate.disposition=level_only` 声明并在声明里写了实测期数
   （1–11 期），**只作同一时点的水平对照，不得画趋势**。
2. **没有一条达到 24 期**：美股最长 23、港股 11、A 股 2。**企业侧目前无法做趋势线，只能做水平对照**。
3. 三条车道都是「企业自报」，与 §2 的协会/文旅部口径**不可混算**（同一家酒店，企业报自有组合、
   协会报全行业），任何图必须标口径。

## 2. 新源待接部分（波1 三单元，**其中两源已入库**）

三个单元已建好、golden live 重放全绿。**截至 2026-11-08：`mct-star-hotel` 与
`mct-tourism-demand` 已抽成观测入库**（8,482 + 64 条，二次跑 inserted=0，概念过命名门）；
`ctha-hotel-report` 仍是「一接就能拿到什么」（未抽取，因为索引面登录墙使它只能一次性直给 PDF）。
下表「产出面」列是实跑实测，不是声明值。**不与 §1 混读。**

| 单元 | 产出面（实跑实测） | 期次 | 粒度 | 一期能立的序列 |
|---|---|---|---|---|
| `mct-star-hotel` | **8,482 行 / 5 期（2023Q2–2024Q3）已入库**：分省 / 按星级 / 50 城 / 50 城×星级四表 | 2024Q3 最新（**系列已停更**） | 季 | 27 概念：12 指标 × 星级档（all/1–5），实体 33 region + 47 city；全国 6,129 vs 50 城小计 2,209 分实体 |
| `mct-tourism-demand` | **64 行 / 7 份文档已入库**（2024 年度 — 2026 前三季） | 2026Q1–Q3 最新（季更活跃） | 年 / 半年 / 前三季 / 单季（`granularity` 区分） | 18 概念：出游人次/花费/入境/出境/星级饭店/旅行社/景区，口径（全国/城镇/农村/外国/港澳台/内地）落在概念码 |
| `ctha-hotel-report` | 168 行（88 metric + 80 ranking）/ 2 份 PDF（**未抽取**） | 2025 年度 | 年 | 行业规模（设施/客房）、四档结构、城市级别、连锁化率、前 50 集团、外资前 30 品牌 |

**三条硬约束（接线时不许绕过）**

1. **`mct-star-hotel` 已停更**：最新一期 2024Q3（2024-12-26 发布），2024Q4 及以后精确检索 `totalHits=0`。
   接进来只能是**历史底数 + 断更检测**，不是当期指标。断更告警属任务 6.4。已入库 5 期为
   2023Q2–2024Q3（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空，均按「无数据」处理，不补零）。
2. **`ctha-hotel-report` 索引面登录墙**：`/cat/35/pN.html` 302 → `/account/login`，只能 `reports=` 直给 PDF，
   **不可点亮 `schedule`**（下一版哈希必变）。所以它的序列**天然是年更一次性**，不是可自动续更的序列。
3. **协会口径 ≠ 星级饭店口径 ≠ 企业自报**：37.5 万家酒店（协会）vs 6,129 家星级饭店（文旅部 2024Q3）
   vs 企业自有组合（三车道）。**三套宇宙，任何跨源图必须分行分色标口径。**

## 3. 板面可用性判定（决定 5.1 / 5.2 怎么画）

| 板面 | 现在能画什么 | 缺什么 |
|---|---|---|
| 5.1 行业景气板 | 欧盟：住宿业营业额指数（E1，月，139 期，**已入库**）+ 住宿夜数（E2/E4，月，188 期，**已入库**）——**这两条是波0 唯一能成趋势线的宏观序列** | 中国当期景气**无可用序列**（NBS 无住宿类目、星级饭店已停更）；美国宏观无 |
| 5.2 供需板 | 供给：协会年度规模（§2 ctha，一次性）+ 欧盟床位容量（E5，21 期 short_window）；需求：文旅部出游（§2，季更）+ 欧盟夜数（E2/E6） | 供给侧没有月度/季度序列；`mct-star-hotel` 断更使「供给当期」不可得 |
| 5.3 公司板 | 85 条企业序列做**水平对照**（RevPAR/OCC/ADR 分地区，**已全部 verified**） | 全都 <24 期，**不能画趋势**；且带 `level_only` 声明 |

**结论：波0 能立起来的只有「欧盟住宿供需双月度序列」一条完整研究线**（E1 + E2/E4 + E5/E6）。
中国线要等 `mct-tourism-demand` 入库（有 2024–2026 六期，可做准趋势）+ 民宿/OTA 补充源。
这条判断直接影响 5.1/5.2 的验收口径 —— **不许把「欧盟有序列」写成「酒店行业景气板已成立」**。

## 4. 挂账（本波不解，写明阻塞原因）

| # | 挂账 | 阻塞原因（实测） | 解锁条件 |
|---|---|---|---|
| D1 | 中国住宿业增加值 / 住宿业 CPI 分项 | `nbs-stats` 无该类目；easyquery 本机 403 `UrlACL` | 新增 `accommodation` 类目并定 zbcode |
| D2 | 美国住宿业月度销售（NAICS 721） | `census-eits` 无 721 类目；`CENSUS_API_KEY` 缺失 | 拿 key + 加类目 |
| D3 | 日本宿泊旅行统计 | `ESTAT_APP_ID` 缺失；单元只取 `TABLE_INF[0]` | 拿 appId + 加「按表 ID 定向」参数 |
| D4 | BLS 住宿业 CPI 系列 | 单元是索引页爬虫，无系列参数；`api.bls.gov` 对本机/集群 403 | 加公共 API v1 通道（或走 DBnomics 镜像并接受冻结） |
| D5 | 欧盟住宿价格指数 | HICP `CP11` / `CP112` 实测 404 未发布 | 无 —— 上游不存在，改用营业额指数代理并写明 |
| D6 | 企业侧趋势线 | 85 条最长 23 期，<24 | 等 8-K / 港股公告再滚 1–2 季 |

## 5. 自检口径

- 本清单所有「实测窗口/期数」均为 **2026-11-08 直连上游或查库所得**，非声明值；
- 标「不可得」的每一项都有**代码级或 HTTP 级证据**，没有一条是「大概没有」；
- §1（既有源）与 §2（新源待接）严格分开，§2 的期数一律是「产出面」不是「已入库观测」；
- §1 的入库数字是**查库实测**：`hotel.eu.*` 745 条（139/188/188/188/21/21）+ 企业侧 441 条；
  二次跑 `inserted=0`；命名门 91/91 双置 verified（`short_window=2`、`level_only=85`、`pairing=4`）。
