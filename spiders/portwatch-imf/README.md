# portwatch-imf 接入档案

IMF PortWatch（国际货币基金组织港口与海运咽喉监测）日度数据。入口：
`run_portwatch_imf(limit: int = 100) -> list[dict]`，返回行 schema 与 manifest
`functions[].columns` 对齐，每行带 `scraped_at` / `source_url`，`limit` 生效（返回 ≤limit 行）。

- 网络：海外源。**【直连=6/代理=0】** 2026-10-02 实测直连成功（curl 与 scrapling curl_cffi
  引擎均通，本次共 6 个请求全部直连 200），无需代理；代码保留直连失败 → `http://127.0.0.1:7890`
  （先探测端口再走一次）的兜底，成功模式粘滞。
- 依赖：仅 scrapling 0.4.x（FetcherSession）+ 标准库，无新增依赖。

## 端点与鉴权

ArcGIS FeatureServer（services9.arcgis.com 实例，PortWatch 组织 id `weJ1QsnbMYJlCHdG`，
两个服务均为纯表格图层 layer 0、无几何）：

| service | 完整 query URL |
|---|---|
| ports | `https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Ports_Data/FeatureServer/0/query` |
| chokepoints | `https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query` |

- 鉴权：**免 key**，直连即可（实测 200）。
- 组织内服务目录清单：`https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services?f=json`。

## 参数白名单（只发以下实测有效参数，多余参数一律不发）

- **取最新日**（每服务先发一次）：`f=json&where=1=1&outStatistics=[{"statisticType":"max","onStatisticField":"date","outStatisticFieldName":"maxdate"}]&returnGeometry=false`
  → `features[0].attributes.maxdate` 即最近可得日（实测 ~1s 返回）。
- **翻页取数**：`f=json&where=date>date'<D-1>'&outFields=*&returnGeometry=false&orderByFields=portid&resultOffset=<n>&resultRecordCount=1000`。
- **分页策略硬约束（W2-10 实测坑）**：
  - 日过滤用 `where=date>date'D-1'`（`date` 字段是 `esriFieldTypeDateOnly`，对 date-only 字面量
    严格 `>` 即精确选中 D 日全天；实测 `>2026-09-24` 恰好返回 09-25 全天 2065 行）；
  - `resultOffset` + `resultRecordCount=1000` 翻页（服务端 `maxRecordCount=1000`，不得调大）；
  - **`orderByFields=portid`——按 date 排序会服务端超时，禁用**；
  - **只在 `exceededTransferLimit=true` 时继续翻页；空页即停**；日度表历史日不可变，offset 翻页安全。
- 最新日定位替代方案（已否决）：`returnDistinctValues` 同样受 1000 条截断（实测只回 2019-01-01 起
  1000 个日期），不可用于找最新日；逐日探测 `returnCountOnly` 可行但啰嗦，仅作应急。

## 上游字段清单（2026-10-02 实测 layer 元数据）

ports（Daily_Ports_Data/0，31 字段）：`date`(DateOnly)、`year`、`month`、`day`、`portid`、
`portname`、`country`、`ISO3`、`portcalls_container/dry_bulk/general_cargo/roro/tanker/cargo/portcalls`、
`import_container/dry_bulk/general_cargo/roro/tanker/cargo/import`、
`export_container/dry_bulk/general_cargo/roro/tanker/cargo/export`、`ObjectId`。
系统字段：`objectIdField=ObjectId`、`maxRecordCount=1000`。

chokepoints（Daily_Chokepoints_Data/0，22 字段）：`date`、`year`、`month`、`day`、`portid`、
`portname`、`n_container/dry_bulk/general_cargo/roro/tanker/cargo/n_total`（过航艘次）、
`capacity_container/dry_bulk/general_cargo/roro/tanker/cargo/capacity`（过航吨）、`ObjectId`。

本爬虫输出为两服务的调和子集（13 列）：`service / port_id / port_name / country / iso3 / date /
portcalls / import_tonnes / export_tonnes / transit_vessels / throughput_tonnes / scraped_at / source_url`。
映射：ports 行填 `portcalls←portcalls`、`import_tonnes←import`、`export_tonnes←export`（六分项不落列，
需要时在 README 记录的字段上扩列）；chokepoints 行填 `transit_vessels←n_total`、`throughput_tonnes←capacity`，
`country/iso3/portcalls/import_tonnes/export_tonnes` 留空（不造数）。吨位上游即整数。

## 容错规则（spec 契约）

- HTTP 5xx / 空响应 / 非 JSON / ArcGIS `error` 载荷：记录 warning 后跳过该服务，**不重试、不绕反爬**。
- 直连连接层失败（异常，非 HTTP 状态码）才触发代理兜底：先 socket 探测 `127.0.0.1:7890`，通则走
  `FetcherSession(proxy=...)` 再试一次；服务端已应答（4xx/5xx）不换道不重试。成功模式粘滞（`NET_MODE`）。
- 翻页只看 `exceededTransferLimit=true`，空页即停；另有 `MAX_PAGES=200` 安全上限（一日 ~3 页，永不触及）。
- 缺 `portid` / `date` 关联键的行视为脏行跳过，不写脏行；数值缺失保持 null，不补 0。

## 历史深度与数据形态（2026-10-02 实测）

- 两服务均为 **2019-01-01 起、日度、逐日整齐**：`1=1` 总数 ports 5,833,625 = 2065 × 2825 天、
  chokepoints 79,156 = 28 × 2827 天（首末日 2019-01-01..各服务最新日，每日行数恒定）。
- **数据滞后**：ports 最新日 2026-09-25（取数日 2026-10-02，滞后 7 天）；chokepoints 最新日
  2026-09-27（滞后 5 天）。两服务最新日不同步，爬虫按服务各自 MAX(date) 取「最近可得一天」。
- **与侦察簿口径的差异（重要）**：旧版 PortWatch 材料宣称 ~6880 港口 / 42 咽喉点；当前数据库已重建，
  `PortWatch_ports_database` 静态港口表 = 2065 个港口、每日 chokepoints = 28 个咽喉点（名称清单实测，
  苏伊士、巴拿马、霍尔木兹、马六甲、直布罗陀、好望角等 28 个）。日行数 2065/天 即当前「一天全量」。

## limit 语义

`limit` 是**两服务合计的行数上限**（默认 100 < 一天全量，只返回 ports 服务 portid 升序的前 100 行；
README 特此说明）。一天全量 = ports ~2065 行 + chokepoints ~28 行 ≈ 2093 行，**取完整一天请传
limit ≥ 2200**。超 limit 时按 ports→chokepoints 顺序、portid 升序截断。

## 真实取数证据（取数日 2026-10-02，UTC 2026-10-01T20:33:50Z，直连，NET_MODE=direct）

执行 `run_portwatch_imf(limit=100000)`：**总条数 2093**（ports 2065 + chokepoints 28），

| service | 取数日 | 行数 | 翻页 | 日合计 |
|---|---|---|---|---|
| ports | 2026-09-25 | 2065 | 3 页（1000+1000+65，末页 exceededTransferLimit 消失即停） | portcalls 4728 艘次，进口 30,346,339 t，出口 30,002,216 t |
| chokepoints | 2026-09-27 | 28 | 1 页 | 过航 1749 艘次，61,302,766 t |

ports 首行（portid=fso0）/末行（portid=port999 Leixoes，portcalls 4，进口 10,974 t / 出口 9,753 t）
与 chokepoints 首行（Suez Canal，37 艘次 / 1,277,754 t）末行（Dover Strait，162 艘次 / 3,370,078 t）
均见样例；全日 portcalls>0 的港口 1104 个（其余 961 个当日零活动，属上游真实零值非缺格）。

样例行（chokepoints 首行）：

```json
{"service": "chokepoints", "port_id": "chokepoint1", "port_name": "Suez Canal", "country": "", "iso3": "",
 "date": "2026-09-27", "portcalls": null, "import_tonnes": null, "export_tonnes": null,
 "transit_vessels": 37, "throughput_tonnes": 1277754,
 "scraped_at": "2026-10-01T20:33:50+00:00",
 "source_url": "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query?f=json&where=date%3Edate%272026-09-26%27&outFields=%2A&returnGeometry=false&orderByFields=portid&resultOffset=0&resultRecordCount=1000"}
```

样例行（ports，结果首行）：

```json
{"service": "ports", "port_id": "fso0", "port_name": "Brazil - Offshore Oil Terminal 1", "country": "Brazil",
 "iso3": "BRA", "date": "2026-09-25", "portcalls": 0, "import_tonnes": 0, "export_tonnes": 0,
 "transit_vessels": null, "throughput_tonnes": null,
 "scraped_at": "2026-10-01T20:33:50+00:00",
 "source_url": "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Ports_Data/FeatureServer/0/query?f=json&where=date%3Edate%272026-09-24%27&outFields=%2A&returnGeometry=false&orderByFields=portid&resultOffset=0&resultRecordCount=1000"}
```

## 本地自检

```bash
cd /Users/chengsishi/finddata/fd-industry-data && .venv/bin/python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("spider", "spiders/portwatch-imf/spider.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = m.run_portwatch_imf(limit=5)
print(len(rows), rows[0])
PY
```
