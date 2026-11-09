# 分桶全表（2026-11-09 复算）

任务 1.2 的产物：五桶清单落档，数能被重建。原始数据见同目录 `min-rows-buckets-2026-11-09.csv`
（列：`source,n,zero,p10,p50,bucket`，纯逗号分隔、无表头）。

## 复算入口

```bash
dashboards/sql/fd_open_data.sh < docs/min-rows-coverage-baseline.md §5 的 SQL
```

口径：近 90 天 `status='success'` 的 `crawl_runs`，按源统计成功次数 `n`、零行次数 `zero`、
`rows_written` 的 p10/p50；分桶规则见 baseline §5 的 `case when`。

## 本次结果

| 桶 | 源数 | 含义 |
|---|---|---|
| A_可声明 | 40 | 90 天内从未零行 |
| B_多数有数 | 9 | 零行占比 <20% |
| C_混合 | 3 | 零行占比 20%–80% |
| D_样本不足 | 67 | 成功次数 <5，不凭猜声明 |
| E_常态0行 | 14 | 零行占比 ≥80%，增量型，不声明行数下限 |
| **合计** | **133** | |

## 与声明的对账

- A 桶 40 个 = 已声明 38 + 刻意跳过 2（`inmetals`、`dispatch-smoke`），**无遗漏**
- B 桶 9 个 = 已声明 7 + 刻意跳过 2（`metal-com` 停用、`rmfyalk-case-crawl` 源名与目录不对应）
- D/E/C 桶本轮不声明（任务 4.x / 5.x）

## 说明

`bucket` 列是 SQL 算出来的，不是我贴的标签；改口径只需重跑那条 SQL，
所以这份表可复核（spec 的的「判定依据必须可复算」）。
