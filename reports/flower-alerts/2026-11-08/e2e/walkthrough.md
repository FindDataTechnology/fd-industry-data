# 花卉线端到端走查（add-flower-vertical 7.3）

**走查时刻**：本地 2026-11-08T04:17+0800 / UTC 2026-11-07T20:17Z（下文 as-of 一律 UTC）
**目标**：一条真链走通 —— 源 → 观测 → 板 → 预警，每个环节都报真数，能追到库。

复现命令（先 `source ~/.config/finddata/dashboards.env`）：

```bash
cd fd-industry-data
.venv/bin/python scripts/flower_alerts.py --publication --rules --events --dry-run \
  --outdir reports/flower-alerts/2026-11-08/e2e     # 不写状态、不落台账
cd ../dashboards && ../fd-industry-data/.venv/bin/python build_all_dashboards.py --only flower  # 或逐 build_flower_*_spec.py
```

`--dry-run` 的退出码 1 表示「有规则越阈」，是预期行为，不是失败。

---

## 1. 源（`crawl_runs` 实读）

| 源 | 运行次数 | 成功 | 写入行 | 最近运行（UTC） |
|---|---|---|---|---|
| `dounan-flower-price` | 1 | 1 | 932 | 2026-11-07T17:50:10 |
| `aiph-stats` | 1 | 1 | 81 | 2026-11-07T17:50:29 |
| `sec-edgar-facts` | 5 | 5 | 6,714 | 2026-11-07T18:38:22 |

两个新单元是 2026-11-07 的**手动试跑**（`schedule` 已写进 manifest，但 manifest 尚未进 git/集群，故集群侧还没有 CronJob；见挂账 §4）。

## 2. 观测（`semantic_observations` 实读）

| `source_used` | 点数 | 概念数 | 期次范围 |
|---|---|---|---|
| `dounan-flower-price` | 2,796 | 3 | 2026-09-09 → 2026-11-05 |
| `aiph-stats` | 81 | 9 | 2005-01-01 → 2024-01-01（年频截面，逐行自带参考年） |
| `sec-edgar-facts` | 1,125 | 3 | 2018-04-01 → 2026-09-30 |

斗南 932 行 × 3 概念（最高/均价/最低）= 2,796 点，与行数自洽。

## 3. 板（`dashboards/*.spec.json` 实读，`data_plane` 块）

| 板 | generated_at | as_of_max | 有数序列 | 空序列 | 点数 | 图 |
|---|---|---|---|---|---|---|
| 价格与供给 | 2026-11-08 | 2026-11-05 | 37 | 0 | 570 | 6 |
| 全球贸易链 | 2026-11-08 | （无） | 0 | 4 | 0 | 4 |
| 公司财务 | 2026-11-08 | 2026-11-08 | 9 | 1 | 287 | 6 |

- 价格板第 6 图（3.3 新增）：**EU 花卉主面积**，8 国 × 10 年 = **77 点**（Eurostat `apro_cpsh1` / `N0000` / 主面积），EU 合计 2021 80,700 → 2024 **66,510 公顷**，荷兰 2024 34,850 公顷为最大产地国。
- 价格板 KPI：斗南红玫瑰（单头）均价 **0.27 元/枝**（截至 2026-11-05）；AIPH 玫瑰最大种植国厄瓜多尔 **5,480 公顷**（参考年 2024，截面非时序）。
- 财务板：纯花卉 2 序列、邻接美元 4 家、邻接人民币 002200、占比 2 家、**分部 0 条**（空图带原因）、事件 495 条申报。
- 贸易板**零数据是口径事实**：`un-comtrade` 调度只跑默认 `cmd_code=854142`，花卉三档（060311/060319/060120）未进参数。KPI 显示「无数据」并给出解锁动作，4 张图各带 `无数据 · <原因>` 占位。

## 4. 预警（同一时刻实跑）

```
dounan-flower-price: window_learning  水位=2026-11-05  滞后=未学出  样本=1
· dounan_rose_avg_wow_drop: quiet 读数=4.3675 percent 期=2026-11-02
🔴 adjacent_operating_margin_below: crossed 读数=-89.493 percent 期=2026-06-30 [EDBL]
⊘ hs0603_export_yoy_negative: 未启用（未接花卉三档）
⊘ eu_flower_production_gap: 未启用（eurostat 未接 apro_cpsh1）
events（dry-run）：扫到 28 条，不落账
```

- 发布节奏级：**窗口学习中**（带水位成功样本 1 < 5）→ 正确地**不判**「未发布」，冷启动不出假 SLA。
- 逐家营业利润率：FLWS −27.14% / CENT +14.26% / SMG +14.47% / **EDBL −89.49%**（分母 355 万美元）/ 002200 +9.16%；HYFM `⊘ 距今 403 天`（营收序列停更）。
- 事件级：90 天窗 **28 个申报**（含 accession 去重；同日两份 8-K 计两件）。

## 5. 走查中修掉的两处真缺陷

1. **空图无占位**：`data_plane.no_data` 的 docstring 承诺「板面出『无数据（原因）』」但渲染器只画空坐标轴 → 公网板上 5 张空图（贸易板 4 + 分部 1）分不清「没数」与「加载失败」。修法：`industry_common.empty_state()` 出 ECharts `graphic` 占位，两个 builder 的空分支挂上；回归测试 `dashboards/tests/test_board_empty_state.py`（产物级守卫：公开 spec 里任何空 series 图必须带「无数据」）。
2. **事件数虚高一倍**：`crawl_items` 逐次运行追加，同一份申报被写 2 行 → 「56 条」实为 **28 个**。修法：`scan_events` 先按业务键收敛；看板去重键补 `accession`（488 → 495，同日两份 8-K 是真两份）。两处口径现已一致。

## 6. 未在走查中覆盖的

- 集群侧真实按点触发（CronJob）——manifest 未进 git，等 2026-11-09T00:15Z 首轮。
- 注册表/MCP 工具面的 live readback——`cheap` kubectl context 已死（见挂账）。
