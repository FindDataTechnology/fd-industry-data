# noaa-ncei —— NOAA NCEI GHCN-Daily 全球气象站日值

新源接入档案。工单：`reports/health-tickets/20261006-noaa-ncei-dfcec3de.yaml`
（`kind=generate`，brief 侦察簿 W1-J）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 站点日值 | `GET https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries&stations=<站码>&startDate=<YYYY-MM-DD>&endDate=<YYYY-MM-DD>&dataTypes=TMAX,TMIN,PRCP&format=json` | 公开 REST，**免 key**，返回 JSON 数组 |

入口：`run_noaa_ncei(limit=100, station=None, start_date=None, end_date=None) -> list[dict]`
（`spider.py`）。仅用标准库 `urllib`，无新依赖。

## 参数白名单（只发实测过的参数）

`dataset=daily-summaries`、`stations`（GHCN 站码，可逗号分隔）、`startDate`、`endDate`、
`dataTypes=TMAX,TMIN,PRCP`、`format=json`。不发 `units`/`includeAttributes` 等未实测参数。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `station` | `STATION` | GHCN 站码（种子 `USW00023174` = Los Angeles Downtown USC） |
| `date` | `DATE` | 观测日 `YYYY-MM-DD` |
| `tmax` / `tmin` | `TMAX` / `TMIN` | **GHCN 原生 0.1 ℃ 计整数字符串（含空白填充），已换算为 ℃**（`"  200"` → `20.0`） |
| `prcp` | `PRCP` | **GHCN 原生 0.1 mm 计整数字符串，已换算为 mm**（`"  414"` → `41.4`） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `noaa-ncei` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实）

- **免 key、免 UA**：公开数据服务，无鉴权；本单元不做指纹伪装与频次对抗，遇风控升级按
  协议转人工（换数据表面），不逆向。
- **值是带空白填充的字符串**（`"  194"`、`"    0"`）：`_num` 先 strip 再转 float；缺测
  （缺键/空串/非数值）→ `None`，绝不硬造 0。
- **`limit` 语义**：行数上限，日期升序后保留**最近** `limit` 行（生产取最新日值）；
  `start_date`/`end_date` 缺省 = 昨日为终点的最近 31 天（日值入库滞后留裕量）。
  golden 重放传固定历史窗口（2020 全年）+ 小 `limit` → 重放结果恒定。
- **失败即红**：单站失败只跳过并留痕；**全部站点失败抛 `RuntimeError`**，不静默返回
  空列表（避免「空洞通过」）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度与节奏

GHCN-Daily 部分站点自 1763 年起（百万站·日级存档）；本单元种子站按工单锚 2020 年窗口，
生产缺省窗口 31 天滚动。节奏 `daily`。

## 实测样例行与证据（2026-10-07 抓取）

```json
{"DATE":"2020-12-31","STATION":"USW00023174","TMAX":"  200","TMIN":"   61","PRCP":"    0"}
```
→ `{"station": "USW00023174", "date": "2020-12-31", "tmax": 20.0, "tmin": 6.1, "prcp": 0.0, ...}`

2020 全年窗口返回 366 行（闰年完整）。

## 自检

```bash
python3 spiders/noaa-ncei/spider.py          # 冒烟，打印最近 3 行日值
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-noaa-ncei-dfcec3de.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-noaa-ncei.json` 锚**固定站点固定历史期**：`date == 2020-12-31` 及其
`tmax == 20.0`、`tmin == 6.1`、`prcp == 0.0`（GHCN 已定档历史值，跨期不变），外加常量
标签 `station` / `source` 与 `min_rows: 5`。**绝不锚**当期/滚动窗口日期；`scraped_at`/
`timestamp` 进 `whitelist_fields`。
