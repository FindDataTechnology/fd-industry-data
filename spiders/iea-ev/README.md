# iea-ev —— IEA 全球电动汽车数据（Global EV Data API）

新源接入档案（`templates/new-source/` 范式，范本 `spiders/nmc-weather/`）。工单：
`reports/health-tickets/20261006-iea-ev-53f8d3d7.yaml`（`kind=generate`，brief 侦察簿 W2-3）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 数据 | `GET https://api.iea.org/evs?region=&category=&parameter=&mode=&powertrain=&year=` | 公开 REST，免鉴权，返回 `[{region,category,parameter,mode,powertrain,year,unit,value}]` |
| 维度枚举（排障用） | `GET /evs/list/region`、`/evs/list/mode?region=`、`/evs/list/powertrain?region=&mode=` | 实测可用；不进主流程 |

入口：`run_iea_ev(limit=100, region=..., category=..., parameter=..., mode=..., powertrain=...) -> list[dict]`
（`spider.py`）。仅用标准库 `urllib`，无新依赖。

## 参数白名单（只发实测过的值，2026-10-06 全量 GET 实测）

| 维度 | 实测可用值 |
|---|---|
| `region` | World / China / Europe / USA |
| `category` | Historical / Projection-CPS / Projection-STEPS |
| `parameter` | EV sales / EV stock / EV sales share / EV stock share / Battery deployment / Electricity demand / Oil displacement Mlge / Oil displacement, Mbd |
| `mode` | Cars / 2 and 3 wheelers / Trucks / Vans / Buses / EV / EVSE |
| `powertrain` | BEV / PHEV / FCEV / EV |

不带 `year` = 全年份返回（历史 2010-2025 逐年一行，投影至 2035），行按 `year` 升序稳定。
上游对未见过的参数组合返回**空数组且不报错**，因此单元侧对白名单之外的组合直接
`ValueError` 拒绝发出，避免「空结果空洞通过」。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `region` / `category` / `parameter` / `mode` / `powertrain` | 同名字段 | 维度标签原样透出 |
| `year` | `year` | int；历史 2010-2025 |
| `unit` | `unit` | Vehicles / percent / GWh 等，原样透出 |
| `value` | `value` | float；解析失败 → `None` |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `iea-ev` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

默认序列：World · Cars · EV sales · BEV · Historical（brief 期望「全球 EV 销量」主口径，
16 行 / 2010-2025）。

## 坑位与容错（brief.notes 逐条落实）

- **无 key**：IEA 该 API 免鉴权，无需注册；带常规浏览器 UA + `Referer: https://www.iea.org/`。
- **历史段稳定**：≤2024 年历史值跨期冻结（2020 = 2000000 Vehicles、2010 = 7000 Vehicles）；
  当期（2025）与投影值会随 GEO 年度版（每年 4 月）刷新——golden **只锚 2010 固定值**，
  绝不锚当期。
- **失败即红**：单一数据口，取数异常 / 负载非数组 / 0 行 → `RuntimeError`，不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度

2010-2025 历史段（年度）；`category=Projection-CPS/Projection-STEPS` 时至 2035。
数据按年更新（Global EV Outlook，约每年 4 月发布新版）。

## 实测样例行（2026-10-06 GET 证据）

```
GET https://api.iea.org/evs?region=World&category=Historical&parameter=EV+sales&mode=Cars&powertrain=BEV
→ [{"region":"World","category":"Historical","parameter":"EV sales","mode":"Cars",
    "powertrain":"BEV","year":2010,"unit":"Vehicles","value":7000}, ... 共 16 行]
```

## 自检

```bash
python3 spiders/iea-ev/spider.py          # 单页冒烟，打印前 5 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-iea-ev-53f8d3d7.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-iea-ev.json` 用 `params={"limit":5}`（行序 year 升序 → 恒为 2010-2014 五行），
锚常量维度标签 + **固定历史期数值**：`year==2010 && value==7000.0`（World·Cars·BEV·EV sales）。
**绝不锚** 2025 当期值、投影值、`url` 查询串与任何会随年度刷新的字段；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
