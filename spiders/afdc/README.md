# afdc —— 美国能源部替代燃料数据中心（AFDC）加注站数量

批次三直建单元。**免 key、免鉴权、直连可达**（无需代理出口，manifest 不声明
`egress_secret`）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | 美国能源部 AFDC「Data, Analysis & Trends」（afdc.energy.gov/data） |
| 图表 | **U.S. Public and Private Alternative Fueling Stations by Fuel Type**，chart ID `10332` |
| 端点 | `GET https://afdc.energy.gov/data/10332.json`（图表 Highcharts 载荷） |
| 鉴权 | **免 key、免鉴权**（匿名 GET 即可，无 UA/频次对抗） |
| 频度 | 年度序列；上游不定期滚动更新（实测 `last_update: "January 2026"`） |
| 历史深度 | 1992..2025（实测 34 个年度点），9 条燃料 series |
| xlsx 兜底 | 同页 `spreadsheet` 字段给出 `/files/u/data/data_source/10332/*.xlsx` 直链（免鉴权，本单元不解析） |

JSON 载荷结构（实测 2026-10-08）：

```json
{"id": 10332, "renderer": "highcharts", "title": "U.S. Public and Private Alternative Fueling Stations by Fuel Type",
 "categories": [1992, 1993, ..., 2025],
 "series": [{"name": "Biodiesel", "data": [null, ..., 1793.0, 1645.0]},
            {"name": "CNG",      "data": [349.0, 497.0, ..., 1385.0]}, ...],
 "yaxis": "Number of Stations", "last_update": "January 2026",
 "spreadsheet": "/files/u/data/data_source/10332/10332_alt_fueling_stations_fuel_1_26_2026.xlsx"}
```

输出行：`(year, fuel, stations, unit, chart_title, upstream_updated, url, source, scraped_at)`，
`series.data[i]` 与 `categories[i]` 按下标配对；`null`/非数值缺测行不透出。
`limit` = 返回行数上限：全量解析后按（year, fuel）字典序确定性排序，保留最近 `limit` 行。

## 2. 通道选择理由（JSON 优先，xlsx 不用）

批次三 brief 明确：**优先找 JSON 通道，JSON 可用就不要解析 xlsx**。探测结果：

| 候选通道 | 实测 | 判定 |
|---|---|---|
| `GET /data/{id}.json`（图表载荷） | 200，结构含 `categories`+`series` 数值 | ✅ **选中** |
| `GET /data.json`（DCAT 式目录） | **406**（带 `Accept: application/json` 同样 406） | ❌ 站点不提供 |
| `/data/search.json`、`/api/v1` 等 | 404 / 406 | ❌ 不存在免 key 的目录 API |
| 州级 `by State` 图（10365-10371、10971） | 200 但 `categories` 空、series 无数值 | ❌ map 图 JSON 不载数值 |
| 页面内 `/files/u/...xlsx` 直链（brief 侦察线索） | 200 可下载 | ⭕ 兜底保留，不启用（JSON 已覆盖） |

**信号选择理由**：brief 建议「替代燃料加注站数量」类周期统计——`10332` 是 AFDC
目录中的表格型 line 图，1992 年起连续、按燃料分 series（含 Electric 至 8.5 万站，
转型信号明确），是加注基础设施规模的权威年度序列；相较零售价类图表（10326），
站数是慢变量存量统计，适合年度/月度巡检。

## 3. 直连证据（2026-10-08 实测）

| 出口 | 请求 | 结果 |
|---|---|---|
| 本机直连 | `GET https://afdc.energy.gov/data/10332.json` | HTTP 200，2.8 KB JSON |
| tencent 站点直连（guangzhou xinru-server1） | 同上 | HTTP 200，2.1 s |
| 本机直连 | `GET https://afdc.energy.gov/data`（目录页，图表 ID 来源） | HTTP 200 |

→ 三处全绿，**无需 fx01 代理出口**；manifest 不写 `egress_secret`、不写 `site`。

## 4. 口坑

- **`/data.json` 是 406 不是 404**：AFDC 的 Rails 路由对 `.json` 格式缺失的请求返
  `406 Not Acceptable`——别误判成「目录接口存在但参数错」；图表数据只能按 ID 逐个取。
- **map 图 JSON 无载荷**：州级「by State」图（10365 Biodiesel / 10366 EV charging /
  10367 E85 / 10368 CNG / 10369 LNG / 10370 Hydrogen / 10371 Propane / 10971
  Renewable Diesel）返回的 JSON `categories` 为空、`series` 无数值；只有 line/bar
  图在 JSON 里载数值。选图必须实弹验证 JSON 有 `categories`+数值。
- **series 缺测是 `null`**：起步晚的燃料（如 Renewable Diesel）早期年度为 `null`，
  一律跳过该行，`limit` 只数真实观测。
- **计数随上游滚动修订**：最近年度的站数每月可能变化（上游月度爬取 AFDC 站点库），
  历史 1992-2012 年数值亦有过小幅回修——golden **只锚结构常量**（`source`/`unit`/
  固定历史年 `year=2000` + `fuel` 码表）+ `min_rows`，**绝不锚任何计数值**。
- **仅常规 UA**：免鉴权站点，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 缺 categories-series 结构 / 0 观测行 →
  抛 `RuntimeError`，不静默回空。

## 5. golden 断言口径（跨期稳定）

`golden/001-afdc.json` 用固定深历史年 `{"limit": 50, "year": 2000}` 重放
（2000 年实测 7 条燃料观测），锚结构常量：`source=afdc` / `unit=stations` /
`year=2000` / `fuel=CNG`（CNG 自 1992 年起每期都在）+ `min_rows=1`。
**绝不锚** `stations` 数值；`scraped_at` / `upstream_updated` 进 `whitelist_fields`。

## 6. 自检

```bash
python3 spiders/afdc/spider.py                  # 冒烟：打印最近 3 行观测
python3 scripts/validate_manifests.py           # manifest 契约校验
python3 scripts/check_manifest_commands.py      # manifest 命令漂移 lint
python3 scripts/conformance_gate.py --roots .   # 仓级机械闸
```
