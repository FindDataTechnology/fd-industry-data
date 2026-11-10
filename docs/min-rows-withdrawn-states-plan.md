# 丙案落地清单 · 三源窗口态进观察表（只写不改，等裁决）

把「暂撤声明的三个源，窗口之后那次跑成了没有」从**人工/agent 手动跑 k8s 命令**，
改成**观察脚本每轮自动算三行**。零新增凭据、零出片、零新写入方。

本文件是**实施清单**，不含已生效的代码改动；裁决后照此落地。

---

## 1. 判据（先定口径，再谈代码）

窗口 = 该源的**下一次排期**。窗口之后**新建**的那次运行才算数——窗口之前跑成功
也证明不了上游恢复，它跑的仍是撤声明之前的状态。

状态从 `crawl_runs` 记录算，六个态分开报（**取严的一侧**：只有第 1 种算通过）：

| 态 | 条件 | 红 | 处置 |
|---|---|---|---|
| `complete` | 窗口后有 `success`/`partial` 且 `rows ≥ floor` | — | 可恢复（走 `restore_min_rows.py`） |
| `low_rows` | 窗口后有 `success`/`partial` 但 `rows < floor` | ✗ | 跑了但上游仍薄——**正是当初三张误报单的形态**，不能当通过 |
| `failed` | 窗口后有 `status='failed'` | ✗ | 跑过没成，看 `error` |
| `incomplete` | 窗口后有 `status='running'` 且无终态 | ✗ | 还在跑，或卡住 |
| `no_new_job` | 窗口后**一条记录都没有** | ✗ | 调度没触发 **或** Pod 没起来（盲区，见 §5） |
| `pending` | 窗口未到 | ✗ 不算 | 提前报红＝天天误报 |

**floor 从哪来**：撤声明时 manifest 里的那三行已被删掉，所以 floor 必须**随窗口一起
写进常量**（`boxofficemojo` 25 / `cisa` 25 / `gb-standards` 30），不能运行时去读
manifest——那里已经没有了，读出来是 `None`，而 `None` 会被误当成"未声明、不判"。

---

## 2. 改动清单（逐文件，共 4 处）

### 2.1 新增 `fd_industry_data/health/withdrawn.py`（唯一真源）

```python
"""暂撤 min_rows_per_run 的三源：窗口 + 原下限 + 从遥测算窗口态。"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

@dataclass(frozen=True)
class Window:
    source: str
    at: datetime      # 该源下一次排期（CST）
    floor: int        # 撤声明时的 min_rows_per_run 原值（manifest 里已删）

WITHDRAWN = (
    Window("gb-standards",  datetime(2026, 11, 12, 2, 25, tzinfo=CST), 30),
    Window("cisa",          datetime(2026, 11, 15, 1, 29, tzinfo=CST), 25),
    Window("boxofficemojo", datetime(2026, 11, 17, 3, 14, tzinfo=CST), 25),
)

RED = {"low_rows", "failed", "incomplete", "no_new_job"}

def window_states(records: list[dict], now: datetime,
                  windows=WITHDRAWN) -> list[dict]:
    """每个源一条：{source, window, verdict, why, red, last_success}。"""
    ...
```

`why` 里必须能读出一句人话，例如
`窗口后无记录（最近 success 2026-11-05 02:25，rows=30）`。

### 2.2 改 `scripts/min_rows_observation.py`

| 锚点 | 改动 |
|---|---|
| 顶部 import 区 | `from fd_industry_data.health.withdrawn import window_states` |
| `main()` 指纹段之后（现 `271-279`） | 打印三行窗口态；无遥测时不打印（与指纹同条件） |
| `263-266` 的 `--json` payload | 加 `"withdrawn": withdrawn_rows` |
| **参数区** | 加 `--now`（ISO8601）——**预演时发现的缺口，见下方注** |
| 末尾 `291` `return 0` | 改为：有红 → `return 3`（`2` 已被"无遥测"占用） |
| 表结构 | **不动**：不加列、不改 `HEADER`、不改 `build_row` |

> **`--now` 为什么必须加（预演实测）**：`--day` 只决定**行里的日期**，不决定"现在"。
> 而窗口态判据依赖真正的当前时刻，所以 A6「构造一条红」在原来的清单里**根本构造不出来**
> ——测试只能等到 11-17 之后才可能红。加了 `--now` 之后，`--now 2026-11-20` 立刻能造出
> `no_new_job` 并验证 `rc=3`。这与 `check_withdrawn_sources.py` 已有的 `--now` 保持对称。

打印样例（**三行**，与指令口径一致）：

```
三源窗口态（暂撤声明）：
  gb-standards   窗口 11-12 02:25  ✗no_new_job  窗口后无记录（最近 success 11-05 02:25，rows=30）
  cisa           窗口 11-15 01:29  …pending     窗口未到（还有 98.1h）
  boxofficemojo  窗口 11-17 03:14  …pending     窗口未到（还有 147.8h）
```

### 2.3 改 `scripts/check_withdrawn_sources.py`

删掉本地的 `WINDOWS`（现 `47-51`）与 `RED`（现 `53`），改为
`from fd_industry_data.health.withdrawn import WITHDRAWN, RED`；`classify()` 签名收
`windows=WITHDRAWN`，内部按 `w.source / w.at` 取。**理由**：日期与 floor 只能有一处
字面量，否则两处会各自漂移（本仓已有"同一事实写在四面会各自漂移"的旧账）。

### 2.4 改 `tests/`

- 新增 `tests/test_withdrawn_states.py`（§3 的 A1–A4、A7）
- 新增 `tests/test_min_rows_observation.py` 里两条：A5（`--json` 含 `withdrawn`）、A6（rc=3）

---

## 3. 验收断言（每条都能跑，且必须能红）

| 编号 | 断言 | 怎么验 | 期望 |
|---|---|---|---|
| **A1** | 四态各自可达 | 构造 4 组 record：窗口后无记录 / `failed` / `running` 无终态 / `success rows=100`；调 `window_states` | 依次 `no_new_job` `failed` `incomplete` `complete` |
| **A2** | **`low_rows` 必须红** | `success rows=20`、floor=25 | `low_rows`，`red=True` —— 这正是三张误报单的形态，若判成 `complete` 就是把老毛病放回来 |
| **A3** | 窗口前的成功不算 | 窗口前 `success rows=100`，窗口后无记录 | `no_new_job`（不是 `complete`） |
| **A4** | 窗口边界按 `>=` | 记录 `started_at` 恰等于窗口时刻 | 算窗口内 |
| **A5** | `--json` 带 `withdrawn` 段 | `pytest -k json_mode` | 3 条，每条形如 `{source, verdict, window, red, why}` |
| **A6** | 有红时退出码 3，与"无遥测"的 2 区分 | 构造一条红 | `rc == 3`；无遥测仍 `rc == 2` |
| **A7** | **日期只有一个真源** | `grep -cE "datetime\(2026, *11, *(12\|15\|17)" scripts/check_withdrawn_sources.py` | 现在 = **3**，落地后必须 = **0**（全部来自 `withdrawn.py`） |
| **A8** | 不回归 | `pytest -k "min_rows or health or withdrawn"` | ≥ 308 passed（现基线） |
| **A9** | **观察表文件零改动**（三行只打印到 stdout，不落盘） | `git show --stat <落地commit> -- docs/min-rows-observation-log.md` | 输出为空（若这一行出现，说明不小心改了表结构） |
| **A10** | 真环境冒烟 | 等 11-11 03:17 的 workflow（含 `--from-pg`） | 三行输出出现；窗口未到 → 全 `pending`、`rc=0`、当日行照常追加 |

**A10 是唯一不能本地验的**（要 `FD_CENTRAL_PG_DSN`，只有 runner 有）；本地只能验到
`--snapshot` 路径。

**验收纪律（本清单自己先踩过一次）**：A7 最初写成 `grep "2026-11-12"`，而代码里的写法是
`datetime(2026, 11, 12, ...)`——那个断言**改之前就是 0**，改与不改都绿，等于没测。
已改成按真实写法计数（现在必须为 3，落地后必须为 0）。凡"做之前就已经通过"的断言，
一律视为没写。

---

## 8. 预演结果（2026-11-10，分支 `rehearsal/bing-plan`，未并入 main）

照本清单在独立 worktree 里实施了一遍，A1–A9 实测：
| 断言 | 结果 |
|---|---|
| A1 四态可达 | ✓（14 条用例全过，含 `low_rows` 与 `pending`） |
| A2 `low_rows` 必须红 | ✓；反向注入"并进 complete" → **2 条红**，证明这条真在测 |
| A3 窗口前成功不算 | ✓ |
| A4 边界按 `>=` | ✓（恰等于窗口时刻算窗口内） |
| A5 `--json` 带 `withdrawn` | ✓ |
| A6 rc=3 与 rc=2 区分 | ✓（需 `--now`，见 §2.2 注） |
| A7 日期单一真源 | ✓ check 脚本 0 命中、`withdrawn.py` 3 命中 |
| A8 不回归 | ✓ **325 passed**（基线 308 + 新增 17） |
| A9 观察表零改动 | ✓ 0 行 diff，表结构正常 |
| A10 真环境冒烟 | 未跑（要 `FD_CENTRAL_PG_DSN`，只有 runner 有） |

产出保留在本地分支 `rehearsal/bing-plan`（`f1eb50a`），**未推送、未并入 main**。
裁决为丙时，`git merge rehearsal/bing-plan` 即可，A10 交给 11-11 03:17 那次巡检自然验。

---

## 4. 回滚点（硬约束②：先说清怎么退）

| 项 | 回滚动作 |
|---|---|
| 代码 | `git revert <commit>` 一个提交即可；无 DB 迁移、无表结构变更、无配置项新增 |
| 观察表 | **不需要回填**——表结构与历史行都不变，新增的只是行下方的三行打印 |
| 退出码 | 影响面仅限 workflow 那一步的非阻断分支（`set +e` + `rc!=0` 出 `::warning::`）；回滚后恢复为恒 `0` |
| 误判风险 | 若 `no_new_job` 大面积误报（例如 telemetry SQL 漏源），把该源从 `WITHDRAWN` 摘掉即可，不需要改逻辑 |

---

## 5. 必须写进文档的盲区（不许含糊）

`no_new_job` = "窗口后**没有记录**"，不是"没跑"。以下三种故障在 `crawl_runs` 里**完全
不留痕**，会和"调度没触发"同形：

1. 拉镜像失败（`ImagePullBackOff`）
2. init 容器 git clone 失败（`GITEE_TOKEN` 失效、gitee 抖动）
3. 调度失败（资源不足、`nodeSelector` 不满足）

**缓解**：`no_new_job` 时同时打印"距该源上一次任何记录多久"，若超过一个排期周期，
措辞从 `窗口后无记录` 升级为 `疑似根本没起 Pod`，并提示去看 Job（甲/乙案的命令）。
**但这只是提示，不改变判定**——三种情况一律红。

---

## 6. 决定记录（硬约束③）

同一事实在三处以不同口径可见（k8s Job 三态 / `crawl_runs` 六态 / 排期时间）。本清单
取**可验证更严的一侧**：

1. 判据锚**运行记录**，不锚"触发过"（`lastScheduleTime` 证明不了跑成，
   `boxofficemojo` 11-10 就是触发过但 `Failed`）；
2. `success` **不等于**通过——还要 `rows ≥ floor`（`low_rows` 单列一态且红）；
3. `no_new_job` 与 `pending` **不许混用**：前者是红，后者是"不能判"；
4. 任何报告里禁止把 `no_new_job` 说成"源健康"。

---

## 7. 不改的东西（避免顺手扩面）

- `submit_new_tickets` / `sync_status`（`--dry-run` 未覆盖 `sync_status` 那个缺陷另有记录，与本案无关）
- `restore_min_rows.py`（它读遥测判"是否已重跑"，逻辑已经是对的；本清单只补"看得见"）
- 观察表的列结构与 `HEADER`
- workflow 的步骤集合（丙案不需要新 step，现有步骤里就会打印）

---

## 9. 落地记录（2026-11-10 裁决：丙案）

已合并到 main（`4aafc0c`），预演分支 `rehearsal/bing-plan`（`f1eb50a`）为其祖先。
复跑 A1–A9：**全部通过**。

| 断言 | 复跑结果 |
|---|---|
| A1 / A3 / A4 | ✓ `tests/test_withdrawn_states.py` 14 passed |
| A2 `low_rows` 红 | ✓ 反向注入"并进 complete" → **2 failed, 12 passed**，还原后 14 passed |
| A5 / A6 | ✓ 3 passed；手工复核三种退出码 |
| A7 | ✓ check 脚本 0 命中、`withdrawn.py` 3 命中 |
| A8 | ✓ **325 passed**（基线 308 + 新增 17） |
| A9 | ✓ `git show --stat HEAD -- docs/min-rows-observation-log.md` 为空 |
| A10 | 待 11-11 03:17 那次巡检（含 `--from-pg`）自然验 |

**退出码实测**（`| tail` 会把 `$?` 换成 tail 的退出码，必须用 `PIPESTATUS` 或落文件）：

| 场景 | rc（落文件 `$?`） | rc（`PIPESTATUS[0]`，bash） |
|---|---|---|
| 窗口未到（`--now 2026-11-10T23:59+08:00`） | 0 | 0 |
| 窗口已过且无记录（`--now 2026-11-20T00:00+08:00`） | **3** | **3** |
| 无遥测（`--append` 且不带 `--snapshot/--from-pg`） | **2** | **2** |

> **另一个坑（2026-11-11 实测）**：本机默认 shell 是 **zsh**，`${PIPESTATUS[0]}` 在 zsh 里
> **是空的**（不报错，只是取不到），要写 `${pipestatus[1]}`（小写、1 起）。要么显式
> `bash -c '...'`，要么干脆落文件再 `tail`——后一种与 shell 无关，最稳。

`check_withdrawn_sources.py` 改为 import 单一真源后仍正常：live 全 `pending` rc=0；
喂真实 Job 产物 + `--now 11-13` → `gb-standards` 标红、rc=1。

---

## 10. A10 核对（2026-11-11）

### 10.1 先说结论：03:17 那次还没到期，但顺手查出**这一步从上线起就没成功过**

核对发生在 **2026-11-11 00:24 CST**，距 03:17 还有近 3 小时，所以"03:17 那次"无法核。
改为**主动触发一次 dispatch**（`dry_run=true`，run `42855093033`）在真环境验同一条路径。

拉上一次真跑（`42817406652`，11-10 20:37 CST）的日志，发现它**失败了**：

```
ModuleNotFoundError: No module named 'fd_industry_data'
##[warning]观察表追加失败（rc=1），工单照常落仓
```

而该步骤设了「不阻断」（`set +e` + 末尾 `exit 0`），所以**步骤结论显示 `success`**——
这就是"探针通过"与"探针根本没跑"在步骤色上同形的又一例。表里那行 `2026-11-10`
是 `c4574c5`（我 11-10 19:49 本地跑脚本）写进去的，**不是 CI 写的**。

**根因**：workflow 在 `working-directory: src` 下跑 `python3 scripts/xxx.py`，
`sys.path[0]` 是 `src/scripts`、**不含 `src/`**。同目录的 `health_inspect.py` /
`adjudicate.py` / `reconcile_render.py` 都自己 `sys.path.insert(0, REPO)`，
只有 `min_rows_observation.py` 漏了这一行（丙案新增 import 后暴露出来）。
修复：`ad2aa16`。

### 10.2 修复后的真环境结果（run `42855093033`）

| 观测点 | 证据 | 结果 |
|---|---|---|
| 该次**带 `--from-pg`** | workflow 第 135 行：`python3 scripts/min_rows_observation.py --from-pg --append ...`；只有这一条调用路径 | ✓ |
| 脚本**真跑通**（不再 ImportError） | 落库门提交 `5513888` 在 `docs/min-rows-observation-log.md` 追加**恰好一行** `\| 2026-11-11 \| 118 \| 0 \| — \| — \| month(840h) \|` | ✓ `healthy=118` 只有读到库才可能算出来 |
| **表格未被小节隔断** | `git show 5513888 -- docs/min-rows-observation-log.md` → 1 行新增，紧跟 11-10 行之后，位于最后一个表格块内 | ✓ |
| 三行窗口态 | job log 原文（run 结束后取到） | ✓ 见下 |
| 退出码 | 日志里**没有** `##[warning]观察表追加失败`（唯一出现的那句是 workflow 源码回显） | **0** |

三行原文（`gh api .../actions/jobs/129965862309/logs`，已去 ANSI）：

```
已追加到 docs/min-rows-observation-log.md
四项上线指纹：
  ① unregistered_sources 字段: 有 ✓ ['example-stock', 'yfinance']
  ② skipped_unknown_period 无调用栈: ✓ []
  ③ deferred 带 category/rule: ✗（1 条）
  ④ blocked_config_sources 字段: 有 ✓ []
三源窗口态（暂撤声明）：
  gb-standards   窗口 11-12 02:25  …pending     窗口未到（还有 25.9h）
  cisa           窗口 11-15 01:29  …pending     窗口未到（还有 96.9h）
  boxofficemojo  窗口 11-17 03:14  …pending     窗口未到（还有 146.7h）
```

`rc=0` 的判据不是"步骤绿"，而是日志里没有那句 warning——`set +e` 下这行只是
`::warning`，步骤结论永远是 success。

### 10.3 顺带查出的第二个缺陷：**冻结路径的顺延条目没有判词**

上面 `③ deferred 带 category/rule: ✗（1 条）` 不是新代码没生效，是真的缺字段。
顺延条目有两条产生路径：

| 路径 | 位置 | 判词 |
|---|---|---|
| 日限额 / 去重 / 非终态 | `inspector.write_plan`（`872-880`） | ✓ 有 `category` + `rule` |
| **裁决冻结** | `adjudication.apply_freeze`（原 `465`） | ✗ **只写 `{source, reason}`** |

这正是 11-09「顺延条目必须说清它本该是什么」那条修复**漏掉的第二条路径**。后果不是
"少了点信息"，而是**指纹 ③ 变成常红**——一个设计用来证明"新代码在跑"的信号，被一个
与部署无关的字段缺口长期点亮，久了就没人看它了。

已修（`apply_freeze` 从 `item.verdict` 取 `category` 与 `rule_trace` 冒号前那段，
与 `write_plan` 同口径，并用 `getattr` 保持鸭子类型契约）；新用例
`test_apply_freeze_deferral_carries_verdict` 断言三件事，反向还原旧实现 → **2 条红**。

### 10.4 这一轮的三条教训

- **非阻断步骤必须看产物，不能看结论**。`set +e` + `exit 0` 让失败在 UI 上完全隐身。
- **脚本要自带 `sys.path` 引导**：`python3 scripts/x.py` 的 `sys.path[0]` 是 `scripts/`，
  不是仓库根。同目录 4 个脚本里 3 个有这行，缺的那个静默失败了两天。
- **我的第一次验证是假证明**：用 `python3 -c` 验 import，而 `-c` 的 `sys.path[0]` 是
  **cwd**——正好落在仓库根，所以"移除引导也成功"。改成按真形态跑**脚本文件**、
  cwd 不在仓库根，才复现出 `ModuleNotFoundError`。
- **自检信号被无关缺口长期点亮＝信号报废**：指纹 ③ 常红不是"新代码没上"，而是冻结路径
  少两个字段。红灯太久就没人看了，所以指纹类断言也要定期问"它红的原因是我的目标变量吗"。

### 10.5 仍待办

| 项 | 状态 |
|---|---|
| 11-11 03:17 的**定时** run（非 dispatch） | 未到（核对时 00:24 CST）；dispatch 已验同一条路径，定时只是触发方式不同 |
| gb-standards 11-12 02:25 窗口 | 待到期，到点看 `check_withdrawn_sources.py` 或观察表的窗口态三行 |
| cisa 11-15 / boxofficemojo 11-17 | 同上 |
| 指纹 ③ 下一次运行是否转绿 | 待下一次巡检（修复已提交，代码从 github 浅克隆进入运行环境） |
