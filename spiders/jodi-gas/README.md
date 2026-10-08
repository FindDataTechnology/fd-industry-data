# jodi-gas —— JODI-Gas 月度天然气供需（world 库）

批次三直建单元。jodi-oil 兄弟范式（先读 `spiders/jodi-oil/spider.py` + README 的
解析与 golden 口径再动工）。**免 key、免鉴权、直连可达**（无需代理出口）。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 平台 | JODI（国际能源论坛联合组织数据倡议）Gas 世界数据库 |
| 下载页 | `https://www.jodidata.org/gas/database/data-downloads.aspx`（HTML **不吐直链**，Vue JS 应用渲染） |
| 发现接口 | `GET https://api.publisher.jodidata.org/web/files/gas` → `{"publicationId": 27, "files": [{"filename": "GAS_world_NewFormat.zip", "format": "CSV", ...}]}` |
| 下载直链 | `GET https://www.jodidata.org/jodi-publisher/gas/{publicationId}/GAS_world_NewFormat.zip`（≈1.7 MB zip → 单成员 SDMX CSV ≈13 MB / 31.6 万行） |
| 鉴权 | **免 key、免鉴权**（两跳均匿名 GET，无 UA/频次对抗） |
| 频度 | 月度；上游每月发布（两个月滞后节奏，与 JODI-Oil 同） |
| 历史深度 | 2009-01 .. 最新月（实测 2026-07），覆盖 94 个自报经济体（2009 年仅 16 个，逐期扩容） |

CSV 列与 jodi-oil 完全同构：`REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,
UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE`。

数据面（2026-10-08 实测全量画像）：

- 产品单一：`NATGAS`；单位三种：`TJ`（能量）/ `M3`（体积）/ `KTONS`（质量）
- 能流 14 种（上游原码透出）：`INDPROD` 产量 / `TOTIMPSB` 总进口（`IMPLNG`
  LNG、`IMPPIP` 管道分项）/ `TOTEXPSB` 总出口（`EXPLNG`、`EXPPIP` 分项）/
  `TOTDEMC`·`TOTDEMO` 国内总需求（计算/观测，`STATDIFF` = 两者之差，见 JODI Gas
  Manual）/ `STOCKCH` 库存变化 / `CLOSTLV` 期末库存 / `MAINTOT` 主要能流合计 /
  `OSOURCES` 其它来源

入口：`run_jodi_gas(limit=100, year=None) -> list[dict]`（`spider.py`）。仅用标准库
`urllib`/`csv`/`json`/`zipfile`/`io`，无新依赖。

## 2. 通道选择理由（brief：先探 gas 对应 CSV 通道，jodidb.org 备选不走）

| 候选通道 | 实测 | 判定 |
|---|---|---|
| **发现接口 + `/jodi-publisher/` zip 直链** | 两跳均 200（本机 + tencent） | ✅ **选中**：SDMX CSV 与 jodi-oil 同构，解析范式零漂移 |
| 直猜 `downloads/gas-data/{monthly,annual}-csv/primary/{year}.csv`（jodi-oil 路径平移） | 均 404 | ❌ gas 下载页是 JS 应用，路径体系不同 |
| jodidb.org TableViewer aspx（brief 备选） | 未走 | ⭕ 能不走就不走（动态渲染重） |
| JODI REST API（下载页挂有 `jodi-rest-api-v1.2.pdf`） | 未走 | ⭕ 需注册 key，违背免 key 约束 |

**信号口径**：经济体 × 能流的月度序列（`year` 缺省 = 数据文件内**最近 12 个日历月**
滚动窗口，历史回补按 `year` 逐窗口取）；下游可自行按经济体/单位聚合全球或主要
经济体分项——本单元**不聚合**（同能流三单位并存，跨单位混加会污染口径）。

## 3. 直连证据（2026-10-08 实测）

| 出口 | 请求 | 结果 |
|---|---|---|
| 本机直连 | `GET api.publisher.jodidata.org/web/files/gas` | HTTP 200，JSON 清单（publicationId=27） |
| 本机直连 | `GET www.jodidata.org/jodi-publisher/gas/27/GAS_world_NewFormat.zip` | HTTP 200，1,699,745 B zip |
| tencent 站点直连（guangzhou xinru-server1） | 发现接口 | HTTP 200 JSON |
| tencent 站点直连 | 下载直链（Range 100 KB 探针） | HTTP 206，6.0 s |

→ **无需 fx01 代理出口**；manifest 不写 `egress_secret`、不写 `site`。

## 4. 口坑（jodi-oil 已踩坑逐条继承 + 本源新坑）

- **下载页不吐直链**（与 oil 页关键差异）：下载列表由 `/assets/index-*.js` Vue
  应用渲染；直链路径 `/jodi-publisher/{type}/{publicationId}/{filename}` 是从
  前端 chunk 的拼串逻辑**公开可见处**读出的，未逆向反爬、不猜路径——publicationId
  每次经发现接口解析（当前 27，上游重发会漂移，**不硬编码**）。
- **zip 内 CSV 成员名不锚定**：现名 `STAGING_world_NewFormat.csv`；按「`.csv`
  后缀 + `REF_AREA` 表头嗅探」选成员（404/异常即 HTML 错误页的同款防御）。
- **缺测占位防御**：gas world 文件当前无占位（非数值 0 行，jodi-oil 是四种占位
  26.3 万行）——仍沿用 jodi-oil 哨兵，非数值整行跳过，`limit` 只数真实观测。
- **同能流多单位**：同一 `FLOW_BREAKDOWN` 在 TJ/M3/KTONS 三单位各有观测，
  **聚合绝不可跨单位混加**（README §2 信号口径亦然）。
- **经济体加总 ≠ 全球**：自报覆盖逐期扩容（2009 年 16 国 → 2026 年 94 国），
  早期年覆盖稀疏；全球合计须注意覆盖率与 `STATDIFF`。
- **排序键必须含 year**：jodi-oil 是单年文件、按 month 串排序即可；本源滚动窗口
  跨日历年，排序键 `(year, month, country, product, flow, unit)`（首版漏 year 的
  bug 已在冒烟中抓出修复）。
- **limit 语义**：确定性排序后保留**最近** `limit` 行（freshness-first；与
  jodi-oil 取前 N 不同——gas 窗口池含 12 个月 ≈1.8 万行，取尾部才是最新月）。
- **仅常规 UA**：免鉴权站点，不做指纹伪装、不做频次对抗。
- **失败即红**：发现/下载/解包/解析任一失败、或过滤后 0 观测行 → 抛
  `RuntimeError`，不静默回空。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 5. golden 断言口径（跨期稳定，jodi-oil 同款）

`golden/001-jodi-gas.json` 用固定深历史年 `{"limit": 50, "year": 2009}` 重放
（2009 年实测 2,341 行；截取尾部 50 行 = 2009-12 的 RU/TH/TW），锚**结构常量**：
`source=jodi-gas` / `product=NATGAS` / `year=2009` / `flow=INDPROD`（任一自报
经济体必有产量流）+ `min_rows=1`。**绝不锚** 数值/当期月度计数（上游逐月滚动
修订）；`scraped_at`/`url` 进 `whitelist_fields`。

## 6. 自检

```bash
python3 spiders/jodi-gas/spider.py          # 冒烟：打印滚动窗口最近 3 行观测
python3 scripts/validate_manifests.py       # manifest 契约校验
python3 scripts/check_manifest_commands.py  # manifest 命令漂移 lint
python3 scripts/conformance_gate.py --roots .  # 仓级机械闸
```
