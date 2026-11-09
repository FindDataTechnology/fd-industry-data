# 酒店监测舰 · 值守档案 2026-11-08

- 生成时间（UTC）: `2026-11-07T22:11:23.368590+00:00`
- 生成命令: `python3 scripts/restaurant_alerts.py --config config/hotel-alerts.json --duty-log`
- 时区: 报告日期按本机本地日（CST）

## 1. 结论摘要

| 层 | 结论 | 证据（可回指） |
|---|---|---|
| 健康级（inspector） | 无未终态工单 | - |
| 阈值级 | fired=2 / quiet=2 / n/a=1 | `threshold-eu_accommodation_turnover_yoy_negative.json`, `threshold-hotel_revpar_yoy_decline_us.json`, `threshold-star_hotel_adr_yoy_drop.json`, `threshold-star_hotel_occupancy_yoy_negative.json`, `threshold-star_hotel_revpar_yoy_negative.json` |
| 事件级 | 首报 0 / 已见 237 / 扫描 237 | `events.json`, `state/events.jsonl` |
| escalate | 未执行（本次未跑 --escalate） | - |

## 2. 健康级工单（来源：scripts/health_inspect.py）

本次目录内无未终态工单。

## 3. 阈值级证据（provenance）

### 规则 `eu_accommodation_turnover_yoy_negative`（asof=2026-11-08, replay_from=2023-01-01）

- 证据文件: `reports/hotel-alerts/2026-11-08/threshold-eu_accommodation_turnover_yoy_negative.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `hotel.eu.turnover.accommodation.monthly` | **quiet** | - | - | 100 | lt | 2023-01 → 2026-07 |

- `hotel.eu.turnover.accommodation.monthly` · source_used=`eurostat-api` · 观测 43 期 · verdict=quiet
  - 未触发；最近点 2023-02 = 190.6，距阈值 90.6 index_2021_100

### 规则 `hotel_revpar_yoy_decline_us`（asof=2026-11-08, replay_from=2023-01-01）

- 证据文件: `reports/hotel-alerts/2026-11-08/threshold-hotel_revpar_yoy_decline_us.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `hotel_revpar_yoy_placeholder` | **n/a** | - | - | 0 | lt | None → None |

- `hotel_revpar_yoy_placeholder` · source_used=`sec-edgar-8k-operating` · 观测 0 期 · verdict=n/a · insufficient_history: 观测 0 期 < min_history=1（判 n/a，不编阈值）

### 规则 `star_hotel_adr_yoy_drop`（asof=2026-11-08, replay_from=2023-01-01）

- 证据文件: `reports/hotel-alerts/2026-11-08/threshold-star_hotel_adr_yoy_drop.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `hotel.cn.star_rated.adr_yoy.all.quarterly` | **quiet** | - | - | -3 | lt | 2023-06-30 → 2024-09-30 |

- `hotel.cn.star_rated.adr_yoy.all.quarterly` · source_used=`mct-star-hotel` · 观测 3 期 · verdict=quiet
  - 未触发；最近点 2024-09-30 = -2.2198，距阈值 0.780201 percent

### 规则 `star_hotel_occupancy_yoy_negative`（asof=2026-11-08, replay_from=2023-01-01）

- 证据文件: `reports/hotel-alerts/2026-11-08/threshold-star_hotel_occupancy_yoy_negative.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `hotel.cn.star_rated.occupancy_yoy.all.quarterly` | **fired** | 2024-09-30 | -3.58 | 0 | lt | 2023-06-30 → 2024-09-30 |

- `hotel.cn.star_rated.occupancy_yoy.all.quarterly` · source_used=`mct-star-hotel` · 观测 5 期 · verdict=fired

### 规则 `star_hotel_revpar_yoy_negative`（asof=2026-11-08, replay_from=2023-01-01）

- 证据文件: `reports/hotel-alerts/2026-11-08/threshold-star_hotel_revpar_yoy_negative.json`

| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |
|---|---|---|---|---|---|---|
| `hotel.cn.star_rated.revpar_yoy.all.quarterly` | **fired** | 2024-09-30 | -7.71997 | 0 | lt | 2023-06-30 → 2024-09-30 |

- `hotel.cn.star_rated.revpar_yoy.all.quarterly` · source_used=`mct-star-hotel` · 观测 3 期 · verdict=fired

## 4. 事件级（财报日历 / 盈警 / 抽检不合格）

- 扫描总数: 237 · 分类: `{"financial_calendar": 208, "profit_warning": 4, "samr_unqualified_restaurant": 25}`
- 台账已有: 237 · 本次首报: **0** · 已见跳过: 237

首报事件（每条可回指原始公告）：

| kind | date | 自然键 | title | 原件 |
|---|---|---|---|---|

## 5. 缺测归因（never_held / gap_in_series / not_applicable）

口径：只统计阈值证据文件里的缺测条目；不插值、不补零。

| rule | concept | never_held | gap_in_series | not_applicable | 说明 |
|---|---|---|---|---|---|
| star_hotel_adr_yoy_drop | `hotel.cn.star_rated.adr_yoy.all.quarterly` | 0 | 456 | 0 | never_held 区间 2023-01-01..2023-06-29（replay 早于首观测） |
| star_hotel_occupancy_yoy_negative | `hotel.cn.star_rated.occupancy_yoy.all.quarterly` | 0 | 454 | 0 | never_held 区间 2023-01-01..2023-06-29（replay 早于首观测） |
| star_hotel_revpar_yoy_negative | `hotel.cn.star_rated.revpar_yoy.all.quarterly` | 0 | 456 | 0 | never_held 区间 2023-01-01..2023-06-29（replay 早于首观测） |

明细（前 60 条，完整列表在各阈值证据文件的 `gaps` 字段）：

- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-01 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-02 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-03 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-04 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-05 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-06 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-07 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-08 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-09 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-10 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-11 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-12 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-13 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-14 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-15 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-16 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-17 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-18 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-19 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-20 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-21 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-22 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-23 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-24 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-25 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-26 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-27 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-28 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-29 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-30 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-07-31 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-01 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-02 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-03 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-04 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-05 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-06 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-07 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-08 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-09 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-10 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-11 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-12 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-13 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-14 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-15 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-16 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-17 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-18 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-19 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-20 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-21 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-22 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-23 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-24 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-25 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-26 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-27 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-28 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）
- `hotel.cn.star_rated.adr_yoy.all.quarterly` 2023-08-29 → **gap_in_series** · 窗口内该期无观测（真实缺测，未插值）；可能为交易所休市，配置未提供节假日日历（2023Q3 详情页无 xlsx、2023Q1/2022Q4 解析表为空 —— gap_in_series，不插值不跨期补位）

## 6. Escalate 路径

本次未执行 escalate —— 本线当前**没有未终态健康工单**（三源在 inspector 扫面内判健康，陈旧判定归发布节奏级 6.4/6.5，见 `reports/hotel-cadence/`）。

escalate 实现复用 `fd_industry_data.health.inspector.submit_new_tickets`（与 `scripts/health_inspect.py --deliver --submit-new` 同一实现）：需要 `WANXING_SPIDER_HEAL_KEY` 与已落盘的工单目录；缺任一条件时如实记 blocked，不伪造投递。


## 7. 审计回指（文件 → 哈希）

| 文件 | sha256[:16] |
|---|---|
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/2026-11-08/threshold-eu_accommodation_turnover_yoy_negative.json` | d8e2c0789fb0f2a4 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/2026-11-08/threshold-hotel_revpar_yoy_decline_us.json` | d96ebccc48f5ab7c |
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/2026-11-08/threshold-star_hotel_adr_yoy_drop.json` | 0c95a13d114351d8 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/2026-11-08/threshold-star_hotel_occupancy_yoy_negative.json` | fa597d44101a161a |
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/2026-11-08/threshold-star_hotel_revpar_yoy_negative.json` | 30f7744f8e0d9800 |
| `/Users/chengsishi/finddata/fd-industry-data/reports/hotel-alerts/state/events.jsonl` | 740f73adf5ac5050 |

回指链：本档案 §2 工单文件 → 工单 `evidence.failing_runs` 行 → 快照 JSON 原始行（source/status/started_at/error）；§3 证据文件 → `semantic_observations`（concept + source_used + date）→ 上游源单元。
