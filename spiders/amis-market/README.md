# amis-market —— AMIS 粮农市场库（G20 商品供需平衡表）

> **⚠️ 需代理出口**：数据端点在 `amis-9189b.appspot.com`（Google App Engine），
> **被墙**。测试/本地必须 `export HTTPS_PROXY=http://<proxy>:<port>`（urllib 缺省
> 尊重该环境变量，本单元不硬编码代理地址）；**集群 runner 代理注入就绪前不点亮**
> ——manifest 刻意不写 `site` 与 `schedule`，静默合入。

批次二 Wave C 直建（机器重启恢复，无工单，入口在 PR body 手工留证）。
侦察簿：`data-source-scouting/INDUSTRY-DATA-SOURCES.md`「★接」行
（`POST amis-9189b.appspot.com/fetch {"query":"<SQL>"}` 无鉴权直查 BigQuery）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| SQL 取数 | `POST https://amis-9189b.appspot.com/fetch`，body `{"query": "<SQL>"}` | 无鉴权、**免 key** 直查 BigQuery；成功回 JSON 行数组，SQL 错误回 HTTP 400 + `{"detail": "<BigQuery 报错原文>"}` |

- 与 AMIS 门户前端（`app.amis-outlook.org`，Angular main bundle 里的
  `environment.BIGQUERY_ENDPOINT` / `BIGQUERY_DATASET = "fao-maps"`）同款调用。
- 数据表在跨项目三段名 `fao-maps.fao_amis.amis_*`；核心事实表 `amis_data`
  （CBS/IGC/PSD 三库），维度表 `amis_product` / `amis_country` / `amis_element`。

入口：`run_amis_market(limit=100, product=None, element=None, region=None) -> list[dict]`。
仅用标准库 `urllib` + `json`，无新依赖。

## 查询面口径

- 库 = `CBS`（Country Balance Sheets，国家平衡表）——AMIS 供需面主库
  （IGC=国际谷物理事会、PSD=USDA 种植面积库，本单元不取）。
- 商品 6 个口径：`Wheat(1)` / `Rice(4)` / `Maize(5)` / `Soybean(6)` +
  聚合组 `COARSE GRAINS(7)` / `TOTAL CEREALS(8)`。
  **侦察簿偏差**：侦察簿称「G20 八商品」，实测 CBS 供需面（`product_code<4000`
  且有数据的）共 6 个商品口径（含两个聚合组；4000+ 为化肥品目，属独立化肥库），
  本单元取全量 6 个。
- 要素 6 个（平衡表主干）：`Production(5)` / `Imports (NMY)(7)` /
  `Exports (NMY)(10)` / `Closing Stocks(16)` / `Domestic Utilization(20)` /
  `Total Utilization(35)`。
- 地区 = 全部国家 + `World(999000)`；营销年 2000-2026（`year`=起始年，
  `season` 形如 `2017/18`）。
- 实测面大小：**25,450 行**（单查询 ~17s）。行按
  (product, element, region, year) 升序，返回最近 `limit` 行。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `product` / `product_code` | `amis_product.product_name/product_code` | 商品名 + 码 |
| `region` / `region_code` | `amis_country.region_name/region_code` | 国家名 + 码（World=999000） |
| `element` / `element_code` | `amis_element.element_name/element_code` | 平衡要素名 + 码 |
| `units` | `amis_data.units` | `Million tonnes` 等 |
| `year` / `season` | `amis_data.year/season` | 营销年（起始年）+ 季标签 |
| `value` | `amis_data.value` | **round 6 位**归一；只取最新修订（见坑位） |
| `last_update` | `amis_data.last_update` | 上游修订时间戳 |
| `url` | 常量 | POST 端点，行级可追溯 |
| `source` | 常量 | `amis-market` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（全部实测）

- **appspot 域被墙**：无代理出口时连接直接失败（本机直连 TCP 不通）。
  单元用 urllib 缺省代理行为（环境变量），不硬编码代理地址。
- **服务端 50000 行硬上限且静默截尾**：不加修订过滤的同条件查询实测恰好
  返回 50000 行（尾部被切、无任何报错标记）。本单元固定查询面 +
  `max_lastupdate=1` 过滤后 ~2.5 万行在帽内；**若仍命中帽（`len >= 50000`）
  视为取数不可靠，直接抛 `RuntimeError` 拒绝截片**。
- **同键多修订行**：`amis_data` 同一 (product, region, element, year) 自然键
  有多行历史修订（`last_update` 不同）。SQL 固定带 `max_lastupdate = 1`
  只取最新修订（实测 50000 行含修订 → 过滤后自然键唯一）。
- **三段名必须整体 backtick**：项目段 `fao-maps` 含连字符，写成
  `fao-maps.fao_amis.amis_data`（无 backtick）会报
  `Syntax error: Expected end of input but got "-"`；必须写
  `` `fao-maps.fao_amis.amis_data` ``（整体反引号，与门户前端一致）。
  另：`region-us.INFORMATION_SCHEMA.SCHEMATA` 可列出默认项目数据集（探表用）。
- **wrapper 会剥列名 backtick 之外无副作用**；`database`/`year` 等列名不加
  backtick 直接可用。
- 上游行 SQL 显式排序后再截 `limit`；`value` round 6 位归一。
- 空响应/非数组/零行/帽命中/解析失败一律 `RuntimeError`（失败即红，不静默
  返回空列表）。单查询无重试、无频次放大。
- 免 key、无鉴权；未见风控（浏览器/Python UA 均通，本单元未带特殊 UA）。
  如遇风控升级按协议转人工，不逆向。

## 历史深度与节奏

2000-2026 营销年（26 期 × 6 商品 × 6 要素 × 28 地区）。上游月度滚动修订
（`last_update` 实测每月更新一次，近期营销年为预估值、持续修订）。

## 实测样例行与证据（2026-10-07 经代理抓取）

```
product=Wheat, region=World, element=Production, year=2017, season=2017/18,
value=760.318808, units=Million tonnes, last_update=2026-10-02T00:00:00+00:00
```

## 自检（均需代理出口）

```bash
python3 spiders/amis-market/spider.py          # 冒烟，打印最近 3 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/validate_manifests.py          # v2 调度契约校验
python3 scripts/conformance_gate.py --roots .  # 布局门
```

## golden 断言口径（跨期稳定）

`golden/001-amis-market.json` 用 `product/element/region` 三参窄化到
「Wheat × Production × World」单序列（27 行），**锚深历史冻结值**：
`year == 2017`（营销年 2017/18）及其 `value == 760.318808`，外加常量
`product` / `element` / `region` / `season` / `source` 与 `min_rows: 25`。
**绝不锚**：近期营销年（2024+ 为预估值、每月修订）、`last_update`
（滚动变化，进 `whitelist_fields`）。若上游对 2017 深历史重算导致锚红，
属上游口径变更，按 sample-update-pending-human 走人工复锚，不静默放水。
