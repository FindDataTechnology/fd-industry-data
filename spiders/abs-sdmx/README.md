# abs-sdmx —— 澳大利亚统计局 SDMX（BA_GCCSA 建筑许可，月度）

新源接入档案（`templates/new-source/` 范式，范本 `spiders/nmc-weather/`）。工单：
`reports/health-tickets/20261006-abs-sdmx-05cdc4e2.yaml`（`kind=generate`，brief 侦察簿 W2-5）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 数据 | `GET https://data.api.abs.gov.au/rest/data/BA_GCCSA/{key}?format=csv` | 官方 SDMX REST，免鉴权，平面 CSV |
| 结构（排障用） | `GET /rest/dataflow/ABS/all`、`/rest/dataflow/ABS/BA_GCCSA?references=all`（`Accept: application/vnd.sdmx.structure+json`） | 数据流/维度/码表 |

入口：`run_abs_sdmx(limit=100, key=<SDMX key>) -> list[dict]`（`spider.py`）。
标准库 `urllib` + `csv`，无新依赖。

## 鉴权与参数白名单

- 无 key；带常规浏览器 UA。
- `key` 形态白名单：点分维度过滤串（`MEASURE.VALUE.SECTOR.WORK_TYPE.BUILDING_TYPE.TSEST.REGION.FREQ`，
  DSD 实测维度序）；默认 `1.1.9.TOT.100.10.1GSYD.M` ＝ 住宅审批套数 / 总价值段 / 全部门 /
  全部工程 / 住宅合计 / 原始值 / **大悉尼（1GSYD）** / 月度。
- **勿用 `all` 维度全量**：`/all?format=csv` 实测 360 MB+，必须 key 过滤取窄切片
  （默认切片约 20 KB、302 行）。

## 字段口径（工单 brief：period/region/value）

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `TIME_PERIOD` | `YYYY-MM`（月度） |
| `region` | `REGION` | GCCSA 码（`1GSYD` = Greater Sydney 等） |
| `measure` | `MEASURE` | `1` = Number of dwelling units（码表 CL_BA_MEASURE 实测） |
| `unit` | `UNIT_MEASURE` | `NUM` |
| `value` | `OBS_VALUE` | float；空串缺测 → `None` |
| `url` / `source` / `scraped_at` | — | 行级可追溯 / `abs-sdmx` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **RPPI 勿选**：brief 点名住宅价格指数数据流 RPPI 2021Q4 已停更——本单元只用
  **BA_GCCSA**（Building Approvals by GCCSA and above，建筑许可，月度，现行至 2026-08）。
- **历史月值固定**：切片行序 = TIME_PERIOD 升序（2001-07 起），2001-07 = 2513 套，
  原始值（TSEST=10）不回改 → golden 锚固定 region 固定月值；当期月份绝不锚。
- **失败即红**：取数失败 / 负载非 CSV / 解析 0 行 → `RuntimeError`，不静默返回空列表。
- **无 schedule**：manifest 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度

2001-07 起，月度，现行至 2026-08（2026-10-06 实测）。

## 实测样例行（2026-10-06 GET 证据）

```
GET https://data.api.abs.gov.au/rest/data/BA_GCCSA/1.1.9.TOT.100.10.1GSYD.M?format=csv
→ DATAFLOW,MEASURE,...,REGION,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE,...
  ABS:BA_GCCSA(1.0.0),1,...,1GSYD,M,2001-07,2513,NUM（HTTP 200，302 行，约 20 KB）
```

## 自检

```bash
python3 spiders/abs-sdmx/spider.py          # 单页冒烟，打印前 3 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-abs-sdmx-05cdc4e2.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-abs-sdmx.json` 用 `params={"limit":3}`（切片头三行恒定：2001-07/08/09），
锚**固定 region 固定月值**：`region==1GSYD && period==2001-07 && value==2513.0 &&
measure==1`。**绝不锚** 当期月份与 `url`；`scraped_at`/`timestamp` 进 `whitelist_fields`。
