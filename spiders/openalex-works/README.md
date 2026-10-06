# openalex-works —— OpenAlex 全球科研产出月度活跃度

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261006-openalex-works-b3312205.yaml`（`kind=generate`，
brief 侦察簿 W1-F）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 月度计数 | `GET https://api.openalex.org/works?filter=from_publication_date:<月初>,to_publication_date:<月末>&per-page=1&mailto=…` | 公开 REST，免 key，`meta.count` 即该月全球 works 总量 |

入口：`run_openalex_works(limit=100) -> list[dict]`（`spider.py`）。仅用标准库
`urllib`，无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 序列轴 | 日历月 `YYYY-MM`，自固定锚月 **2020-01**（brief 指定）按月推进 |
| `works_count` | `meta.count` | 该月发布（`publication_date` 落于月初..月末）的全球 works 总量 |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `openalex-works` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes / 实测踩坑逐条落实）

- **工单示例区间语法实测 400**：`filter=publication_date:2020-01-01..2020-01-31`
  上游报 "invalid date"。已改官方区间口径 `from_publication_date` + `to_publication_date`，
  并做了口径校验：2020-01-01..07 的区间 count（3068855）与逐日精确 count 求和完全一致，
  无「年精度注水」问题。
- **polite pool**：请求带 `mailto` 参数（上游建议，RFC 2606 保留域占位邮箱），UA 同步标注；
  限速约 10 req/s → 串行低频、请求间 sleep 0.12s，不并发、不重试放大。
- **只要计数**：`per-page=1`，目标载荷只有 `meta.count`，请求最轻。
- **`limit` 语义**：返回月份数，序列自固定锚月 2020-01 推进到最近**已完结月**（UTC，
  当月不计）。`limit=1` 时站点/URL/锚月恒定 → golden 重放确定；取最新月份需调大
  `limit`（默认 100 覆盖锚月起约 8 年余量）。
- **失败即红**：单月失败只跳过并留痕；全部月份失败抛 `RuntimeError`，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/openalex-works/spider.py          # 单月冒烟，打印 1 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-openalex-works-b3312205.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-openalex-works.json` 只锚**常量标签与确定性字段**：`month == 2020-01`
（固定历史锚月）、`source == openalex-works`、`url`（固定参数构造，逐字符确定），
以及 `min_rows: 1`。
**绝不锚** `works_count`（当期数值/计数）与任何当月数据；`scraped_at`/`timestamp` 进
`whitelist_fields`。

## 实测样例行（2026-10-06 冒烟）

```json
{"month": "2020-01", "works_count": 3470605,
 "url": "https://api.openalex.org/works?filter=from_publication_date%3A2020-01-01%2Cto_publication_date%3A2020-01-31&per-page=1&mailto=research%40finddata.example",
 "source": "openalex-works", "scraped_at": "2026-10-06T21:03:08+00:00"}
```
