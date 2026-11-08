# 告警样本盘点（2026-11-08）

> openspec change `wire-alert-delivery` 任务 6.1 的实证记录。
> 口径：**只写库里真实跑出来的判定**，不用构造样本充数；"无样本"如实写"无样本"。
> 复算入口见每节末尾。

## 总表

| 线 | 启用规则 | 已判 | **当期**越阈 | 历史越阈(stale) | 缺数(n/a) | 停用(带原因) | 真实送达 | 排程 |
|---|---|---|---|---|---|---|---|---|
| 银行 | 6 | 6 | 0 | 0 | 0 | 3 | 待首次 | ✅ 14:05 UTC |
| 农业 | 2 | 2 | 0 | 0 | 0 | 2 | 待首次 | ✅ 18:17 CST |
| 花卉 | 4 | 4 | **0** | 2（期次 131d / 677d） | 0 | 0 | 待首次 | ✅ 01:05 UTC |
| 餐饮 | 3 | 3 | **0** | 4（触发期 2022-12 / 2024-11 / 2026-03 / 2026-08） | 0 | 0 | ✅ 已发 4 封（**属误报，已修**） | ✅ 19:47 CST |
| 酒店 | 5 | 1 | 0 | 0 | 4 | 0 | 无 | ✅ 20:17 CST |

"越阈"按点计（一条规则可对多个实体/概念越阈），与"条规则"不同数量级。

## 银行（`config/banking-alerts.json`）

- 覆盖：`coverage.covered=42 / universe=42`（104 个判定行 = 6 规则 × 多银行，部分银行缺项）
- 6 条启用规则当期全部 **quiet**：`bank-capital-adequacy` / `bank-tier1-adequacy` /
  `bank-cet1-adequacy` / `bank-leverage-ratio` / `bank-lcr` / `bank-nsfr`
- 3 条停用且写明原因：拨备覆盖率与贷款拨备率是**区间**（120%–150% / 1.5%–2.5%）+ 联动条件，
  单侧判会误报；另一条缺 `financials.*` 概念
- **真实越阈样本：无**（当期为 quiet）。首次真实触发后应回填本期。
- 复算：`python3 scripts/banking_alerts.py --dry-run --today <date>` →
  `reports/banking-alerts/<date>/rules-banking.json`

## 农业（`config/agriculture-alerts.json`）

- 已判 2 条，均 quiet 且有真实读数：
  - `breeding_sow_vs_normal_inventory_band` = 96.0 percent（2026Q3）
  - `soybean_meal_settle_percentile_spike` = 3101.0 cny_per_ton（2026-11-06）
- 停用 2 条并留痕：`cpi_food_vs_wholesale_scissors`（库里只有总体 CPI，无食品 CPI 分项）、
  `hog_grain_ratio_official`（NDRC 官方猪粮比价不可得）
- 事件级四类（业绩预告 / 动物疫病 / 产区灾害 / 收储与进口配额）均 `NO-SAMPLE`，原因与解锁条件逐条写明
- **真实越阈样本：无**。
- 复算：`python3 scripts/agriculture_alerts.py --rules --events --dry-run` →
  `reports/agriculture-alerts/rules-<date>.json`

## 花卉（`config/flower-alerts.json`）

- 4 条全部可判，2 条 crossed，但**两条的期次都很旧**：

| 规则 | verdict | 期次 | 期次年龄 | action |
|---|---|---|---|---|
| `adjacent_operating_margin_below` | crossed | 2026-06-30 | ≈131 天 | suppress |
| `eu_flower_area_yoy_negative` | crossed | 2024 | ≈677 天 | suppress |
| `dounan_rose_avg_wow_drop` | quiet | 2026-11-02 | ≈6 天 | idle |
| `hs0603_export_yoy_negative` | quiet | 2025 | ≈312 天 | idle |

- 与餐饮不同，**花卉的判据本身是对的**：`eval_change_rule` 只比「最后两期」，
  所以 `crossed` 就是"最新可用期次"的判定，不存在餐饮那种 96 月窗口的旧点问题。
- 但 **`action=suppress`**：状态机里两条自 2026-11-07 起就是 `firing`，
  按边缘触发语义本轮不再通知（当时还没有邮件通道，所以历史通知实际未发出）。
- **期次年龄值得注意**：花卉只在 `kind=ratio` 上实现了 `max_period_age_days` 时效闸，
  `kind=change` 没有。所以年频源（`eu_flower_area_yoy_negative` 的 2024 年报）
  会一直以"最新可用期次"身份参与判定——这在年频源上是合理的，但**读数只能说明 2024 年**，
  不能当成当期信号。
- **真实越阈样本：形式上 2 条，但一条 131 天、一条 677 天**。归档口径按"有样本"记，
  使用时必须带期次。
- 复算：`python3 scripts/flower_alerts.py --rules` → `reports/flower-alerts/rules-<date>.json`

## 餐饮（`config/restaurant-alerts.json`）

- 3 条规则，**4 个越阈点**：`catering_yoy_negative`、`cpi_scissors`、`price_spike`（两点）
- **真实送达已实证**：2026-11-08 一次真实运行 `sent=4 failed=0`；随后第二次运行
  `sent=0 alerts=[] recovers=[]`（同一集合不重发）
- 复算：`python3 scripts/restaurant_alerts.py --rules --dry-run`

## 酒店（`config/hotel-alerts.json`）

- 5 条规则，**4 条判 n/a**（无可用历史）：
  `star_hotel_occupancy_yoy_negative`、`star_hotel_adr_yoy_drop`、
  `star_hotel_revpar_yoy_negative`、`hotel_revpar_yoy_decline_us`
- 仅 `eu_accommodation_turnover_yoy_negative` 可判，当期 quiet
- 回执 `coverage = {rules: 5, evaluable: 1, unknown: 4, mute: false}`
- **真实越阈样本：无**；且**结构性受限**——四条星级饭店指标依赖的数据在挂账里已记为
  停更（D10）或字段缺位（E2），短期不可能产生样本
- 复算：`python3 scripts/restaurant_alerts.py --config config/hotel-alerts.json --rules --dry-run`

## 结论（归档前必须认可的现状）

1. **"越阈样本"要连期次一起看**（2026-11-08 更正）：
   - 餐饮原有 4 条"fired"**经复核是历史越阈**（触发期 2022-12 / 2024-11），
     根因是 96 个月回放窗 + "窗口内曾越阈即 fired"。判据已改为 `latest_period`，
     改后餐饮**当期越阈 0 条**、`stale=4`。
   - 花卉 2 条 crossed 的期次分别是 131 天与 677 天前，判据本身正确（只比最后两期），
     但读数只能说明那个期次。
   - 结论：**目前没有任何一条线存在"当期越阈"**。先前"花卉 2 条真实越阈"的说法
     应改为"2 条历史越阈，期次已标注"。
2. **酒店线短期不可能有样本**：4/5 规则的数据源已记为停更/字段缺位。这不是告警实现问题，
   是数据面欠账；`coverage.mute` 会在全哑时显性报出。
3. **停用规则共 6 条**（银行 3、农业 2、酒店另有 1 条占位概念），全部带 `blocked_reason`，
   不存在"原因空着"的条目。
