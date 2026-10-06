# gleif-lei —— GLEIF 全球新设法人（LEI 注册）月度序列

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261006-gleif-lei-b66a12c1.yaml`（`kind=generate`，
brief 侦察簿 W1-F）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 月度计数 | `GET https://api.gleif.org/api/v1/lei-records?page[size]=1&filter[registration.initialRegistrationDate]=<月初>..<月末>` | 公开 REST，免 key，JSON:API，`meta.pagination.total` 即该月新注册 LEI 总量 |
| 单国拆分 | 同上 + `&filter[entity.legalAddress.country]=<ISO 码>` | 可选 `country` 参数 |

入口：`run_gleif_lei(limit=100, country=None) -> list[dict]`（`spider.py`）。仅用
标准库 `urllib`，无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 序列轴 | 日历月 `YYYY-MM`，自固定锚月 **2020-01**（brief 指定）按月推进 |
| `country` | `country` 参数 | 单国拆分行携带 ISO 3166-1 alpha-2 码；全局行为 `None` |
| `lei_count` | `meta.pagination.total` | 该月 `initialRegistrationDate` 落于月初..月末的 LEI 注册量 |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `gleif-lei` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes / 实测踩坑逐条落实）

- **工单示例嵌套过滤实测 400**：`filter[registration][initialRegistrationDate]`
  上游报 "Filter should contain only allowed values"；实测有效形态是**点号平铺**
  `filter[registration.initialRegistrationDate]=YYYY-MM-DD..YYYY-MM-DD`，
  `..` 区间语法 GLEIF 实测支持。本单元只发实测有效参数。
- **分页上限**：`page[size]` 上限 200；本单元只要计数，固定 `page[size]=1`。
- **节奏**：匿名公开 API，串行低频（请求间 sleep 0.25s）、不并发、不重试放大。
- **`limit` 语义**：返回月份数，序列自固定锚月 2020-01 推进到最近**已完结月**（UTC，
  当月不计）。`limit=1` 时站点/URL/锚月恒定 → golden 重放确定；取最新月份需调大
  `limit`（默认 100 覆盖锚月起约 8 年余量）。
- **可选拆分**：`country` 参数（ISO alpha-2，内部大写归一）追加
  `filter[entity.legalAddress.country]` 过滤；行内 `country` 列回显。逐国序列由平台
  按需调用，不在单次运行内枚举全部国家（避免请求放大）。
- **失败即红**：单月失败只跳过并留痕；全部月份失败抛 `RuntimeError`，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/gleif-lei/spider.py          # 单月冒烟，打印 1 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-gleif-lei-b66a12c1.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-gleif-lei.json` 只锚**常量标签与确定性字段**：`month == 2020-01`
（固定历史锚月）、`source == gleif-lei`、`url`（固定参数构造，逐字符确定），
以及 `min_rows: 1`。
**绝不锚** `lei_count`（计数，上游可能回溯补录/更正）与任何当月数据；
`scraped_at`/`timestamp` 进 `whitelist_fields`。

## 实测样例行（2026-10-06 冒烟）

```json
{"month": "2020-01", "country": null, "lei_count": 18357,
 "url": "https://api.gleif.org/api/v1/lei-records?page%5Bsize%5D=1&filter%5Bregistration.initialRegistrationDate%5D=2020-01-01..2020-01-31",
 "source": "gleif-lei", "scraped_at": "2026-10-06T21:04:44+00:00"}
```
