# uk-hpi —— 英国土地注册局 UK HPI 房价指数

新源接入档案（`templates/new-source/` 范式，范本 `spiders/nmc-weather/`）。工单：
`reports/health-tickets/20261006-uk-hpi-48ae2c94.yaml`（`kind=generate`，brief 侦察簿 W2-5）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 探测 | `HEAD .../UK-HPI-full-file-YYYY-MM.csv` | 官方固定文件名，按月刷新 |
| 数据 | `GET .../UK-HPI-full-file-YYYY-MM.csv` + `Range: bytes=0-2097151` | 全量 CSV 约 34 MB，**支持 HTTP Range（实测 accept-ranges: bytes）**，只取头部样例 |

基地址：`https://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/`
（工单 brief.source_urls 里的 `private-rental-data/` 目录是 PRD 租金数据，HPI 实测在
`house-price-index-data/`，文件名口径与 brief 一致。）

入口：`run_uk_hpi(limit=100) -> list[dict]`（`spider.py`）。仅标准库 `urllib`/`csv`，无新依赖。

## 鉴权与参数白名单

- 无 key、无签名；带常规浏览器 UA。
- 单元不向上游发业务参数（文件名月份由单元自动向前扫描决定）：从当月起向前至多
  7 个自然月取最新在架文件（实测 2026-10 时最新为 2026-07，2026-08/09 尚未在架）。

## 口径（工单 brief：region/month/average_price/sales_volume，1995 起）

| 列 | 来源 | 口径 |
|---|---|---|
| `region` / `area_code` | `RegionName` / `AreaCode` | 区域名 + ONS 区码 |
| `month` | `Date`（`dd/mm/YYYY`） | 归一为 `YYYY-MM` |
| `average_price` | `AveragePrice` | 英镑均价 |
| `index` | `Index` | 房价指数水平 |
| `sales_volume` | `SalesVolume` | 成交量；空值 → `None` |
| `url` / `source` / `scraped_at` | — | 行级可追溯 / `uk-hpi` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **文件大**：34 MB+，不整档下载；按工单口径**只取头部样例**（默认前 2 MB ≈ 9000+ 行）。
- **Range 稳定**：文件内行序 = 区域（字母序）内日期升序，头部行是**固定历史期**
  （首区域 Aberdeenshire 自 2004-01 起；英格兰区域自 1995-01 起）。实测 2025-08 与
  2026-07 两个版本头部行字节级一致 → 跨月刷新不漂移。
- **当月缺位**：最新文件可落后当月数月，向前扫描兜底；近 7 个自然月全缺 → `RuntimeError`。
- **失败即红**：探测/取数/解析任何环节全失败 → `RuntimeError`，不静默返回空列表。
- **无 schedule**：manifest 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度

1995-01 起（英格兰/威尔士区域；苏格兰 2004-01、北爱 2005-01），月度。头部样例（2 MB）
覆盖约 20+ 个区域的完整历史段。

## 实测样例行（2026-10-06 GET 证据）

```
GET .../UK-HPI-full-file-2026-07.csv  Range: bytes=0-65535
→ 01/01/2004,Aberdeenshire,S12000034,84638,41.1,...,388,...（2004-01 均价 84638 GBP，指数 41.1，成交 388）
```

## 自检

```bash
python3 spiders/uk-hpi/spider.py          # 单页冒烟，打印前 3 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-uk-hpi-48ae2c94.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-uk-hpi.json` 用 `params={"limit":3}`（文件头三行恒定：Aberdeenshire 2004-01/02/03），
锚**固定 region 固定历史月数值**：`month==2004-01 && average_price==84638.0 && index==41.1
&& sales_volume==388.0`（Aberdeenshire·2004-01，两个版本实测一致）。
**绝不锚** `url`（随最新月份滚动）与任何当期值；`scraped_at`/`timestamp` 进 `whitelist_fields`。
