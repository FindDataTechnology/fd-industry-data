# 观察基线（2026-11-09 快照）

任务 3.2 要求「观察一周、统计误报率、>20% 回退」。**基线必须在观察开始前记下来**——
一周后再去还原「当时每个源处于什么状态」要重查，而窗口已经变了。

数据来源：`crawl_runs` 近 30 天（3406 条 / 129 源），每个声明源取最近一次
`success`/`partial` 的行数。回归判据用 `scripts/min_rows_false_positive_report.py`。

## 观察开始时已知低于下限的源

| source | 最近成功行数 | 下限 | 成因 |
|---|---|---|---|
| `boxofficemojo` | 20 | 25 | 2026-11-06 批量小 limit（48 源各一次、恰好 20 行、`image_tag=unknown`） |
| `cisa` | 20 | 25 | 2026-11-06 批量小 limit（48 源各一次、恰好 20 行、`image_tag=unknown`） |
| `gb-standards` | 20 | 30 | 2026-11-06 批量小 limit（48 源各一次、恰好 20 行、`image_tag=unknown`） |

预期：这三个源下次正常调度后回到下限之上，工单自愈。**若一周后仍未恢复，说明不是波次而是真问题**。

## 全量基线（45 个声明源）

| source | 下限 | p10 | 窗口内成功数 | 最近成功行数 | 最近成功日 |
|---|---|---|---|---|---|
| `boxofficemojo` ⚠ | 25 | 60 | 5 | 20 | 2026-11-06 |
| `cisa` ⚠ | 25 | 60 | 4 | 20 | 2026-11-06 |
| `gb-standards` ⚠ | 30 | 68 | 5 | 20 | 2026-11-06 |
| `amis-market` | 20 | 100 | 4 | 100 | 2026-11-03 |
| `bls` | 1 | 1 | 31 | 1 | 2026-11-08 |
| `carbon-monitor` | 20 | 100 | 31 | 100 | 2026-11-08 |
| `chinawuliu-logistics` | 10 | 27 | 5 | 20 | 2026-11-06 |
| `cninfo-operating` | 5 | 5 | 6 | 100 | 2026-11-07 |
| `cpcaauto-monthly` | 30 | 30 | 30 | 32 | 2026-11-08 |
| `eastmoney-data` | 2 | 2 | 45 | 2 | 2026-11-09 |
| `eastmoney-futures` | 1 | 1 | 29 | 1 | 2026-11-09 |
| `eia-v2` | 20 | 100 | 663 | 100 | 2026-11-09 |
| `elexon-bmrs` | 15 | 32 | 30 | 32 | 2026-11-08 |
| `entsoe-e` | 20 | 100 | 26 | 100 | 2026-11-08 |
| `eurostat-api` | 10 | 18 | 9 | 319 | 2026-11-07 |
| `fangchan-market` | 79 | 79 | 31 | 79 | 2026-11-08 |
| `fao-empres` | 20 | 46 | 5 | 20 | 2026-11-06 |
| `fao-food-price` | 15 | 38 | 9 | 2784 | 2026-11-07 |
| `gdacs` | 3 | 6 | 23 | 10 | 2026-11-02 |
| `hkex-disclosure` | 50 | 221 | 9 | 226 | 2026-11-07 |
| `imf-pcps` | 30 | 68 | 6 | 417 | 2026-11-07 |
| `jodi-gas` | 25 | 52 | 3 | 100 | 2026-11-08 |
| `kitco` | 1 | 1 | 52 | 1 | 2026-11-09 |
| `moa-hog-price` | 12 | 28 | 6 | 20 | 2026-11-06 |
| `mohurd-yearbook` | 7 | 14 | 7 | 20 | 2026-11-06 |
| `nasa-firms` | 20 | 100 | 25 | 100 | 2026-11-03 |
| `nasa-power` | 20 | 100 | 31 | 100 | 2026-11-08 |
| `nbs-stats` | 18 | 18 | 7 | 1029 | 2026-11-07 |
| `nmc-weather` | 20 | 100 | 716 | 100 | 2026-11-09 |
| `noaa-ncei` | 10 | 27 | 30 | 28 | 2026-11-08 |
| `noaa-swpc` | 4 | 8 | 31 | 8 | 2026-11-08 |
| `open-meteo` | 20 | 100 | 32 | 100 | 2026-11-08 |
| `openchargemap` | 20 | 100 | 4 | 100 | 2026-11-03 |
| `portwatch-imf` | 1 | 0 | 31 | 100 | 2026-11-08 |
| `sec-edgar-facts` | 150 | 748 | 5 | 835 | 2026-11-07 |
| `socrata-chicago` | 20 | 100 | 3 | 100 | 2026-11-01 |
| `socrata-ny` | 20 | 100 | 6 | 100 | 2026-11-02 |
| `steam-ccu` | 5 | 11 | 12 | 12 | 2026-10-21 |
| `un-comtrade` | 2 | 4 | 13 | 5 | 2026-11-03 |
| `usda-quickstats` | 6 | 12 | 7 | 423 | 2026-11-07 |
| `usgs-nwis` | 20 | 100 | 31 | 100 | 2026-11-09 |
| `wire-finddatatech-cloud` | 1 | 1 | 31 | 1 | 2026-11-08 |
| `worldbank-procnotices` | 20 | 100 | 31 | 100 | 2026-11-09 |
| `wto-eping` | 5 | 10 | 31 | 10 | 2026-11-09 |
| `zgdypf-boxoffice` | 20 | 100 | 31 | 100 | 2026-11-08 |

⚠ = 基线时低于下限。

## 2026-11-10 追加：7 个 B 桶源的声明已撤

按 `docs/min-rows-calibration-analysis.md` 的反算，这 7 个源有合法零行质量，
任何正的行数下限都会按该比例出单，属**结构性不可声明**，声明已从 manifest 撤除：

| source | 零行占比 |
|---|---|
| `cninfo-operating` | 17% |
| `portwatch-imf` | 15% |
| `nbs-stats` | 10% |
| `cpcaauto-monthly` / `eastmoney-futures` | 3% |
| `eastmoney-data` / `fangchan-market` | 3% / 3% |

所以**下文表格里这 7 行的「下限」列已失效**（它们不再有声明的下限，判据改由周期缺失承担）。
观察期末复核时，这 7 个源应只统计"是否出了行数不足单"——预期为 0，若非 0 说明撤除没生效。

## 一周后怎么判

1. 跑 `python3 scripts/min_rows_false_positive_report.py --days 7 --from-pg`；
2. 看每个源「最后一张单之后是否又达标」——**>0 不等于误报**（可能真低产后修复，或上游恢复）；
3. 只有「出单 → 立即恢复 → 再出单」这种反复，才是阈值太敏感的实证；
4. 误报率 >20% 的源回退：删掉 manifest 里的 `min_rows_per_run` 行，再跑
   `python3 scripts/validate_manifests.py` 复核。

贴边项优先看：`eastmoney-data` 2/2、`eastmoney-futures` 1/1、`fangchan-market` 79/79。
