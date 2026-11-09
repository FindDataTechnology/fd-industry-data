# 餐饮监测舰 · 值守档案 2026-11-08

- 生成时间（UTC）: `2026-11-07T17:55:15.369140+00:00`
- 生成命令: `python3 scripts/restaurant_alerts.py --duty-log`
- 时区: 报告日期按本机本地日（CST）

## 1. 结论摘要

| 层 | 结论 | 证据（可回指） |
|---|---|---|
| 健康级（inspector） | 5 张未终态工单 | `20261107-census-eits-87d11ac3`, `20261107-fao-food-price-a7f3d778`, `20261107-fred-cpi-bc6601b0`, `20261107-hkex-disclosure-89934d77`, `20261107-ico-coffee-price-ba21fcd1` |
| 阈值级 | fired=4 / quiet=0 / n/a=1 | `threshold-catering_yoy_negative.json`, `threshold-cpi_scissors.json`, `threshold-price_spike.json` |
| 事件级 | 首报 0 / 已见 198 / 扫描 198 | `events.json`, `state/events.jsonl` |
| escalate | blocked | `state/escalations.jsonl` |

## 2. 健康级工单（来源：scripts/health_inspect.py）

### 2.1 `20261107-census-eits-87d11ac3` · source=`census-eits`

- 工单文件: `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-census-eits-87d11ac3.yaml`（sha256:f8d56a30bbde6327）
- unit: `spiders/census-eits/` · kind=`repair` · category=`network` · needs_human=False · terminal=None
- evidence.window: 2026-10-20T00:00:00Z → 2026-11-08T02:00:00Z
- last_success: `{"started_at": "2026-10-20T00:00:00Z", "rows": 10}`
- watermark: latest=None · basis=unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）
- 分诊轨迹: rule5:network votes=3/3 labels={'network': 3}

原始遥测行（工单 evidence.failing_runs，逐行回指快照）：

| started_at | status | rows | http_status | error_summary |
|---|---|---|---|---|
| 2026-11-06T02:00:00Z | failed | 0 | None | ReadTimeout: HTTPSConnectionPool timed out |
| 2026-11-07T02:00:00Z | failed | 0 | None | ReadTimeout: HTTPSConnectionPool timed out |
| 2026-11-08T02:00:00Z | failed | 0 | None | ReadTimeout: HTTPSConnectionPool timed out |

生命周期事件（append-only）: created@2026-11-07T16:36:48.177595+00:00

### 2.2 `20261107-fao-food-price-a7f3d778` · source=`fao-food-price`

- 工单文件: `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-fao-food-price-a7f3d778.yaml`（sha256:4648a3fbedeca05d）
- unit: `spiders/fao-food-price/` · kind=`repair` · category=`network` · needs_human=False · terminal=None
- evidence.window: 2026-10-20T00:00:00Z → 2026-11-08T02:00:00Z
- last_success: `{"started_at": "2026-10-20T00:00:00Z", "rows": 10}`
- watermark: latest=None · basis=unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）
- 分诊轨迹: rule5:network votes=3/3 labels={'network': 3}

原始遥测行（工单 evidence.failing_runs，逐行回指快照）：

| started_at | status | rows | http_status | error_summary |
|---|---|---|---|---|
| 2026-11-06T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |
| 2026-11-07T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |
| 2026-11-08T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |

生命周期事件（append-only）: created@2026-11-07T16:36:48.177595+00:00

### 2.3 `20261107-fred-cpi-bc6601b0` · source=`fred-cpi`

- 工单文件: `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-fred-cpi-bc6601b0.yaml`（sha256:02cbe6537d6112dd）
- unit: `spiders/fred-cpi/` · kind=`repair` · category=`network` · needs_human=False · terminal=None
- evidence.window: 2026-10-20T00:00:00Z → 2026-11-08T02:00:00Z
- last_success: `{"started_at": "2026-10-20T00:00:00Z", "rows": 10}`
- watermark: latest=None · basis=unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）
- 分诊轨迹: rule5:network votes=3/3 labels={'network': 3}

原始遥测行（工单 evidence.failing_runs，逐行回指快照）：

| started_at | status | rows | http_status | error_summary |
|---|---|---|---|---|
| 2026-11-06T02:00:00Z | failed | 0 | None | ConnectionResetError: connect reset by peer |
| 2026-11-07T02:00:00Z | failed | 0 | None | ConnectionResetError: connect reset by peer |
| 2026-11-08T02:00:00Z | failed | 0 | None | ConnectionResetError: connect reset by peer |

生命周期事件（append-only）: created@2026-11-07T16:36:48.177595+00:00

### 2.4 `20261107-hkex-disclosure-89934d77` · source=`hkex-disclosure`

- 工单文件: `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-hkex-disclosure-89934d77.yaml`（sha256:4d5c26ad758c7a8e）
- unit: `spiders/hkex-disclosure/` · kind=`repair` · category=`network` · needs_human=False · terminal=None
- evidence.window: 2026-10-20T00:00:00Z → 2026-11-08T02:00:00Z
- last_success: `{"started_at": "2026-10-20T00:00:00Z", "rows": 10}`
- watermark: latest=None · basis=unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）
- 分诊轨迹: rule5:network votes=3/3 labels={'network': 3}

原始遥测行（工单 evidence.failing_runs，逐行回指快照）：

| started_at | status | rows | http_status | error_summary |
|---|---|---|---|---|
| 2026-11-06T02:00:00Z | failed | 0 | 503 | 503 Service Unavailable |
| 2026-11-07T02:00:00Z | failed | 0 | 503 | 503 Service Unavailable |
| 2026-11-08T02:00:00Z | failed | 0 | 503 | 503 Service Unavailable |

生命周期事件（append-only）: created@2026-11-07T16:36:48.177595+00:00

### 2.5 `20261107-ico-coffee-price-ba21fcd1` · source=`ico-coffee-price`

- 工单文件: `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-ico-coffee-price-ba21fcd1.yaml`（sha256:22f491076f4d1e92）
- unit: `spiders/ico-coffee-price/` · kind=`repair` · category=`network` · needs_human=False · terminal=None
- evidence.window: 2026-10-20T00:00:00Z → 2026-11-08T02:00:00Z
- last_success: `{"started_at": "2026-10-20T00:00:00Z", "rows": 10}`
- watermark: latest=None · basis=unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）
- 分诊轨迹: rule5:network votes=3/3 labels={'network': 3}

原始遥测行（工单 evidence.failing_runs，逐行回指快照）：

| started_at | status | rows | http_status | error_summary |
|---|---|---|---|---|
| 2026-11-06T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |
| 2026-11-07T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |
| 2026-11-08T02:00:00Z | failed | 0 | None | ReadTimeout: timed out |

生命周期事件（append-only）: created@2026-11-07T16:36:48.177595+00:00

## 3. 阈值级证据（provenance）

### 规则 `catering_yoy_negative`（asof=2026-11-08, replay_from=2019-01-01）

- 证据文件: `reports/restaurant-alerts/2026-11-08/threshold-catering_yoy_negative.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `retail.cn.catering.yoy` | **fired** | 2022-12 | -14.1 | 0 | lt | 2019-01 → 2026-09 |

- `retail.cn.catering.yoy` · source_used=`nbs-stats` · 观测 77 期 · verdict=fired

### 规则 `cpi_scissors`（asof=2026-11-08, replay_from=2019-01-01）

- 证据文件: `reports/restaurant-alerts/2026-11-08/threshold-cpi_scissors.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `price.cpi.us.food_away_from_home - price.cpi.us.food_at_home` | **fired** | 2024-11 | 2.08365 | 2 | abs_gte | 2019-01 → 2026-09 |

- `price.cpi.us.food_away_from_home - price.cpi.us.food_at_home` · source_used=`fred-cpi` · 观测 92 期 · verdict=fired
  - 触发明细: `{"yoy_away": 3.648063, "yoy_home": 1.56441, "levels": {"food_away_from_home": 373.53, "food_at_home": 309.093}, "base_month": "2023-11", "base_levels": {"food_away_from_home": 360.383, "food_at_home": 304.332}}`

### 规则 `price_spike`（asof=2026-11-08, replay_from=2019-01-01）

- 证据文件: `reports/restaurant-alerts/2026-11-08/threshold-price_spike.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `price.coffee.icip.daily` | **fired** | 2026-08-25 | 2.74302 | 2.5 | abs_gte | 2025-09-01 → 2026-11-05 |
| `price.palm_oil.imf.monthly` | **fired** | 2026-03 | 2.74078 | 2.5 | abs_gte | 2019-01 → 2026-09 |
| `agri.pork.price.wholesale` | **n/a** | - | - | 2.5 | abs_gte | 2026-07-20 → 2026-10-26 |

- `price.coffee.icip.daily` · source_used=`ico-coffee-price` · 观测 255 期 · verdict=fired
  - 触发明细: `{"raw_value": 301.97, "rolling_mean": 286.475333, "rolling_stdev": 5.648771, "z_window": 30}`
- `price.palm_oil.imf.monthly` · source_used=`imf-pcps` · 观测 93 期 · verdict=fired
  - 触发明细: `{"raw_value": 175.226082, "rolling_mean": 155.014311, "rolling_stdev": 7.374455, "z_window": 12}`
- `agri.pork.price.wholesale` · source_used=`moa-hog-price` · 观测 0 期 · verdict=n/a · insufficient_history: 观测 15 期 < min_history=24（判 n/a，不用不足样本算 z-score）

## 4. 事件级（财报日历 / 盈警 / 抽检不合格）

- 扫描总数: 198 · 分类: `{"financial_calendar": 169, "profit_warning": 4, "samr_unqualified_restaurant": 25}`
- 台账已有: 198 · 本次首报: **0** · 已见跳过: 198

首报事件（每条可回指原始公告）：

| kind | date | 自然键 | title | 原件 |
|---|---|---|---|---|

## 5. 缺测归因（never_held / gap_in_series / not_applicable）

口径：只统计阈值证据文件里的缺测条目；不插值、不补零。

| rule | concept | never_held | gap_in_series | not_applicable | 说明 |
|---|---|---|---|---|---|
| catering_yoy_negative | `retail.cn.catering.yoy` | 0 | 0 | 16 |  |
| cpi_scissors | `price.cpi.us.food_away_from_home - price.cpi.us.food_at_home` | 0 | 1 | 0 |  |
| price_spike | `price.coffee.icip.daily` | 0 | 24 | 122 | never_held 区间 2019-01-01..2025-08-31（replay 早于首观测） |

明细（前 60 条，完整列表在各阈值证据文件的 `gaps` 字段）：

- `retail.cn.catering.yoy` 2019-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2019-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2020-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2020-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2021-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2021-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2022-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2022-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2023-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2023-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2024-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2024-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2025-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2025-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2026-01 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `retail.cn.catering.yoy` 2026-02 → **not_applicable** · NBS 不单独发布 1/2 月分月社零/餐饮（两月合并公布），属 not_applicable，不插值
- `price.cpi.us.food_away_from_home - price.cpi.us.food_at_home` 2025-10 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）
- `price.coffee.icip.daily` 2025-09-06 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-07 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-13 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-14 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-20 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-21 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-27 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-09-28 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-04 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-05 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-11 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-12 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-18 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-19 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-25 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-10-26 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-01 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-02 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-08 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-09 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-15 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-16 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-22 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-23 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-29 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-11-30 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-06 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-07 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-13 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-14 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-20 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-21 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-25 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-27 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2025-12-28 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-01 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-03 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-04 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-10 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-11 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-17 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-18 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）
- `price.coffee.icip.daily` 2026-01-24 → **not_applicable** · 周末；交易日序列源不发布（ICO 指示价为交易日序列；周末/节假日源不发布（not_applicable），缺失工作日按 gap_in_series 记录）

## 6. Escalate 路径

- at=2026-11-07T17:55:15.298154+00:00 · status=**blocked** · tickets=['20261107-census-eits-87d11ac3', '20261107-fao-food-price-a7f3d778', '20261107-fred-cpi-bc6601b0', '20261107-hkex-disclosure-89934d77', '20261107-ico-coffee-price-ba21fcd1'] · key_present=False
  - reason: WANXING_SPIDER_HEAL_KEY 未设置——已走到实际投递前的最后一步，未发出 SUBMIT
  - 下一步命令: `WANXING_SPIDER_HEAL_KEY=*** .venv/bin/python scripts/restaurant_alerts.py --escalate --tickets-dir /Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig`
  - 两段式约束: 工单须先落入远端可见的仓库（git push 落仓门）再投递；同轮写盘+投递会被 agent 以「文件不存在」拒收（docs/health-loop.md）。

## 7. 审计回指（文件 → 哈希）

| 文件 | sha256[:16] |
|---|---|
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-census-eits-87d11ac3.yaml` | f8d56a30bbde6327 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-fao-food-price-a7f3d778.yaml` | 4648a3fbedeca05d |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-fred-cpi-bc6601b0.yaml` | 02cbe6537d6112dd |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-hkex-disclosure-89934d77.yaml` | 4d5c26ad758c7a8e |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/health-drill/tickets-realconfig/20261107-ico-coffee-price-ba21fcd1.yaml` | 22f491076f4d1e92 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/threshold-catering_yoy_negative.json` | fe1f9d293c112ad6 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/threshold-cpi_scissors.json` | 52879c71d41f45e9 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/2026-11-08/threshold-price_spike.json` | 89ef3830e358cd85 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/restaurant-alerts/state/events.jsonl` | 9d3a7f6575d4d46a |

回指链：本档案 §2 工单文件 → 工单 `evidence.failing_runs` 行 → 快照 JSON 原始行（source/status/started_at/error）；§3 证据文件 → `semantic_observations`（concept + source_used + date）→ 上游源单元。
