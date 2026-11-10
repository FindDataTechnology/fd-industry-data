# 行数下限 · 一周误报率观察表

每天巡检后追加一行。判读口径见 `scripts/min_rows_observation.py` 的文档字符串。

**读表要点**：出 rule1c 单且**超窗天数 = 0** 的源，才是"跑了但产出少"的候选误报；
超窗 > 0 的源是"压根没跑"，真因在调度/抓取，不是阈值。

**行只由带遥测的运行写入**：脚本在没有 `--from-pg` / `--snapshot` 时**拒绝追加**（rc=2），
因为空记录只能写出一行 `healthy=—`、`rule1c=0` 的「全绿假行」——比不写更糟。
所以某天缺行 = 当天没有带遥测的巡检成功写入，不等于"当天零误报"。

**2026-11-10 人工订正**：当日行原记 `0`，实为 **3**。原因是工单文件名用 **UTC** 日期
（`health_inspect.py:87` 的 `now` 是 UTC），而 `--day` 用 **CST**——00:00–08:00 CST 生成的单
带的是前一天的前缀，按前缀筛选会整天漏掉。当日的三张单 `created_at=2026-11-09T22:30Z`
（CST 11-10 06:30），文件名一律 `20261109-*`，于是被漏掉。
`scripts/min_rows_observation.py` 已改为按 `created_at` 的 CST 日期判，并加了反向验证用例。

## 三源恢复跟踪（每轮巡检核对一次）

暂撤声明的三个源，恢复条件 = **在自己的排期上真跑成功过**，不是"日期到了"。
本轮核对：**2026-11-10 22:25 CST**，证据取自 tencent 集群 CronJob/Job 状态（只读），
不是照排期表推算。

| source | 本轮状态 | 最近一次 Job | 下次排期 | 判定 |
|---|---|---|---|---|
| `boxofficemojo` | **触发了但失败** | `29904194` Failed（`BackoffLimitExceeded`，11-10 03:14 CST） | 11-17 03:14（周二） | 未恢复 |
| `cisa` | 未触发 | `29896889` Complete（11-05 01:29） | 11-15 01:29（cron 5,15,25） | 未恢复 |
| `gb-standards` | 未触发 | `29896945` Complete（11-05 02:25） | 11-12 02:25（周四） | 未恢复 |

**`boxofficemojo` 这条是真故障，不是"还没到日子"**：11-10 03:14 的 Job 重试到
`backoffLimit` 才判失败；Pod 与日志已随历史清理（`failedJobsHistoryLimit=3`）丢弃，
现在查不到失败原因，只能确认「触发过、没成功」。下次窗口 11-17。

三个源都还没恢复重跑，所以 `restore_min_rows.py --require-fresh-run` 会继续拒绝恢复——
这是它该做的，不是它坏了。下一轮巡检由 workflow 自动跑（每日 03:17 CST）并追加当日行。

### 待核对窗口：gb-standards 2026-11-12 02:25 CST

**判据取严的一侧**：窗口之后新建的最新 Job 必须是 `Complete 1/1`。
四种非通过态分开报，因为处置完全不同：

| 判定 | 含义 | 算不算红 |
|---|---|---|
| `pending` | 窗口还没到 | **不算**（提前报红＝天天误报） |
| `no_new_job` | 窗口过了却没有新 Job —— 调度没触发 | 红 |
| `failed` | Job 在但 Failed —— 跑过没成（`boxofficemojo` 11-10 即此态） | 红 |
| `incomplete` | Job 在但没到 1/1 也没 Failed —— 还在跑或卡在中间态 | 红 |

**不看 Pod**（有 TTL 会被清掉——`boxofficemojo` 那个失败 Job 的日志就这么丢的），
也**不看 `lastScheduleTime`**（只证明"触发过"，不证明跑成）。

这一步已落成脚本，一条命令，退出码非零即红：

```bash
python3 scripts/check_withdrawn_sources.py          # 加 --json 出机器可读
```

```
source           判定           窗口(CST)          说明
boxofficemojo    …pending     11-17 03:14      窗口 11-17 03:14 CST 未到
cisa             …pending     11-15 01:29      窗口 11-15 01:29 CST 未到
gb-standards     ✗no_new_job  11-12 02:25      窗口后无新 Job（最新是 11-05 02:25 complete）
```

上面第二块是**把 now 推到 11-13 用今天的真实 Job 数据做的演练**——`gb-standards`
立刻标红并带出"最新是 11-05"，证明红区不是死的。真实时点（11-10 23:25）三者都是
`pending`，退出码 0。

**当前实测（2026-11-10 23:25 CST，距 gb-standards 窗口 27.0 小时）**：最新 Job 仍是
`29896945 Complete 1/1`（11-05 02:25 CST），`lastScheduleTime=2026-11-04T18:25Z`。
**窗口未到，尚未核对，不预填结论。**

**这个源有个检测盲区，所以必须显式核 Job、不能等巡检报**：manifest 写的是
`data_period: month`（容忍窗 840h = 35 天），实际排期却是每周四。一次周跑失败时，
巡检既不会出 rule1c 单（没有"行数不足的成功运行"可判），也不会报超期（离 35 天还远）
——11-12 03:17 那次巡检即便 gb-standards 挂了，报告面也是全绿的。

### 待核对窗口：nasa-firms 2026-11-11 06:07 CST

**必须先拆一个口径，否则会误判**：`106,838` 是**快照总行数**，不是**落库行数**。
CronJob 的 args 是 `nasa-firms --limit 100 --window-auto`，而 `run_nasa_firms` 在
`len(rows) >= limit` 时直接 break（`spider.py:186`）——所以这次跑**落库约 100 行**，
拿 106,838 去对 `crawl_runs.rows` 会得出"跑批失败"的错误结论。

**决定记录（口径取严且可验证的一侧）**：

| 观测量 | 判据 | 在哪看 |
|---|---|---|
| 落库行数 | ≈100 且 **≥ `min_rows_per_run` 20** | `crawl_runs.rows` |
| 运行结论 | `success`（失败会写 `status=failed`） | `crawl_runs.status` |
| 快照新鲜度 | 最新 `acq_date` 距跑批 ≤ **2 天**（`MAX_SNAPSHOT_AGE_DAYS`） | 由 spider 内部把关，跑成功即已通过 |
| 快照总行数 | 只作旁证，**不作判据** | 直连上游才有 |

**窗口前实测（2026-11-10 23:2x CST，直连 N20/J1 24h CSV）**：**106,838 行**，
最新 `acq_date=2026-11-10`，陈旧 **0 天**——即修复后的平台阶梯能选中 N20 并拿到当日数据。
这是**基线**，不是跑批结果：11-11 06:07 那次 Job 目前**尚未触发**（距窗口 6.7 小时）。

| 日期 | healthy | rule1c 单数 | 出单源 | 超窗天数（源:天） | 容忍窗 |
|---|---|---|---|---|---|
| 2026-11-10 | 118 | 3 | `boxofficemojo`, `cisa`, `gb-standards` | — | month(840h) |
