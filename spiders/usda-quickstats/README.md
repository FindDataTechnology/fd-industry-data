# usda-quickstats —— USDA NASS Quick Stats 农情调查（keyed 源）

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261007-usda-quickstats-6fa3d4d0.yaml`（`kind=generate`，
批次二 Wave B 直建）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 取数（唯一端点） | `GET https://quickstats.nass.usda.gov/api/api_GET/?key=<QUICKSTATS_API_KEY>&commodity_desc=CORN&year=2020&agg_level_desc=NATIONAL&statisticcat_desc=AREA%20PLANTED&format=JSON` | 官方 Quick Stats GET API，返回 `{"data":[...]}`；上表 URL 即本单元**默认口径**（去掉 key 后逐字符冻结进 golden） |

入口：`run_usda_quickstats(limit=100, commodity="CORN", years="2020", agg_level="NATIONAL",
statisticcat="AREA PLANTED", state=None, reference_period=None, unit=None) -> list[dict]`
（`spider.py`）。仅用标准库 `urllib`/`json`/`re`，无新依赖。

**密钥纪律**：key 只从 `os.environ["QUICKSTATS_API_KEY"]` 读（集群由 secret
`fd-industry-source-keys` 经 `envFrom` 注入，见 `k8s/cronjob-template/templates/cronjob.yaml`）；
缺 key 立即抛 `RuntimeError`。行内 `url` 列**剥离 key**，只保留真实查询参数——
密钥不进任何产出行、日志或 golden 文件。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `commodity` | `commodity_desc` | 商品（默认 `CORN`） |
| `year` | `year` | 调查年度（JSON 里为 int） |
| `state` / `state_alpha` | `state_name` / `state_alpha` | 州；全国聚合行为 `US TOTAL` / `US` |
| `agg_level` | `agg_level_desc` | 聚合层级（`NATIONAL` / `STATE` / `COUNTY`…） |
| `statisticcat` | `statisticcat_desc` | 统计范畴（默认 `AREA PLANTED`） |
| `unit` | `unit_desc` | 计量单位（`ACRES` / `PCT BY TYPE`…） |
| `value` | `Value` | 去掉千分位后转数值；屏蔽符 `(D)/(Z)/(S)/(NA)/(X)` → `None` |
| `value_raw` | `Value` | 上游原文（如 `"90,432,000"`），屏蔽符原样保留 |
| `reference_period` | `reference_period_desc` | 参考期（`YEAR` 为年度定值；`YEAR - JUN ACREAGE` 等为过程稿） |
| `short_desc` | `short_desc` | 完整序列描述 |
| `source_desc` | `source_desc` | 项目来源（`SURVEY` / `CENSUS`） |
| `load_time` | `load_time` | NASS 库装入时间 |
| `url` | 请求 URL | 行级可追溯，**无 key** |
| `source` | 常量 | `usda-quickstats` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（工单 brief.notes / 实测逐条落实）

- **缺 key / 无效 key**：实测 HTTP 401 + `{"error":["unauthorized"]}`（工单另记
  纯文本 `unauthorized` 口径）。响应体首字符非 `{`/`[` 一律报错；JSON 里出现
  `error` 键同样报错——错误页绝不当下游数据。实测 `key=BADKEY123` → 401。
- **单次 ≤ 50,000 行**：单分片返回行数 ≥ 50000 视为被截断（数据必丢），该分片按
  失败处理（跳过留痕），不做静默截断；大查询用 `years`（逐年）与 `state`（逐州）
  分片。
- **逗号年份不可用（实测坑）**：`year=2019,2020` 返 HTTP 500 HTML 错误页——
  分片只能在客户端逐年发请求；本单元 `years` 每个年份一个请求（实测 `2019-2020`
  → 25 行，两次请求间隔 0.5s）。
- **失败即红**：单分片失败只跳过并向 stderr 留痕；**全部分片失败抛
  `RuntimeError`**，不静默返回空列表（避免"空洞通过"）。
- **节奏**：无公示频控，保守串行（请求间隔 0.5s）、不并发、不重试放大。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`（Wave B 直建统一静默合入，
  点亮留人工门）。

## 自检

```bash
python3 spiders/usda-quickstats/spider.py      # 冒烟，打印默认口径 3 行
python3 scripts/validate_manifests.py           # 双 lint 之一
python3 scripts/check_manifest_commands.py      # 双 lint 之二（命令漂移）
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-usda-quickstats-6fa3d4d0.yaml
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-usda-quickstats.json`（`params={"limit":20}`）只锚**默认口径**下的
确定性与历史定值：

- 结构与常量：`commodity=CORN`、`year=2020`、`agg_level=NATIONAL`、
  `statisticcat=AREA PLANTED`、`reference_period=YEAR`、`unit=ACRES`、
  `short_desc="CORN - ACRES PLANTED"`、`source=usda-quickstats`；
- **冻结定值**：2020 年度玉米播种面积 `value == 90432000`（`value_raw="90,432,000"`，
  NASS 2020 最终值，构建时实况冻结；`YEAR` 期排除 JUN/MAR/OCT 过程稿）；
- `url` 逐字符锚定默认查询（无 key）；
- `load_time` / `scraped_at` / `timestamp` 进 `whitelist_fields`。

`limit=20` 覆盖默认口径全部 12 行，锚定行必在返回集中且与行序无关。

## 实测样例行（2026-10-07 冒烟，实况冻结）

```json
{"commodity": "CORN", "year": 2020, "state": "US TOTAL", "state_alpha": "US",
 "agg_level": "NATIONAL", "statisticcat": "AREA PLANTED", "unit": "ACRES",
 "value": 90432000, "value_raw": "90,432,000", "reference_period": "YEAR",
 "short_desc": "CORN - ACRES PLANTED", "source_desc": "SURVEY",
 "load_time": "2024-01-09 15:00:00.000",
 "url": "https://quickstats.nass.usda.gov/api/api_GET/?commodity_desc=CORN&year=2020&agg_level_desc=NATIONAL&statisticcat_desc=AREA%20PLANTED&format=JSON",
 "source": "usda-quickstats", "scraped_at": "2026-10-07T11:18:59+00:00"}
```

默认口径实测 12 行（4 行 ACRES 参考期 + 8 行 PCT BY TYPE）；`2019-2020` 两分片
实测 25 行；`state=IA, agg_level=STATE` 实测 IA 州行正常。