# eurostat-api 接入档案

Eurostat（欧盟统计局）分发 API。入口：`run_eurostat_api(limit: int = 100) -> list[dict]`，
返回行 schema 与 manifest `functions[].columns` 对齐，每行带 `scraped_at` / `source_url`，`limit` 生效（返回 ≤limit 行）。

- 网络：海外源。**【直连=1/代理=0】** 2026-10-02 实测直连成功（curl 与 scrapling curl_cffi 引擎均通），无需代理；
  代码保留直连失败 → `http://127.0.0.1:7890`（先探测端口再走一次）的兜底，模式粘滞。
- 依赖：仅 scrapling 0.4.x（FetcherSession）+ 标准库，无新增依赖。

## 端点与鉴权

- 端点：`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}`
- 鉴权：**免 key**，直连即可（实测 200）。固定参数 `format=JSON&lang=EN`。
- 返回 JSON-stat 2.0（`class: dataset`）。

## 数据集 × 维度参数白名单（只发以下实测有效参数，多余参数一律不发）

数组维度以标准重复 query 编码（如 `geo=DE&geo=FR&geo=IT`）。

| dataset | 固定维度参数 | geo | sinceTimePeriod | 频率 |
|---|---|---|---|---|
| `sts_inpr_m` 制造业生产指数（月度） | `freq=M&indic_bt=PRD&s_adj=NSA&unit=I21`，`nace_r2=C`（制造业）与 `nace_r2=C20`（化工） | DE, FR, IT | 固定 `2015-01`（验收窗口） | monthly |
| `ei_bsco_m` 消费者信心指标（月度） | `freq=M&indic=BS-CSMCI&s_adj=SA&unit=BAL`（BS-CSMCI=综合消费者信心指标） | DE, FR, IT | 运行时回补 12 个月（`YYYY-MM`） | monthly |
| `apri_pi_outq` 农业产出价格指数（季度） | `freq=Q&am_item=AM010000&p_adj=NI&unit=I20`（AM010000=谷物含种子，NI=名义指数，I20=2020=100） | DE, FR, IT | 运行时回补 12 个月（`YYYY-QN`） | quarterly |

- **时间参数格式必须 `2024-06`（月）/ `2024-Q2`（季）**，经 `sinceTimePeriod` 下推窗口。
- **禁用数据集（实测）**：`ei_bsci` → HTTP 404（2026-10-02 实测），消费者信心一律用 `ei_bsco_m`；
  `apri_pi05_outq` 已失效（侦察簿 W3 实测口径），农业价格一律用 `apri_pi_outq`。

## JSON-stat 2.0 解析硬约束

响应 `value` 是 `{扁平索引: 数值}` 且 **null 格被省略**（例：sts_inpr_m 3 国×140 月声明 420 格只回 417 值；
IT 的 CCI 2026-09 缺格）。解析必须按 `id`/`size`/`dimension[].category.index` 重建各维坐标：
扁平索引按行主序（首个维度最慢、`time` 最快）用步长 divmod 拆多维下标，再反查维度码表；
缺格不造数（只产出非空数值格），越界索引告警跳过，绝不能按 value dict 顺序想当然。

## 容错规则（spec 契约）

- HTTP 5xx / 空响应 / 非 JSON / `class != dataset`：记录 warning 后跳过该数据集，**不重试、不绕反爬**。
- 直连连接层失败（异常，非 HTTP 状态码）才触发代理兜底：先 socket 探测 `127.0.0.1:7890`，通则走
  `FetcherSession(proxy=...)` 再试一次；服务端已应答（4xx/5xx）不换道不重试。
- 成功的网络模式粘滞（`NET_MODE`），后续请求沿用，避免每个数据集都试错。
- 解析失败/非法格留空跳过，不写脏行。

## 历史深度

- `sts_inpr_m`：**实测可回溯至 1953-01**（DE, nace_r2=C, unit=I21, s_adj=NSA 共 884 期，2026-10-02 实测）。
  首爬窗口按验收口径固定 2015-01→最新（140 个月声明格）；如需更长史改 spider 内 `since` 即可。
- `ei_bsco_m`：侦察口径 1985 年起可回溯；本爬虫按 12 个月滚动回补。
- `apri_pi_outq`：季度，随官方发布节奏（实测最新 2026-Q2）；按 12 个月滚动回补。

## 真实取数证据（2026-10-02，取数时间 UTC 2026-10-01T20:17:58Z，直连）

本次执行 `run_eurostat_api(limit=5000)`：**总条数 878**（sts_inpr_m 834 + ei_bsco_m 35 + apri_pi_outq 9）。

验收项 sts_inpr_m（nace_r2=C, unit=I21, s_adj=NSA, 2015-01→最新），**每国 139 条 ≥130 ✅**：

| geo | 条数 | 首期 | 首值 | 末期 | 末值 |
|---|---|---|---|---|---|
| DE | 139 | 2015-01 | 91.0 | 2026-07 | 93.3 |
| FR | 139 | 2015-01 | 97.1 | 2026-07 | 104.4 |
| IT | 139 | 2015-01 | 84.0 | 2026-07 | 106.4 |

nace_r2=C20（化工）同步回补：DE/FR/IT 各 139 条（DE 末值 79.5，FR 90.9，IT 92.5，末期同 2026-07）。

ei_bsco_m（BS-CSMCI, SA, BAL, 2025-10→2026-09）：DE 12 条（-10.8 → -14.5）、FR 12 条（-15.5 → -18.4）、
IT 11 条（2025-10=-15.4 → 2026-08=-20.3，2026-09 官方缺格，验证了空格省略解析路径）。

apri_pi_outq（AM010000 谷物, NI, I20, 2025-Q4→2026-Q2）：DE 3 条（102.4 → 104.4）、FR 3 条（103.08 → 108.97）、
IT 3 条（121.4 → 119.5）。

样例行（结果首行，sts_inpr_m DE）：

```json
{"dataset": "sts_inpr_m", "geo": "DE", "freq": "M", "unit": "I21", "period": "2015-01", "value": 91.0,
 "scraped_at": "2026-10-01T20:17:58+00:00",
 "source_url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/sts_inpr_m?format=JSON&lang=EN&freq=M&indic_bt=PRD&nace_r2=C&nace_r2=C20&s_adj=NSA&unit=I21&geo=DE&geo=FR&geo=IT&sinceTimePeriod=2015-01",
 "indic": "PRD", "nace_r2": "C", "s_adj": "NSA", "p_adj": ""}
```

## Batch2 Wave C 四扩展（端点 / 参数 / 锚值 / 复测证据）

四个专用入口（同一 base URL，各一个 dataset×维度组合，均在 2026-10-07 实测冻结）：
`run_eurostat_c20` / `run_eurostat_bsci` / `run_eurostat_apri` / `run_eurostat_comext`，
锚值冻结于 `golden/002..005`。所有查询都过 `_assert_nonempty` 硬断言——
Eurostat 对无数据参数组返回 **HTTP 200 空集**，空集必须硬失败，绝不能当零行成功。

### C20 化工生产指数 — run_eurostat_c20

- 端点：`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/sts_inpr_m`
- 参数：`freq=M&indic_bt=PRD&nace_r2=C20&s_adj=CA&unit=I21&geo=EU27_2020&sinceTimePeriod=2000-01`
  （`s_adj=NSA`×C20 为空集，唯一活组合是 CA；`indic_bt=PROD` 已死）
- 锚值：**2026-07 = 81.8**（I21, PRD, CA, EU27_2020），golden/002 冻结同一 source_url。

### 消费者信心 NSA 变体 — run_eurostat_bsci

- 端点：`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ei_bsco_m`
- 参数：`freq=M&indic=BS-CSMCI&s_adj=NSA&unit=BAL&geo=EU27_2020&sinceTimePeriod=1980-01`
  （序列维自 1980-01 声明，EU27_2020 实值从 1985-01 起；SA 口径由原 run_eurostat_api 覆盖）
- 锚值：**2026-09 = -15.6**（BAL, BS-CSMCI, NSA），golden/003 冻结。
- 禁用：`ei_bsci` 上游 404（实测），永不使用。

### 农业产出价格指数 — run_eurostat_apri

- 端点：`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/apri_pi_outq`
- 参数：`freq=Q&am_item=AM141000&p_adj=NI&unit=I20&geo=EU27_2020&sinceTimePeriod=2000-Q1`
  （AM141000 实值 2020-Q1..最新，基期 2020=100）
- 锚值：**2026-Q2 = 135.29**（I20, NI），golden/004 冻结。
- 禁用：`apri_pi05_outq` 已死（实测），农业价格一律用 `apri_pi_outq`。

### 中欧月度贸易 — run_eurostat_comext

- 端点：`https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data/DS-045409`
  （SDMX 2.1 GenericData XML；`/statistics/1.0` 分发段对 comext 404，永不改道）
- 键面：`M.EU27_2020.CN.{HS6}.{flow}.VALUE_IN_EUROS?startPeriod={since}`；
  reporter/partner 只收 ISO 码（EU27_2020/CN，旧码 1A/1Z 静默空集）。
- **贸易流方向（2026-10-07 复核双探针定案）：flow=1 = EU 进口（中国对欧出口视角），
  flow=2 = EU 出口。** 首版误用 flow=2，实采成了欧盟对华出口，已改 1 并重锚。定案证据：
  - HS 360410（烟花爆竹，中国垄断欧盟供给）：flow=1 自 2025-01 起连续 19 个月实值
    （€7.45M → €27.09M）；flow=2 仅 2 个零星观测（2025-02=€169,007、2026-03=€34,453）。
  - HS 854142 @2025-01：flow=1 = €3,917,107 vs flow=2 = €95,058。
- HS2022 码位断点：854140（HS2017）数据止于 2021-12（since 2018-01）；
  HS2022 的 854141/854142/854143/854149 自 2022-01 起（since 2022-01）。
- 锚值（golden/005 冻结）：**854142 @2025-01 = 3917107（EUR，EU 自华进口）**；
  复测冒烟（limit=500）：268 行（48+55×4），全部 flow=1，
  854140=2018-01..2021-12，HS2022 四码=2022-01..2026-07。
- 口径修正：measure 列为 `trade_value`（欧元贸易额语义，manifest 绑
  `economic.trade_value` / `unit: EUR`），与其余四入口的指数语义 `value`
  （`economic.index_value`）拆分——金额不再错挂指数 concept。

## 本地自检

```bash
cd /Users/chengsishi/finddata/fd-industry-data && .venv/bin/python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("spider", "spiders/eurostat-api/spider.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = m.run_eurostat_api(limit=5)
print(len(rows), rows[0])
PY
```
