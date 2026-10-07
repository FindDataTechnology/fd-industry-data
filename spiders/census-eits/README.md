# census-eits —— 美国零售/批发月度销售与库存（Census EITS：mrts/mwts）

新源接入档案。工单：`reports/health-tickets/20261007-census-eits-0a67b68f.yaml`
（`kind=generate`，批次二 Wave B 直建）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 零售（MARTS） | `GET https://api.census.gov/data/timeseries/eits/mrts?get=cell_value,data_type_code,time_slot_id&for=us:*&time=2020-01&category_code=44X72&seasonally_adj=no&key=<CENSUS_API_KEY>` | 月度零售贸易与餐饮服务总计，`category_code=44X72` |
| 批发（MWTS） | `GET https://api.census.gov/data/timeseries/eits/mwts?get=cell_value,data_type_code,time_slot_id&for=us:*&time=2020-01&category_code=42&seasonally_adj=no&key=<CENSUS_API_KEY>` | 商户批发总计，`category_code=42`（旧名 mots/wts 不存在勿用） |

返回二维数组（首行表头 `cell_value/data_type_code/time_slot_id/time/category_code/
seasonally_adj/us`）。入口：`run_census_eits(limit=100) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib/json/os`，无新依赖。**key 从环境 `CENSUS_API_KEY` 读取，
`key=` 查询参数传递，绝不硬编码/落文件。**

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `program` | 常量 | `mrts`（零售）/ `mwts`（批发） |
| `time` | 表头 `time` | `2020-01`（固定历史月） |
| `category_code` | 表头回显 | `44X72` 零售总计 / `42` 批发总计 |
| `data_type_code` | 表头 | `SM`=销售额、`IM`=库存额、`IR`=库销比、`MPC*`=环比 %、`E_*`=抽样误差 |
| `seasonally_adj` | 表头回显 | 固定 `no`（未季调原值） |
| `cell_value` | 表头 | 数值化 float（百万美元 / 百分比）；脏值原样字符串透出 |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级可追溯（**已剔除 `key=` 参数，key 不落盘**）/ `census-eits` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **必填谓词 `category_code` + `seasonally_adj`**：缺任一上游即
  `error: missing required variable/predicate: ...`（HTTP 400，实测）。
- **无 key/超限坑**：上游 302 → HTML "Missing Key" 错误页（HTTP 200 形态）——
  响应体首字符必须为 `[`，否则显式报错，**绝不允许静默降级为无 key 通道裸跑**。
- **golden 锚固定 category_code 固定历史月值**（构建时 2026-10-07 实况冻结）：
  `mrts/44X72/SM/2020-01 == 472427`、`mwts/42/SM/2020-01 == 488638`、
  `mwts/42/IM/2020-01 == 684010`。若上游年度基准修订触及 2020-01，需人工复锚。
- 两 program 各仅一次请求，无"部分成功"口径：任一 program 拉取失败 → 整体
  `RuntimeError`（失败即红）；零可用行 → `RuntimeError`。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗；`www.census.gov` 有
  Cloudflare——适配器只碰 `api.census.gov`（干净）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env
python3 spiders/census-eits/spider.py          # 冒烟，打印前 3 行
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-census-eits-0a67b68f.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-census-eits.json`：`params={"limit":20}` → 2020-01 未季调零售
（4 行）+ 批发（10 行）共 14 行。锚 `time == 2020-01`、`seasonally_adj == no`、
`category_code ∈ {44X72, 42}`、`data_type_code ∈ {SM, IM}`、
`cell_value == 472427.0 / 488638.0 / 684010.0`（实测定稿值）、
`source == census-eits`，`min_rows: 14`。**绝不锚**当期/近月未定稿值；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
