# gdacs —— GDACS 全球灾害事件列表

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-gdacs-8149dca4.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 事件检索 | `GET https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?fromDate=<YYYY-MM-DD>&toDate=<YYYY-MM-DD>` | 归档检索，GeoJSON FeatureCollection，多灾种，免 key |

入口：`run_gdacs(limit=100, from_date=None, to_date=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`，无新依赖。

## 端点勘误（2026-10-06 实测，README 留档）

工单 brief.source_urls 给的是 `geteventlist/MAP?fromDate=…&toDate=…`，实测 **MAP 变体完全
忽略时间窗**——返回的全是当前事件（2025/2026 `fromdate`），历史窗口拿不到归档。按 brief
「golden 锚固定历史期事件 id」口径改用 **SEARCH 变体**（`fromDate`/`toDate` 生效）：

- `SEARCH?fromDate=2020-01-01&toDate=2020-01-31` → 13 features（5 EQ / 1 TC / 1 VO / 5 DR
  等多灾种混合，含少量窗口外关联事件）；
- `SEARCH` 的 `eventtype` 参数是**空操作**（带不带返回同集），故不发——多灾种由响应直接给出。

## 参数白名单（只发实测过的参数）

- `fromDate` / `toDate`（YYYY-MM-DD，闭区间，客户端再按 `fromdate` 日期过滤）。
- 函数参数 `from_date` / `to_date` 缺省 = 近 30 天（截至今天 UTC，daily 节奏）；golden 显式
  传 `2020-01-01` / `2020-01-31` 固定窗。
- 不发 `eventtype`（SEARCH 上无效）、不用 MAP 变体（不吃时间窗）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `event_id` / `episode_id` | `properties.eventid / .episodeid` | GDACS 事件与集次 id |
| `type` | `properties.eventtype` | `EQ`/`TC`/`VO`/`DR`/`WF`/`FL` |
| `country` / `iso3` | `properties.country / .iso3` | 受影响国家（多国逗号连接）/ 主国 ISO3 |
| `alertlevel` / `alertscore` | 同名列 | `Green`/`Orange`/`Red`；score 缺失为 `null` |
| `name` | `properties.name` | 事件显示名（如 `Earthquake in Cuba`、`Eruption  Taal`） |
| `date` / `todate` | `properties.fromdate / .todate` | 上游 ISO 时间（无时区尾注），`date` 为事件开始 |
| `lat` / `lon` | `geometry.coordinates`（Point） | 质心经纬度；非 Point/缺失为 `null` |
| `event_source` / `sourceid` | `properties.source / .sourceid` | 上游监测机构与源事件号（如 `ALASKA EC` / `us60007l0r`） |
| `url` | `properties.url.report` | 事件报告页 |
| `source` / `scraped_at` | 常量 / 本地 | `gdacs`；UTC ISO8601 |

## 历史深度与稳定性（2026-10-06 实测）

- SEARCH 归档可回溯多年（实测窗内含 2018 年起报的跨期干旱）；2020-01 窗**窗内**去重后
  7 个事件（5 EQ、1 VO Taal 塔尔火山、1 TC TINO-20），alertlevel 全 `Orange`。
- 响应顺序实测两次全等，但检索排序不承诺稳定——golden 只锚**成员资格**（固定事件 id 出现）。
- SEARCH 会混入窗口外关联事件（如 2018/2019 起报的跨期干旱），客户端按窗过滤。

## 坑位与容错（brief.notes 逐条落实）

- **免 key**、无频次对抗痕迹；固定 UA，单窗单请求，超时 60 s 不重试。
- **失败即红**：取数失败 / 空特征集 / 过滤后 0 行 → 抛 `RuntimeError`，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

实测样例行（2020-01 固定窗）：

```
{"event_id": 1203961, "episode_id": 1293801, "type": "EQ", "country": "Cuba",
 "iso3": "CUB", "alertlevel": "Orange", "alertscore": 2.0, "name": "Earthquake in Cuba",
 "date": "2020-01-28T19:10:25", "todate": "2020-01-28T19:10:25",
 "lat": 19.421, "lon": -78.7627, "event_source": "NEIC", "sourceid": "",
 "url": "https://www.gdacs.org/report.aspx?eventid=1203961&episodeid=1293801&eventtype=EQ",
 "source": "gdacs", ...}
```

## 自检

```bash
python3 spiders/gdacs/spider.py          # 冒烟（近 30 天窗），打印 3 行
python3 scripts/check_manifest_commands.py
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-gdacs-8149dca4.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-gdacs.json`（`params: {limit: 15, from_date: 2020-01-01, to_date: 2020-01-31}`）
只锚**固定历史期事件 id 集合**：`event_id ∈ {1203961（古巴地震 EQ）, 273070（塔尔火山喷发 VO）,
1000646（气旋 TINO-20 TC）}`、`type == EQ`、`alertlevel == Orange`、`source == gdacs`，
以及 `min_rows: 5`（实测窗内 7 行，留余量）。**绝不锚**当期事件、顺序与抓取时间；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
