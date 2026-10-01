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
