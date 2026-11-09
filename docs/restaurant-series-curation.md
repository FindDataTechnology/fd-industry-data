# 餐饮行业垂直线 — 波0 首批精选序列清单

> change：`add-restaurant-vertical` · 任务 1.1 · 定稿日期 2026-11-07
> 数据面：生产观测库 `fd_open_data`（mesh `100.64.0.3:30432`），长形表 `semantic_observations`
> 口径契约：`industry-observation-supply`（自然键 `(concept_id, entity_type, entity_id, date, granularity, source_used)`，
> `INSERT ... ON CONFLICT DO NOTHING` 只增不改，缺测不插值不补零）

## 0. 这份清单管什么

波0 的验收标准是「零新爬虫出序列」：把**已经在役单元**里已有的真实历史抽成观测、过命名门，
把「餐饮景气 / 成本链 / 价格对照」三条研究线先立起来。因此本清单里每条序列都标注：

- **源单元（`source_used`）**：行级溯源字段，一条序列只能有一个 `source_used`；
- **源侧序列 ID**：上游真实标识（payload 里的 `series_key` / `short_desc` / `item` / 端点符号），不是自造编号；
- **真实窗口**：库里实测的起止日期与期数（2026-11-07 查库所得，非声明值）；
- **趋势门状态**：≥24 期正常入库；12–23 期按 `short_window` 登记并带声明；<12 期不入库；
- **口径注记**：任何「看起来像 A 实际是 B」的地方必须写在序列旁边，不许靠读者猜。

**首批合计 28 条序列 + 3 家台股标的**（其中 9 条是既有在库序列的复用，19 条为本次新增入库）。

## 1. 宏观需求面（9 条）

### 1.1 中国（NBS 月度，`source_used = nbs-stats`）

上游通道：`data.stats.gov.cn` 的 `dg` 公共发布接口（月度数据），非 easyquery（后者对本站点出口
WAF 403，见 §5）。上游口径每年 1、2 月不单独发布当月社零，窗口内为 3–12 月连续月。

| # | 概念码 | 源侧序列 | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `retail.cn.catering.monthly` | `series=餐饮收入`（dg 月度） | month | 2012-03 → 2026-09（147） | 亿元 | verified |
| 2 | `retail.cn.catering.yoy` | `series=餐饮收入同比` | month | 2012-03 → 2026-09（147） | % | verified |
| 3 | `retail.cn.catering.limitup.monthly` | `series=限额以上单位餐饮收入` | month | 2012-03 → 2026-09（147） | 亿元 | verified |
| 4 | `retail.cn.catering.limitup.yoy` | `series=限额以上单位餐饮收入同比` | month | 2012-03 → 2026-09（147） | % | verified |
| 5 | `retail.cn.social.monthly` | `series=社会消费品零售总额` | month | 2012-03 → 2026-09（147） | 亿元 | verified |
| 6 | `retail.cn.goods.monthly` | `series=商品零售` | month | 2012-03 → 2026-09（147） | 亿元 | verified |

注：`social_yoy`（社零同比）随抓但波0 未绑概念码，payload 留在 crawl 库待后续波次按需注册——不静默丢。

### 1.2 美国（2 条）

| # | 概念码 | 源单元 | 源侧序列 ID | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|---|
| 7 | `retail.us.retail_food_services_total.monthly` | `census-eits` | MARTS `category_code=44X72`, `data_type_code=SM`, `seasonally_adj=no` | month | 2020-01 → 2026-08（14） | 百万美元 | verified · **short_window(14)** |
| 8 | `price.cpi.us.food_away_from_home` | `dbnomics-api` | `BLS/cu/CUSR0000SEFV` | month | 1953-01 → 2025-01（865） | index 1982-84=100 | verified |
| 9 | `price.cpi.us.food_at_home` | `dbnomics-api` | `BLS/cu/CUSR0000SAF11` | month | 1952-01 → 2025-01（877） | index 1982-84=100 | verified |

**两条口径注记（务必保留在板面 caveat 里）**

1. **#7 不是纯餐饮口径**：MARTS `44X72` = 「零售贸易 + 餐饮服务」总计。命名门已按实情改码改名
   （原 `retail.us.foodservice.monthly` / 美国餐饮零售销售额 会让人误读为单餐饮）。真正的餐饮单项
   是 MRTS 类别 `722`，本波未落库 → 挂账 §5。
2. **#8/#9 经 DBnomics 镜像，观测冻结在 2025-01**：`api.bls.gov` 与 `download.bls.gov` 对本机与
   集群出口都是 Akamai 403（2026-11-07 实测，含真 key + 浏览器 UA），镜像站是唯一可达免 key 通道。
   2025-02 之后的月份一律按**不可得**处理：不插值、不延展、不标 0。剪刀差图要显式标「末值 2025-01」。

### 1.3 欧盟（1 条）

| # | 概念码 | 源单元 | 源侧序列 ID | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|---|
| 10 | `hicp.eu.catering.monthly` | `eurostat-api` | `prc_hicp_midx` · `coicop=CP11`（餐饮住宿服务）· `geo=EU27_2020` · `unit=I15` | month | 2000-12 → 2025-12（301） | index 2015=100 | verified |

实体 `EU27_2020` 本波新注册（`entities.id=300990`，entity_type=`country`）。上游缺口仅 null，
无插值；末值 2025-12 系 Eurostat 发布延迟，非断流。

## 2. 成本链面（19 条）

### 2.1 FAO 食品价格指数（8 条，既有在库，`source_used = fao-food-price`）

上游：FAO 官方 `food_price_index_nominal_real.xlsx`（`index_name` 字段区分序列）。

| # | 概念码 | 源侧序列 `index_name` | 粒度 | 真实窗口（期数） | 状态 |
|---|---|---|---|---|---|
| 11 | `price.food.fao.index` | Food Price Index (nominal) | month | 1990-01 → 2026-10（442） | verified |
| 12 | `price.food.fao.cereals` | Cereals | month | 1990-01 → 2026-10（442） | verified |
| 13 | `price.food.fao.dairy` | Dairy | month | 1990-01 → 2026-10（442） | verified |
| 14 | `price.food.fao.meat` | Meat | month | 1990-01 → 2026-10（442） | verified |
| 15 | `price.food.fao.oils` | Vegetable Oils | month | 1990-01 → 2026-10（442） | verified |
| 16 | `price.food.fao.sugar` | Sugar | month | 1990-01 → 2026-10（442） | verified |
| 17 | `price.food.fao.annual.nominal` | Food Price Index (nominal, annual) | year | 1961 → 2026（66） | verified |
| 18 | `price.food.fao.annual.real` | Food Price Index (real, annual) | year | 1961 → 2026（66） | verified |

### 2.2 生猪与猪肉（3 条，既有在库，`source_used = moa-hog-price`）

上游：农业农村部「全国 500 个县集贸市场和采集点」周报（周度，`report_url` 逐期可回指原报告）。

| # | 概念码 | 源侧序列 | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 19 | `agri.hog.price.spot` | `item=活猪` | week | 2026-07-20 → 2026-10-26（15） | 元/公斤 | verified · **short_window(15)** |
| 20 | `agri.pork.price.market` | `item=猪肉（集贸市场）` | week | 2026-07-20 → 2026-10-26（15） | 元/公斤 | verified · **short_window(15)** |
| 21 | `agri.pork.price.wholesale` | `item=猪肉（批发市场）` | week | 2026-07-20 → 2026-10-26（15） | 元/公斤 | verified · **short_window(15)** |

周度序列按周累积，满 24 周后自动退出 short_window（约 2027-01）。上游另有 `mom_pct`/`yoy_pct`
未绑概念码，留在 payload。

### 2.3 饲料与油脂（4 条，本次新增）

| # | 概念码 | 源单元 | 源侧序列 ID | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|---|
| 22 | `futures.dce.meal.settle.daily` | `dce` | 新浪期货日 K `symbol=M0`（豆粕主力连续） | day | 2010-06-08 → 2026-11-06（3,726） | 元/吨 | verified |
| 23 | `futures.dce.corn.settle.daily` | `dce` | 新浪期货日 K `symbol=C0`（玉米主力连续） | day | 2010-06-08 → 2026-11-06（3,728） | 元/吨 | verified |
| 24 | `price.palm_oil.imf.monthly` | `imf-pcps` | `G001.PPOIL.INDEX.M` | month | 1992-01 → 2026-09（417） | index 2016=100 | verified |
| 25 | `price.sugar.imf.monthly` | `imf-pcps` | `G001.PSUGA.INDEX.M` | month | 1992-01 → 2026-09（417） | index 2016=100 | verified |

- 合约实体本波新注册：`DCE.M0`（`entities.id=300991`）、`DCE.C0`（`300992`），entity_type=`contract`。
- **DCE 缺测纪律**：新浪返回的 `settle=0` 是「当日未回传」哨兵值，抽取时整行跳过（3,170 行），
  绝不写成 0 元/吨——这是价格序列，0 会被下游当成真实暴跌。
- **IMF 单位校准**：`DATA_TRANSFORMATION=INDEX`（2016 年均值恰为 100），单位是**指数**不是美元/吨；
  命名门已把名字改成「价格指数」，避免被当成报价使用。

### 2.4 美国牛肉成本链（4 条，本次新增，`source_used = usda-quickstats`）

| # | 概念码 | 源侧序列 `short_desc` | 粒度 | 真实窗口（期数） | 单位 | 状态 |
|---|---|---|---|---|---|---|
| 26 | `agri.beef.cold_storage.total.monthly` | BEEF, COLD STORAGE, FROZEN - STOCKS | month | 2015-01 → 2026-09（141） | 磅 | verified |
| 27 | `agri.beef.cold_storage.bone_in.monthly` | BEEF, BONE-IN, COLD STORAGE, FROZEN - STOCKS | month | 2015-01 → 2026-09（141） | 磅 | verified |
| 28 | `agri.beef.cold_storage.boneless.monthly` | BEEF, BONELESS, COLD STORAGE, FROZEN - STOCKS | month | 2015-01 → 2026-09（141） | 磅 | verified |
| 29 | `agri.cattle.inventory.us.annual` | CATTLE, INCL CALVES - INVENTORY（`reference_period=FIRST OF JAN`, `source_desc=SURVEY`） | year | 2000 → 2026（26） | 头 | verified |

注：同 `short_desc` 下的 `END OF DEC` 版本属于 CENSUS 口径、同一年多行且分类维度未落 payload，
**整块 514 行不入库**（无法归到单一序列，硬并会造出假序列）。

## 3. 台股月营收（3 家标的，本波**未得** — 见 §5）

| 标的 | 代码 | 车道 | 目标序列 | 状态 |
|---|---|---|---|---|
| 王品 | 2742 | `twse-monthly-revenue` | 月度营收（千元新台币，当月快照） | **未得**（上游不可达） |
| 瓦城 | 2729 | 同上 | 同上 | **未得** |
| 美食-KY | 2723 | 同上 | 同上 | **未得** |

已入行的 `twse-monthly-revenue` 观测只有 2026-09 快照中的前 100 家（上游行序），三家均不在其中。

## 4. 波1 三新源（2.1–2.4）——序列 ID 与历史深度（侦察结论，待点亮）

| 源单元 | 源侧序列 ID | 粒度 | 可得历史 | 状态 |
|---|---|---|---|---|
| `ico-coffee-price` | `data.ico.org/api/v1/globe/icip` + `/history`，指标 `I-CIP` 等 5 组 | day | 匿名窗口 2026-01-02 → 2026-11-05（217 天 × 5 指标 = 1,085 行） | 单元+golden 就绪，待试跑点亮 |
| `restaurant-rpi` | restaurant.org `RPI-<Month>-<Year>.pdf` 第 3 页表，11 个字段 | month | 滚动 5 个月窗（2026-05 → 2026-09） | 同上 |
| `samr-food-inspection` | samr.gov.cn jPaas 公共发布接口 `spcjs` 栏目 | 公告（batch） | 回溯至 2019-02，count=228 | 同上 |

侦察原文：`data-source-scouting/restaurant/{ico-coffee-price,restaurant-rpi,samr-food-inspection}-scouting.md`。

## 5. 不可得与挂账（如实登记，不假装有数）

| 项 | 现象与证据 | 处置 |
|---|---|---|
| **台股三家月营收（任务 1.4）** | `openapi.twse.com.tw` 从本机与生产集群均连接级超时（`urllib.error.URLError: <urlopen error timed out>`，60–178s）；生产 run 4564（11-06）与 run 4787/4788（11-07）均 failed rows=0。`www.twse.com.tw` 同样 000，`mops.twse.com.tw` 可达但 `/nas/t21/...` 与 `ajax_t21sc03` 一律返回「FOR SECURITY REASONS」拦截页（含 cookie 预热 + Referer 实测）。 | 本波不勾 1.4；待上游恢复或改走 MOPS 会话化通道后单独立项，**不硬造三家数字** |
| 美国餐饮单项（MRTS `722`） | 现有单元只取 `44X72` 总计 | 后续波次扩展 census-eits 的 `category_code` 列表（单测已支持多 program 循环） |
| NBS easyquery 通道 | 本机直连 + 代理均 403（生产侧 740 条 nbs-stats payload 全是 CPI 行，零 easyquery 行） | 已改走 `dg` 公共发布接口，波0 无需处理 |
| BLS 直连 | `api.bls.gov`/`download.bls.gov` Akamai 403（本机+集群） | 已经 DBnomics 镜像（冻结 2025-01），caveat 已写入 §1.2 |
| `social_yoy` 社零同比 | 随抓未绑概念 | payload 留 crawl 库，待后续波次注册 |
| MOA 周报 `mom_pct`/`yoy_pct` | 同上 | 同上 |

## 6. 复现路径

```bash
cd /Users/chengsishi/finddata/fd-industry-data
source ~/.config/finddata/dashboards.env

# 1) 抽取（只增不改；重跑 INSERT 冲突即 0 行）
.venv/bin/python scripts/extract_restaurant_wave0.py --dry-run   # 空转看条数
.venv/bin/python scripts/extract_restaurant_wave0.py             # 正式写库

# 2) 命名门（六字段 + registry 镜像；psycopg3 在 protocol venv 里）
../fd-open-data-protocol/.venv/bin/python scripts/naming_gate_restaurant.py --apply
```

本波写入结果（2026-11-07）：观测库 11,375 行新增（+301 行 eurostat，实体注册后二次入账），
合计 11,676 行；18 个概念全部 `verified=true`，registry 镜像 18/18 置 verified
（`verified_by=rule:naming-gate`）。
