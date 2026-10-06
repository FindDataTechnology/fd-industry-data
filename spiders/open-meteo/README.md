# open-meteo —— Open-Meteo 历史气象再分析日值

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-open-meteo-ee751664.yaml`
（`kind=generate`，批次二，侦察簿 W1-C）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 历史日值 | `GET https://archive-api.open-meteo.com/v1/archive?latitude=<lat>&longitude=<lon>&start_date=<d>&end_date=<d>&daily=temperature_2m_mean,precipitation_sum` | 公开 REST，**免 key**，返回 JSON |

入口：`run_open_meteo(limit=100, city=None, start_date=None, end_date=None) -> list[dict]`
（`spider.py`）。仅用标准库 `urllib`，无新依赖。`air-quality-api` 同构可扩（本次不接）。

## 参数白名单（只发实测过的参数）

`latitude` / `longitude` / `start_date` / `end_date` / `daily` —— 其余一律不发。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `city` | 城市常量表 | 种子 `beijing`（工单实测坐标 39.9/116.4）在前，其余固定顺序：shanghai/guangzhou/shenzhen/chengdu/hangzhou/wuhan/xian |
| `latitude` / `longitude` | 城市常量表 | 请求坐标 |
| `date` | `daily.time[i]` | 观测日 ISO8601 |
| `temperature_2m_mean` | `daily.temperature_2m_mean[i]` | 日均 2m 气温 ℃（ERA5 再分析） |
| `precipitation_sum` | `daily.precipitation_sum[i]` | 日降水合计 mm |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `open-meteo` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 历史深度与窗口语义

- archive 数据 **1940 年起**（侦察簿实测 1950 上海 365 天满量）；再分析值一经发布即冻结，
  固定历史区间的数值跨期可复现。
- 缺省窗口 = 截至昨天（UTC）的近 31 天滚动窗口（与 `frequency: daily` 对齐）；
  `start_date/end_date` 可钉死任意历史区间（golden 用此机制锚 2020-01 固定值）。

## 坑位与容错（brief.notes 逐条落实）

- **免 key、无反爬**：不带任何鉴权与频次对抗；固定 UA，超时即跳过该城市，不重试不加频次。
- **`limit` 语义**：城市外层、日期内层顺序展开；`limit` 小于单城天数时只打种子城市 → 行序确定。
- **失败即红**：单城市失败只跳过并留痕；**全部城市失败抛 `RuntimeError`**，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/open-meteo/spider.py          # 冒烟，打印 3 行逐日值
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-open-meteo-ee751664.yaml
```

## 实测样例行（2026-10-06，golden 钉窗口重放）

```json
{"city": "beijing", "latitude": 39.9, "longitude": 116.4, "date": "2020-01-01",
 "temperature_2m_mean": -5.8, "precipitation_sum": 0.0,
 "url": "https://archive-api.open-meteo.com/v1/archive?latitude=39.9&longitude=116.4&start_date=2020-01-01&end_date=2020-01-05&daily=temperature_2m_mean%2Cprecipitation_sum",
 "source": "open-meteo", "scraped_at": "…"}
```

## golden 断言口径（跨期稳定）

`golden/001-open-meteo.json` 用 `params` 钉死种子城市 + 2020-01-01..05 固定历史区间，
锚**冻结再分析值**：`temperature_2m_mean == -5.8`（2020-01-01 北京）、`precipitation_sum == 0.0`、
`date == 2020-01-01`、`city == beijing`、`min_rows: 5`。
**绝不锚** 滚动窗口数值、`scraped_at`/`timestamp`（在 `whitelist_fields`）。
