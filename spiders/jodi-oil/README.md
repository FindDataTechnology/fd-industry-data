# jodi-oil —— JODI-Oil 月度石油供需（primary 流）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-jodi-oil-a587475b.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 年度全量 | `GET https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/{year}.csv` | **确定性 URL 模式**，免鉴权；SDMX 风格 CSV，约 11 MB / 28.3 万行/年 |

入口：`run_jodi_oil(limit=100, year=None) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`/`csv`，无新依赖。

## 参数白名单

只有 `year` 一个自由度（拼进确定性 URL）。实测可用区间 **2002..(当前年-1)**：
2001 与 2026（当前年）均 404，2002/2024/2025 均 200。`year` 缺省 = 上一个完整年度。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `year` / `month` | `TIME_PERIOD`（`2002-01`） | 拆为整数年 + 月字符串 |
| `country` | `REF_AREA` | 上游国别码（ISO two-letter，如 `AE`） |
| `flow` | `FLOW_BREAKDOWN` | 流代码原码：`INDPROD` 产量 / `TOTIMPSB` 进口 / `TOTEXPSB` 出口 / `REFINOBS` 炼厂输入 / `DIRECUSE` 直接使用 / `CLOSTLV` 期末库存 / `STOCKCH` 库存变化 / `STATDIFF` 统计差额 / `TRANSBAK` 转移 / `OSOURCES` 其它来源 |
| `product` | `ENERGY_PRODUCT` | `CRUDEOIL` / `NGL` / `OTHERCRUDE` / `TOTCRUDE` |
| `value` | `OBS_VALUE` | 数值；缺测占位行整行不透出（见坑位） |
| `unit` | `UNIT_MEASURE` | `CONVBBL` / `KBBL` / `KBD` / `KL` / `KTONS` |
| `assessment` | `ASSESSMENT_CODE` | 上游质量旗 1/2/3，原样透出（brief「含质量旗」） |
| `url` / `source` / `scraped_at` | 常量/本地 | 追溯与标识 |

`limit` = 返回行数上限：全量解析后按（month, country, product, flow, unit）字典序确定性排序再截取。

## 坑位与容错（brief.notes 逐条落实）

- **缺测占位四种**：`-` / `x` / `N/A` / `..`（2002 年实测占 26.3 万行）——一律视为缺测，
  **该行不透出**，`limit` 只数真实观测行，不透空洞行。
- **404 即 HTML 错误页**：按 `REF_AREA` 表头嗅探，非 CSV 载荷直接报错（失败即红）。
- **仅常规 UA**：免鉴权站点，不做指纹伪装、不做频次对抗。
- **失败即红**：单文件表面，404/HTTP 错误/解析失败/0 观测行 → 抛 `RuntimeError`，不静默回空。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 实测样例行（2026-10-07 冒烟，`year=2002` 排序前 3）

```json
{"year": 2002, "month": "01", "country": "AE", "flow": "CLOSTLV", "product": "CRUDEOIL",
 "unit": "CONVBBL", "value": 7596.0, "assessment": "3"}
{"year": 2002, "month": "01", "country": "AE", "flow": "INDPROD", "product": "CRUDEOIL",
 "unit": "KBD", "value": 1955.0, "assessment": "1"}
```

## 自检

```bash
python3 spiders/jodi-oil/spider.py          # 单页冒烟（上一个完整年度），打印 3 行观测
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-jodi-oil-a587475b.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-jodi-oil.json` 用**固定深历史年** `year=2002` + `limit=5` 重放，锚 `AE / 2002-01 /
CRUDEOIL / INDPROD / KBD / value=1955.0 / assessment=1` 等（23 年前数据，回填已收敛；每条规则
独立匹配任一行）。**绝不锚** 当年/上年数值；`scraped_at`/`timestamp` 进 `whitelist_fields`。
