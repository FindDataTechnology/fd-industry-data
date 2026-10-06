# unido-sdmx —— UNIDO 工业生产指数（IIP 月度）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-unido-sdmx-96c2142b.yaml`
（`kind=generate`，brief 侦察簿 W2-12 翻案）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 数据 | `GET https://stat.unido.org/portal/sdmx/data/UNIDO/IIP/1.0/{COUNTRY}.{INDICATOR}.{CLASSIFICATION}` | SDMX-JSON v2 数据，免鉴权，直连无需代理 |
| 结构 | `GET https://stat.unido.org/portal/sdmx/datastructure/UNIDO/IIP_STRUCTURE/1.0` | 维度枚举（国码/分类代码→名称），仅用于翻译名称 |
| 数据流目录 | `GET https://stat.unido.org/portal/sdmx/dataflow/UNIDO/all/all` | 10 个数据流；本单元用 `IIP`（Indices of Industrial Production） |

入口：`run_unido_sdmx(limit=100) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`，无新依赖。

工单 brief 里的 `source_urls`（`/portal/dataset/getDataset`）是**门户前端路径**，直连 404；
真实数据面是上表 `/portal/sdmx/data/...`（经 actuator mappings + OpenAPI v3 佐证，2026-10-06 实测）。

## 参数白名单（只发实测过的参数）

- 请求头：`Accept: application/vnd.sdmx.data+json;version=2.0.0`（数据）/
  `application/vnd.sdmx.structure+json;version=2.0.0`（结构）——**不带即 406
  （`{"message":"No acceptable representation","code":"GEN-003"}`）**。
- 国码：ISO 数字码（`156` = 中国）。key 维度序 `COUNTRY.INDICATOR.CLASSIFICATION`，
  种子键实测为 `156.52.C`（52 = Original index，C = Total manufacturing）。
- `startPeriod`/`endPeriod` 可用但格式必须是 `YYYY-MXX`（如 `2020-M01`；`2020-01` 返回
  400）。本单元**默认不发**这两个参数（全史一次取回后本地按 `YYYY MXX` 月度过滤），规避格式坑。
- 未实测的维度组合（其他国家、季调指数 53、细分行业 10-33/B/D/E）不进种子，仅 README 留档。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `country` | SDMX key 第 1 维 | ISO 数字码（`156` = 中国） |
| `country_name` | 结构枚举 `COUNTRY` | 国家名（结构取不到时为 `null`，不阻断取数） |
| `indicator` | SDMX key 第 2 维 | `52` = Original index（`53` = Seasonally adjusted） |
| `classification` | SDMX key 第 3 维 | ISIC 代码，种子 `C` = Total manufacturing |
| `classification_name` | 结构枚举 `CLASSIFICATION` | 分类名（同上可降级为 `null`） |
| `period` | `observations` 键 | 月度期次 `YYYY MXX`；年度（`2005`）/季度（`2026 Q1`）期次被过滤 |
| `value` | `observations` 值 | 原指数值（全精度浮点透出，不四舍五入） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `unido-sdmx` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 历史深度（2026-10-06 实测）

`156.52.C` 全史 350 个观测：月度自 **2006 M01** 起（26.89…）到 **2026 M07**（139.35…），
另混有 2005-2006 年度值与 2026 季度值（均被月度过滤剔除）。

实测样例行（`limit=3`）：

```
{"country": "156", "country_name": "China", "indicator": "52", "classification": "C",
 "classification_name": "Total manufacturing", "period": "2006 M01",
 "value": 26.8926011853938, "url": ".../sdmx/data/UNIDO/IIP/1.0/156.52.C",
 "source": "unido-sdmx", "scraped_at": "2026-10-06T21:…+00:00"}
```

## 坑位与容错（brief.notes 逐条落实）

- **Accept 必须是 SDMX v2 JSON**：否则 406 `GEN-003`（见上）。
- **紧凑 SDMX-JSON**：`dataSets[*].series[key].observations` 是 `{期次: [[值]]}`，
  `structure.dimensions` 只给维度名不给值数组——期次字符串即键，直接可用。
- **混频期次**：月度源只保留 `^\d{4} M\d{2}$` 形态，年度/季度值不透出。
- **`limit` 语义**：按种子国顺序取数、期内 period 升序截断到 `limit`；`limit` 固定则行集
  确定 → golden 重放可复现。
- **失败即红**：单国失败只跳过并在异常信息里留痕；**全部国家失败抛 `RuntimeError`**，
  不静默返回空列表。结构（名称表）失败降级为纯代码行。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/unido-sdmx/spider.py          # 单页冒烟，打印 3 行月度 IIP
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-unido-sdmx-96c2142b.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-unido-sdmx.json`（`params: {limit: 5}`）只锚**固定历史期**：`country == 156`、
`period == 2006 M01`、`value == 26.8926011853938`（2006 年月度历史值，早已定版）、
`source == unido-sdmx`、`url == …/IIP/1.0/156.52.C`，以及 `min_rows: 5`。
**绝不锚**当期/近期值与抓取时间；`scraped_at`/`timestamp` 进 `whitelist_fields`。
