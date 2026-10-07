# census-m3 —— 美国制造商出货/库存/订单月度（Census EITS：M3）

新源接入档案。工单：`reports/health-tickets/20261007-census-m3-a7211ee6.yaml`
（`kind=generate`，批次二 Wave B 直建）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 制造业总计 | `GET https://api.census.gov/data/timeseries/eits/m3?get=cell_value,data_type_code,time_slot_id&for=us:*&time=2020-01&category_code=MTM&seasonally_adj=no&key=<CENSUS_API_KEY>` | Manufacturers' Shipments, Inventories, and Orders（1992 起），`category_code=MTM`（制造业总计），月度 |

返回二维数组（首行表头 `cell_value/data_type_code/time_slot_id/time/category_code/
seasonally_adj/us`）。入口：`run_census_m3(limit=100) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib/json/os`，无新依赖。**key 从环境 `CENSUS_API_KEY` 读取
（与 census-eits 共用同一把 Census key），`key=` 查询参数传递，绝不硬编码/落文件。**

## 码位勘误（实测 2026-10-07，brief 预设与上游实际不符）

| brief 预设 | 实测 | 结论 |
|---|---|---|
| `category_code=334413`（半导体 NAICS） | HTTP **204** 空响应（`334`/`3344`/`33441`/`336413`/`TOTAL`/`DURABLES` 同样 204） | **m3 的 category_code 是助记码，不是 NAICS**——六位 NAICS 与英文词全不命中 |
| 锚"NAICS 334413 固定历史月" | `MTM`（制造业总计）可用且口径稳定 | 落地为锚 **MTM/2020-01**；如需半导体细分，m3 无对应码位，需换源（ASM/MA333T）另行接档 |

MTM 2020-01 未季调 16 行：`VS`=出货、`NO`=新订单、`MI`/`WI`/`FI`/`TI`=库存、
`UO`=未完成订单、`IS`/`US`=比率、`MPC*`=环比 %。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `time` | 表头 `time` | `2020-01`（固定历史月） |
| `category_code` | 表头回显 | `MTM`（制造业总计） |
| `data_type_code` | 表头 | 见上表；`MPC*`=环比、`E_*`=抽样误差 |
| `seasonally_adj` | 表头回显 | 固定 `no`（未季调原值） |
| `cell_value` | 表头 | 数值化 float（百万美元 / 比率 / 百分比）；脏值原样字符串透出 |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级可追溯（**已剔除 `key=` 参数，key 不落盘**）/ `census-m3` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **必填谓词 `category_code` + `seasonally_adj`**：缺任一上游即
  `error: missing required variable/predicate: ...`（HTTP 400，实测）。
- **无 key/超限坑**：上游 302 → HTML "Missing Key" 错误页（HTTP 200 形态）——
  响应体首字符必须为 `[`，否则显式报错，**绝不允许静默降级为无 key 通道裸跑**。
- **golden 锚固定 category_code 固定历史月值**（构建时 2026-10-07 实况冻结）：
  `MTM/VS/2020-01 == 435587`（出货）、`MTM/MI/2020-01 == 244888`（库存）、
  `MTM/NO/2020-01 == 439561`（新订单）。若上游年度基准修订触及 2020-01，
  需人工复锚。
- 拉取失败 / 载荷无表头 / 零可用行 → `RuntimeError`（失败即红，不静默返回空列表）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗；`www.census.gov` 有
  Cloudflare——适配器只碰 `api.census.gov`（干净）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env
python3 spiders/census-m3/spider.py          # 冒烟，打印前 3 行
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-census-m3-a7211ee6.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-census-m3.json`：`params={"limit":20}` → 2020-01 未季调制造业
总计 16 行。锚 `time == 2020-01`、`seasonally_adj == no`、
`category_code == MTM`、`data_type_code ∈ {VS, MI, NO}`、
`cell_value == 435587.0 / 244888.0 / 439561.0`（实测定稿值）、
`source == census-m3`，`min_rows: 16`。**绝不锚**当期/近月未定稿值；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
