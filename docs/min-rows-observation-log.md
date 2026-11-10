# 行数下限 · 一周误报率观察表

每天巡检后追加一行。判读口径见 `scripts/min_rows_observation.py` 的文档字符串。

**读表要点**：出 rule1c 单且**超窗天数 = 0** 的源，才是"跑了但产出少"的候选误报；
超窗 > 0 的源是"压根没跑"，真因在调度/抓取，不是阈值。

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
`Failed` / `0/1` / 压根没有新 Job，三种都算**未恢复**。不看 Pod 是否还活着（它有 TTL，
会被清掉），也不看 `lastScheduleTime`——那只证明"触发过"，不证明跑成（`boxofficemojo`
11-10 就是触发过但 Failed 的实例）。

```bash
kubectl --context tencent get job -n fd-industry \
  -l fd-industry/source=gb-standards --sort-by=.metadata.creationTimestamp | tail -2
```

**当前实测（2026-11-10 22:49 CST，距窗口 27.6 小时）**：最新 Job 仍是
`29896945 Complete 1/1`（11-05 02:25 CST），`lastScheduleTime=2026-11-04T18:25Z`。
**窗口未到，尚未核对，不预填结论。**

**这个源有个检测盲区，所以必须显式核 Job、不能等巡检报**：manifest 写的是
`data_period: month`（容忍窗 840h = 35 天），实际排期却是每周四。一次周跑失败时，
巡检既不会出 rule1c 单（没有"行数不足的成功运行"可判），也不会报超期（离 35 天还远）
——11-12 03:17 那次巡检即便 gb-standards 挂了，报告面也是全绿的。

| 日期 | healthy | rule1c 单数 | 出单源 | 超窗天数（源:天） | 容忍窗 |
|---|---|---|---|---|---|
| 2026-11-10 | 118 | 3 | `boxofficemojo`, `cisa`, `gb-standards` | — | month(840h) |
