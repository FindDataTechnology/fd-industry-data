# 花卉线观测序列精选 — add-flower-vertical task 3.1–3.4 真源

本文是花卉线**观测序列的挑选与命名真源**：哪些序列进板、每条的 `source_used`、期次范围、
以及不可得项的显式声明。注册脚本与看板按本文取数，不在板脚本里硬编码序列。

命名沿用既有车位契约（`TradeFlow` 先例：**一序列一码**，轴编进 concept code，
`measure` 统一）——`data_plane.series` 只按 `code + source + entity` 过滤。

---

## 1. 价格与供给板（12 条日频 + 9 条同源备用）

来源单元：`dounan-flower-price`（昆明市农业农村局·斗南花卉市场日价）。
`entity_type=commodity`、`measure=avg|high|low`、`unit=currency_cny`、`frequency=daily`。
`source_used='dounan-flower-price'`。

**序列身份 = concept × entity**：concept 只到口径层（`price.flower.dounan.avg`
/ `.high` / `.low`），**品种落在 entity**（`entity_type=commodity`，23 个品种各一条）。
这与 manifest 一致，也避免把「元/枝」与「元/把」两个量纲塞进同一个 code。
板面取数：`plane.series("price.flower.dounan.avg", "dounan-flower-price",
entity_id=<品种>)`。期次范围：**自接入日起累积**（上游仅滚动窗 ≈2 个月，
实测 2026-11-08 最老可见 2026-09-09，42 个有数日、23 个品种）。

选板口径：玫瑰四色（单头/多头）是斗南的价格基准，配百合三色 + 六个大品类主花。

| # | entity 品种（上游写法） | 说明 |
|---|---|---|---|
| 1 | 红玫瑰（单头） | 价格基准 |
| 2 | 红玫瑰（多头） | 多头基准 |
| 3 | 白玫瑰（单头） | |
| 4 | 黄玫瑰（单头） | |
| 5 | 粉玫瑰（单头） | |
| 6 | 复色玫瑰（单头） | |
| 7 | 白百合 | |
| 8 | 粉百合 | |
| 9 | 红百合 | |
| 10 | 康乃馨 | |
| 11 | 菊花 | |
| 12 | 洋桔梗 | 元/把 |
| 13 | 绣球 | 元/枝 |
| 14 | 金鱼草 | |
| 15 | 向日葵 | |
| 16 | 勿忘我 | 元/把 |
| 17 | 茉莉花 | 元/把 |
| 18 | 马蹄莲 | |
| 19 | 尤加利叶 | 配叶 |
| 20 | 紫罗兰 | |
| 21 | 白玫瑰（多头） | |
| 22 | 黄玫瑰（多头） | |
| 23 | 复色,玫瑰（多头） | **上游原文含半角逗号**，slug 归一但 `item` 字段照录 |

> 1–12 进板；13–23 同源同表、**注册但不上板**（板面控制信噪比，检索面仍可取）。
> `high` / `low` 同表可达（`price.flower.dounan.high` / `.low`），波4 价格板上板时
> 作为「区间带」一并注册；`avg` 不派生（上游给值）。
>
> **单位混装警告**：斗南表的 `计量单位` 逐品种不同（`元/枝` / `元/把` / `元/扎`）。
> 跨品种求"均价"是**错的**——板面只画单品种曲线，或按 `unit` 分组后再聚合。
> 宇宙 `entity` 是品种（commodity），不是"花"这个整体。

## 2. 全球贸易链板（6 条年频，零新建）

来源单元：`un-comtrade`（既有）。`entity_type=hs_code`、`measure=export`、`unit=usd`、
`frequency=yearly`。`source_used='un-comtrade'`。**已入库 2016–2025 共 10 年（57 点）**。

HS 口径（切花与观赏植物三档，**永不合并**——合并会掩盖品类结构）：

| # | concept code | HS6 | 含义 |
|---|---|---|---|
| 1 | `trade.export_flow.world` | `060311` | 玫瑰切花（鲜） |
| 2 | `trade.export_flow.world` | `060319` | 其他鲜切花 |
| 3 | `trade.export_flow.world` | `060120` | 花卉鳞茎/块茎/球根（生长中） |

- 前三条按 `cmd_code` 区分（`entity` 为 HS 码），进口侧 `flow_code=M` 另注册三条同形序列。
- `0602` 是**活体观赏植物**（含盆栽），与 `0603` 切花不同构；板上分两个分组呈现。
- 伙伴国轴：本期只取 `partner_code=0`（world）；伙伴国明细在波4 需要时按 `un-comtrade`
  既有参数化能力追加，**不与世界轴混画**。

### 2.1 实测锚与入库状态（2026-11-08）

2023 年度中国申报值（取自官方免 key 预览端点，**仅作口径锚**；生产入库走生产端点，
需要 `COMTRADE_API_KEY`）：

| HS6 | 出口（美元） | 进口（美元） |
|---|---|---|
| `060311` 玫瑰切花 | 17,283,341 | 5,876,523 |
| `060319` 其他鲜切花 | 39,250,772 | 42,701,244 |
| `060120` 鳞茎球根 | 19,371 | 23,927 |

读法（重要，别当成异常）：中国**其他鲜切花进口 > 出口**（4,270 万 vs 3,925 万），
而玫瑰切花是出口 > 进口（1,728 万 vs 588 万）——品类结构不同，不能拿「花卉顺差/逆差」
一句话概括；玫瑰与球根的绝对额差两个量级，**三档不可相加**。

抽取器 `scripts/flower_extractors/comtrade_flowers.py` 已就绪（6 组合、按请求过口径闸、
串档即拒），`tests/test_comtrade_flowers.py` 用真实回包 fixture 钉住映射；`hs_code` 实体
`060311/060319/060120` 已注册。**入库未跑**：本机无 `COMTRADE_API_KEY`（生产侧有），
故覆盖表仍记 0 条——别把「测试绿」读成「已入库」。

## 3. 面积与产量（5 条截面 + 5 条备用）

### 3.1 AIPH 种植面积（新单元 `aiph-stats`）

`entity_type=country`、`measure=area`、`unit=hectare`、`frequency=yearly`。
`source_used='aiph-stats'`。

| # | concept code | 说明 |
|---|---|---|
| 1 | `area.flower.rose.planted` | 玫瑰种植面积（国别截面） |
| 2 | `area.flower.carnation.planted` | 康乃馨 |
| 3 | `area.flower.chrysanthemum.planted` | 菊花 |
| 4 | `area.flower.lily.planted` | 百合 |
| 5 | `area.flower.cut_foliage.planted` | 切叶 |

- 9 个 product 全部注册（`rose/carnation/orchid/chrysanthemum/lily/christmas_tree/cut_foliage/
  flowering_plant/green_foliage_plant`），板上取前 5 个。
- **品类必须编进 concept code、国家落 entity**（同 `price.food.fao.<commodity>` 先例）：
  若把品类放到 entity 侧，同一国家的玫瑰与康乃馨会撞 `uq_sem_obs`
  (concept, entity, date, granularity) 主键而互相覆盖——第一版曾写成
  `area.flower.planted.total` + entity 双轴，波4 建模时发现并纠正（三个旧 code 已从库里撤销，
  从未写入过观测）。
- **每行自带 `ref_year`**（2005–2024 混装）：板面必须显示 `ref_year`，**禁止**把条形图画成
  "2025 年各国面积"，也**禁止**跨 `ref_year` 求增速。
- `area_open_ha` / `area_protected_ha` 同表可选，本期不注册（NULL 面太大）。
- **核对记录（别当 bug 修）**：西班牙玫瑰面积显示 **87 公顷**，看着像解析错位，实则不然——
  原始行 `total=87 / open=13 / protected=74`（87 = 13 + 74），来自同一张 `Roses Production` 表，
  是该年鉴对西班牙的申报口径（切花玫瑰），**上游就是这么报的**。中国 19,049（2020）、
  印度 41,000（2023）同样直接来自该表。三国的 open/protected 为 NULL 是因为该版表只有合计列。

### 3.2 Eurostat 花卉主面积（既有 `eurostat-api`，`apro_cpsh1`）— 已接入

`source_used='eurostat-api'`，concept `area.flower.all.planted`，entity=国别（含 `EU27_2020` 合计）。
**2026-11-08 已实测并入库 77 点**（8 geo × 10 年；EU 合计 7 年，2018 起）。

口径三条（实测，别改错）：

1. 花卉作物码 `N0000`「Flowers and ornamental plants (excluding nurseries)」在该数据集里
   **只有 `MAR_THS_HA`（主面积·千公顷）有值**；`AR_THS_HA` / `HPRD_HUMD_EU_THS_T` /
   `YLD_HUMD_EU_T_HA` 查询均返回 0 值 → 这条序列**是面积，不是产量**。
2. 入库折成**公顷**（上游千公顷 ×1000），与 AIPH 面积同单位但**分源分图**，不合并。
3. `EU27_2020` 与历史口径 `EU` / `EU28` 覆盖期不同，**不可相加**；成员国与欧盟合计也不可相加。
   板面因此用**双轴**（成员国左轴 / 欧盟合计右轴虚线），图例把合计排最后。

### 3.3 USDA / 日本 产量（挂账）

`usda-quickstats`（花卉口径需确认是否有对应 commodity group）、`jp-estat`（花き 出荷量/産出額）
——**状态：挂账**，未实测。禁止凭 API 存在就宣称有数据。

## 4. 公司财务（波4 消费，非本波注册）

来源：`sec-edgar-facts`（合并口径）+ `awaken-flower-report-ingest` 的 `sec-xbrl-segment-extraction`
（分部口径，`source_used='sec-xbrl-instance'`）+ `stock-zygc`（A股分部）。
宇宙真源：`docs/flower-universe.yaml`（7 实体）。

| 序列 | 实体 | 状态 |
|---|---|---|
| `revenue.total` / `net_profit.total` | FLWS | 可得（US XBRL） |
| `revenue.segment.consumer_floral_and_gifts` / `.gourmet_foods_and_gift_baskets` / `.net_bloomnet` | FLWS | 可得（10-K instance，逐家成员映射） |
| `revenue.segment.*` | CENT / SMG / HYFM / EDBL | **待实测**（各家是否有分部轴） |
| `revenue.segment.*` | 002200 | 可得（`stock-zygc` 东财主营构成，苗木 0.23%） |
| 全部财务 | `1377.T` | **不可得**（EDINET key 未申请） |

## 4bis. EU 花卉主面积（3.3，Eurostat 腿）

来源 `eurostat-api`，数据集 `apro_cpsh1`（Crop production in EU standard humidity），
作物码 **`N0000`**「Flowers and ornamental plants (excluding nurseries)」，
口径 **`MAR_THS_HA`**（Main area，千公顷）。

| 项 | 实测（2026-11-08 直连 HTTP 200） |
|---|---|
| 该作物可用口径 | **只有主面积**；`AR_THS_HA` / `HPRD_HUMD_EU_THS_T` / `YLD_HUMD_EU_T_HA` 均返回 **0 值** |
| 覆盖 | 41 geo × 12 年（2015–2026），316 个非零值 |
| 本线入库 | concept `area.flower.all.planted`；8 geo（EU27_2020 / NL / IT / DE / FR / ES / PL / BE）**77 点** |
| 单位 | 入库折成**公顷**（上游千公顷 ×1000），与 AIPH 面积同单位但**分源不混图** |
| EU 合计 | 2021 80,700 → 2022 71,460 → 2023 68,050 → 2024 **66,510 公顷**（连续下降） |
| 不可相加 | EU27_2020 与历史口径 EU / EU28 覆盖期不同；成员国与欧盟合计不可相加 |

规则 `eu_flower_area_yoy_negative`（`aggregate: year_mean`，阈值 0）：上线即越阈
（2024 同比 −2.263%），这是真实的面积收缩，不是噪声。

**同族未接**：`usda-quickstats`（花卉口径）、`jp-estat`（花き 出荷量/産出額，需 e-Stat 应用 ID）。

## 5. 记录总数与覆盖表（3.4）

| 分组 | 注册条数 | 上板条数 | 期次范围 | 来源分层 |
|---|---|---|---|---|
| 斗南日价 | 23 | 12 | 自接入日起（上游滚动 ≈2 月） | 官方（市农业农村局） |
| 全球贸易 | **0**（计划 6） | **0** | 未入库 | 国际组织（UN Comtrade） |
| AIPH 面积 | 9 | 5 | 各版年鉴快照（`ref_year` 2005–2024 混装） | 行业协会（AIPH/Union Fleurs） |
| EU 花卉主面积 | 1（`area.flower.all.planted`） | 1（8 geo / 77 点） | 2015–2024（EU 合计 2018 起） | 官方统计（Eurostat） |
| 公司财务 | 按实体 × 概念 | 波4 定 | 近 4 季度 + 1 年报 | 监管申报（SEC）/ 交易所（东财） |
| **合计** | **34（不含财务，其中贸易 0）** | **24** | — | — |

- 无重复概念：三个来源的概念命名空间互不重叠（`price.flower.dounan.*` / `trade.*` /
  `area.flower.planted.*`）。
- **2026-11-08 校正（原表虚高）**：原表写「全球贸易 6 注册 / 6 上板」是**错的**——库里
  `trade.*` 概念只有 5 条，且全是锂电口径的按报告国导出（`trade.export_flow.{de,jp,uk,us,world}`），
  花卉三档（060311/060319/060120）**一条也没有**；`flower-trade-chain` 板实读 4/4 序列
  `status: absent`。根因不是抓不到，是**生产排期无法给一个单元传参数**：
  `fd-runner --params` 通道存在（`tests/test_runner_params.py`），但 `k8s/cronjob-template`
  的 args 固定为 `["<source>", "--limit", N, "--window-auto"]`，AppSet 不转发 params
  → 一个 manifest 只能跑一套默认参数。要接花卉三档需要「参数变体排期」这项平台能力
  （见 `docs/flower-debt-ledger.md` F6/F7 与缺口一节），不能在垂直线上私造副本。
- **不可得项显式声明**：`1377.T` 全部财务、`CENT/SMG/HYFM/EDBL` 的分部占比、
  EU/USDA/JP 产量、KIFA 行情 —— 各自带障碍与解锁条件，绝不以 0 或估算值出现。

## 6. 缺口与前置（诚实记账）

1. **`hectare` 单位已补**：`fd-open-data-protocol/vocabulary/units.yaml` 于 2026-11-08
   追加 `hectare`（UN/CEFACT `HAR`），`validate_vocabulary()` 零错误。原先该词表只有
   货币/人数/股/指数/千吨/百分比/年/计数，面积序列无量纲可依。
2. **`measure: area` 无先例**：manifest 层 `measure` 是自由文本（无注册表校验），
   但 `PlantedArea` 概念族尚未在 `concepts.yaml` 建立——波2 注册时需先补该族
   （或把面积挂到既有族下并注明口径），否则变量无法落到 ontology store 的 `concepts` 表。
3. **斗南序列的 item 解析风险**：上游出现过 `复色,玫瑰（多头）`（半角逗号）与
   `复色玫瑰（单头）` 两种写法。注册时用**显式词表映射**（本文第 1 节），禁止按字符串相似度猜。
