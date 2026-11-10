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

## 观测项登记：三条结构不变量的误报风险（2026-11-11）

`tests/test_health_workflow_summary.py` 里有三条不变量，钉的是"改坏了也全绿"的结构点
（带 `--from-pg`、排在落库门之前、落库门确实 `git add` 了本文件）。它们是**文本/结构
断言**，所以合法重构会让它们误报。**误报不是噪音，是要按下面换判据的信号**：

| 观测项 | 什么情况算误报（结构变了但行为没坏） | 换成什么更严/更贴行为的判据 |
|---|---|---|
| 观察步骤带 `--from-pg` | flag 被挪进包装脚本（`scripts/run_obs.sh`）或改成从 env 读 | 跑一次脚本：无遥测时 `rc=2`、有遥测时不返回 2；即**行为断言**替代文本断言 |
| 观察步骤排在落库门之前 | 两步合并成一个 step，或顺序改由别的机制保证 | 断言"落库门的 `run` 里包含观察脚本调用"**或**"观察步索引 < 落库门索引"（二者其一即可） |
| 落库门 `git add` 本文件 | 改成目录级/`git add -A` 之类的批量 add | 断言该文件**被 staged**（`git diff --cached --name-only` 含它），而不是断言命令行里出现文件名 |

**处置口径（取严的一侧）**：换成行为断言，不要直接把断言删掉。删掉等于放弃这条不变量，
而下一次真坏时就没人拦。

**本周观察什么**：这三条用例自 2026-11-11 起生效，第一次误报出现在哪一条、当时是哪种
重构——若三周内零误报，说明当初选文本断言是对的；若误报 ≥2 次，改成行为断言并在此记一笔。

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

### 窗口核对自动化：三案对比（待裁决，未实施）

现在的状态是：**脚本有了，但要人/agent 在窗口后手动跑一次**。本节把"让它自己跑"的三条路
摆齐，含最小改动清单与风险，供裁决后实施。三案判据完全一致（`check_withdrawn_sources.py`
的四态），差别只在**谁来跑、凭什么权限、结果落在哪**。

#### 甲案 · 跨集群只读 RBAC（GHA runner 直接读 tencent）

| 项 | 内容 |
|---|---|
| 最小改动 | ① tencent/fd-industry 建 SA `fd-health-jobcheck` + Role（**仅** `get,list` on `jobs`）+ RoleBinding；② 用 TokenRequest 铸一枚长效 token 并落成 GHA secret `FD_TENCENT_KUBECONFIG`；③ **runner 镜像必须重新出片**——已实测**没有** `kubectl`（见下）；④ workflow 加一步跑 `check_withdrawn_sources.py`；⑤ 脚本加 `--kubeconfig` 入参 |
| 镜像实测（2026-11-10 23:35 CST，只读） | 跑着的 `fd-prod/fd-health-runner`（`ccr.ccs.tencentyun.com/finddata/fd-health-runner:sha-d1028f8`，与 `runner/Dockerfile` 的 `d1028f8` 同源）里：`kubectl` → **NONE**；`psql` → `/usr/bin/psql`；`python3` → `/usr/bin/python3`；`git` → `/usr/bin/git`；`/root/.kube`、`/home/runner/.kube` → **都不存在**。`runner/Dockerfile` 的 apt 列表也印证：`ca-certificates curl git jq libicu72 postgresql-client python3 python3-yaml tzdata`——本来就没装 kubectl |
| 依赖 | cheap→tencent API 网络可达（未验证）；kubectl 进镜像要走一次出片 |
| 优点 | 结果直接进现有 GHA 的 step summary 与运行日志，和别的巡检信号在同一处看 |
| 风险 | **凭据离开集群**：一枚长效 token 存在 GitHub secrets，泄露面比给集群内 SA 大一档（虽只 read jobs）；两集群版本/CA 漂移要维护；**必过的前置是重新出片**（镜像里没有 kubectl，已实测），出片后还得在真 runner 上验一次 `kubectl get jobs` 能通——否则会变成"步骤常红"而不是"源红了" |

#### 乙案 · tencent 上每日 07:00 CronJob 自跑

| 项 | 内容 |
|---|---|
| 最小改动 | ① 同一 namespace 建 SA + Role（`get,list` jobs）+ RoleBinding；② 建 CronJob `fd-health-withdrawn-check`，`schedule: "0 7 * * *"`、`timeZone: Asia/Shanghai`、`backoffLimit: 0`、跑脚本；③ **输出通道（关键，见风险）** |
| 依赖 | 无跨集群凭据；但要一条"红了怎么办"的路 |
| 优点 | **零新增凭据面**：SA 就在本集群本 namespace，读自己的 Job；不碰入网、不碰 runner、不重新出片 |
| 风险 | **它自己不会喊**。Job 红了只是 `kubectl get jobs` 里多一行，没人看等于没做。要真闭环必须接输出：<br>· 乙-1 复用集群里已有的 `fd-industry-gitee-token`（每个爬虫的 init 容器都在用）推一行到本文件 —— 代价是**给仓库加了第二个写入方**，与多会话 git 协定直接冲突（bot 与 CronJob 抢 push）<br>· 乙-2 走 SMTP 邮件（`alerts_core/notify.py`，DirectMail 465）—— 但 `FD_ALERT_SMTP_*` 这套**在集群里不存在**（实测：tencent 与 cheap 两个集群都没有该前缀的 env）。`fd-wire-api` 有一套自己的 `SMTP_*`，属另一服务，复用等于跨服务借凭据<br>· 乙-3 不输出，靠人每天看 —— 那和现在手动跑一次没区别，只是把"记得跑"换成了"记得看" |

#### 丙案 · 用已有遥测代替 Job 状态（零新增凭据）— **推荐**

| 项 | 内容 |
|---|---|
| 做法 | 在**现有** GHA 巡检里给三源各打一行"窗口后最近一次运行"：`started_at` / `status` / `rows`。判据同构：窗口后无记录 → `no_new_job`；有 `failed` → `failed`；有 `running` 或未终态 → `incomplete`；`success/partial` 且 `rows ≥ floor` → `complete` |
| 最小改动 | 只改 `scripts/min_rows_observation.py`（多算三行的窗口态）+ 一行 workflow 无需改（已注入 `FD_CENTRAL_PG_DSN`） |
| 凭据 | **零新增**：用的就是已经在跑的只读账号 `fd_health_ro` |
| 依据 | `writer.py` 是**每次执行写一行** `crawl_runs`（含 `status='failed'`，两阶段还有 `'running'`）。已发生的两个实例都覆盖得到：`nasa-firms` 抛 `RuntimeError` → 写 failed；`boxofficemojo` `BackoffLimitExceeded`（退出码 3）同样是容器跑完后失败 → 写 failed |
| 风险（必须写明） | **盲区是"runner 还没来得及写行就死了"**：拉镜像失败、init 容器 git clone 失败、调度失败——这些在 `crawl_runs` 里**完全没记录**，会显示成 `no_new_job`，与"调度没触发"同形。甲乙两案能区分这一种，丙案不能。<br>缓解：`no_new_job` 时同时报"该源最近一次 `lastScheduleTime` 距今多久"，超过一个周期仍无记录就升级为"疑似根本没起 Pod"，人工看一眼 Job 即可 |
| 结论 | 覆盖已观测到的全部真实失败形态，零凭据、零出片、零新写入方。**建议先上丙**；若后续真出现"Pod 起不来"这类，再按乙案加 Job 层兜底。<br>**落地清单见 [min-rows-withdrawn-states-plan.md](min-rows-withdrawn-states-plan.md)**（逐文件改动、10 条验收断言、回滚点；只写未改） |

**决定记录**：本案涉及"同一事实在三处以不同口径观测"（k8s Job 三态 / crawl_runs 四态 /
排期时间），按项目硬约束③取**可验证更严的一侧**——判据一律锚"窗口后是否有一条**成功且
不低于下限**的运行记录"，不以"触发过"或"文件有行"替代。丙案的盲区已显式登记，不允许
在报告里把 `no_new_job` 说成"源健康"。

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
| 2026-11-11 | 118 | 0 | — | — | month(840h) |
