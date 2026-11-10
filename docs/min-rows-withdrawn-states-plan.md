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
| `pending` | 窗口未到 | **不算红** | 提前报红＝天天误报 |

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

**三步退出码各说各话**（2026-11-11 修）：`0` 正常、`2` 没遥测已拒绝写表、`3` **表已写入**
但有暂撤源未恢复。workflow 里原先三个码都报「观察表追加失败」，于是 `rc=3` 会被读成
"写盘出问题"——而那时行其实写成功了，真问题是源没恢复。现已按码分支给不同提示，
`rc=3` 那条直接指向「三源窗口态」三行与 §11 的核 Job 命令。

**写这段时踩到一个隐蔽坑**：`$rc` 紧贴全角括号写成 `（rc=$rc）`，在非 UTF-8 locale 下
bash 会把 `$rc` 与后面多字节字符一起解析，输出成 `rc=??`。改成 `${rc}` 定界后正常
（`od -c` 可复现：旧写法产出 `274 211` 两字节残片）。这类问题在 CI 里可能恰好不犯，
所以不能靠"CI 绿"来判断文案对——已用 `tests/test_health_workflow_rc.py` 固化：
它把 workflow 里的 `case` **原文抽出来执行**，逐码断言输出。

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

### 10.5 待办表（2026-11-11 00:50 CST 复核）

**为什么这次不等 03:17 的定时 run**：修复（`28f00bb`）已推到 github main，而 workflow
是浅克隆 github 的 `main`——所以任意一次新 run 都等价于"下一次巡检"的代码面。
与其空等 2.5 小时，不如自己触发一次（`42858802087`，`dry_run=true`），4 分钟就拿到了
与定时 run 同源的证据。定时那次到点后如需再确认，看同一行日志即可。

**③ 转绿的判读边界**（别把它读大）：
- 它是 `write_plan` 在**临时目录**里重放出来的形态，不是"生产真的冻结了某个源"。当前
  仓里确有一批 frozen 源（`adjudication.frozen_sources('.')` 返回 30 个），所以这条
  路径确实会被走到；
- 本地另做了一次构造验证：伪造 escalated 队列 → `apply_freeze` 产出
  `{'source': 'alpha', 'category': 'structure', 'rule': 'rule1c', ...}` →
  `_deferred_carry_verdict` 返回 `True`。修复前后的差异由此可复现，不依赖 CI。

| 项 | 状态 | 证据 |
|---|---|---|
| **指纹 ③ 是否转绿** | ✅ **已转绿**（`✓（1 条）`），四指纹全绿 | run `42858802087` job log：`③ deferred 带 category/rule: ✓（1 条）` |
| 观察表当日行是否重复 | ✅ 未重复 | 同次日志 `（2026-11-11 已有记录，未重复追加）` |
| 三行窗口态是否照常打印 | ✅ 三行均在，全 `…pending` | 同次日志（还有 25.5h / 96.6h / 146.3h） |
| **gb-standards 11-12 02:25 窗口** | ⏳ **未到期**（复核时距 25.6h） | `date` + 窗口常量比对；当前最新 Job 仍是 `29896945 Complete`（11-05） |
| cisa 11-15 01:29 / boxofficemojo 11-17 03:14 | ⏳ 未到期（96.6h / 146.4h） | 同上 |
| 11-11 03:17 的**定时** run | ⏳ 未到（复核时 00:50 CST，还有 2.5h） | dispatch 已把同一条代码路径验完，定时只是触发方式不同 |

---

## 11. 四个待期窗口 · 到期判定清单（下一轮照此核）

每条都**只有一个命令**，且都验过可跑（2026-11-11 00:5x CST 实跑，见 §11.5）。
判据统一：**窗口之后新建的那次运行必须成功且不低于下限**——不看"触发过"、不看步骤色。

### 11.1 gb-standards · 2026-11-12 02:25 CST（周四）

```bash
python3 scripts/check_withdrawn_sources.py --json | python3 -c "
import json,sys
r=[x for x in json.load(sys.stdin)['rows'] if x['source']=='gb-standards'][0]
print(r['verdict'], r['red'], '|', r['why'], '|', r['latest_job'])"
```

| 项 | 内容 |
|---|---|
| **通过判据** | `verdict == complete` 且 `red is False`，且 `latest_job.state == complete`、`latest_job.created` 晚于 `2026-11-12T02:25+08:00`；**floor = 30**（撤声明时的原值） |
| **证据来源** | tencent 集群 `fd-industry-gb-standards` 的 Job `.status.conditions[Complete]`（脚本直接读，非排期表推算） |
| **旁证** | 当次巡检 job log 的三行窗口态里该行应为 `✓complete`；观察表当日行仍由 workflow 追加 |
| **不通过处置** | `no_new_job` / `failed` / `incomplete` → **保持暂撤**，不动 manifest；写到 §10.5；连续两个周期都失败才升级人工 |

### 11.2 cisa · 2026-11-15 01:29 CST

```bash
python3 scripts/check_withdrawn_sources.py --json | python3 -c "
import json,sys
r=[x for x in json.load(sys.stdin)['rows'] if x['source']=='cisa'][0]
print(r['verdict'], r['red'], '|', r['why'], '|', r['latest_job'])"
```

判据 / 证据 / 处置同 11.1，窗口换成 `2026-11-15T01:29+08:00`，**floor = 25**。
**注意**：cisa 的 cron 是 `5,15,25`（每月三天）。所以：
- **11-15 01:29 之前**跑，得到的是 `pending`（窗口未到，不能判）——不是 `no_new_job`；
- **11-15 01:29 之后**若仍 `no_new_job`，那是真的调度没触发（这个源每月只有 3 次机会，
  错过 11-15 就要等到 11-25，所以这条要盯紧）；
- 11-15 那次跑成功且 `rows ≥ 25` 才算 `complete`。

### 11.3 boxofficemojo · 2026-11-17 03:14 CST（周二）

```bash
python3 scripts/check_withdrawn_sources.py --json | python3 -c "
import json,sys
r=[x for x in json.load(sys.stdin)['rows'] if x['source']=='boxofficemojo'][0]
print(r['verdict'], r['red'], '|', r['why'], '|', r['latest_job'])"
```

判据 / 证据 / 处置同 11.1，窗口换成 `2026-11-17T03:14+08:00`，**floor = 25**。
**特别提示**：这个源 11-10 那次是 **Failed（`BackoffLimitExceeded`）**，所以当前
`latest_job.state == failed` 但 `verdict == pending`——**pending 优先于 latest_job**，
窗口未到前不要因为看到 failed 就提前判"没恢复"。窗口到了若仍 failed，才算未恢复。

### 11.4 11-11 03:17 CST 的**定时** run（不是 dispatch）

```bash
gh run list --workflow=health-inspect.yml --event=schedule --limit 1 \
  --json databaseId,createdAt,conclusion -q '.[0]|"\(.databaseId) \(.createdAt) \(.conclusion)"'
```

| 项 | 内容 |
|---|---|
| **通过判据** | 存在 `event=schedule`、`createdAt > 2026-11-10T19:17:00Z`、`conclusion=success` 的 run |
| **证据来源** | 该 run 的 job log 里 `③ deferred 带 category/rule: ✓` 那行 + 三行窗口态 |
| **别急着判"没跑"** | 定时触发实测**延迟 3.4–6.8h**（cron `19:17Z`，实际落在 `22:42Z`–`02:03Z`；见下表），所以 03:20 CST 没看到 run 是正常的，等到 06:30–10:00 CST 再看 |
| **不通过处置** | 若到 10:00 CST 仍无 schedule run → 查 `concurrency: health-inspect` 是否被一次长跑（999s 的 `sync_status`）占着，或 runner pod 是否在 |

历史延迟实测（`gh run list --event=schedule`）：

| run 落地时刻 (UTC) | 对应 cron | 延迟 |
|---|---|---|
| 2026-11-10T02:03:52Z | 11-09 19:17Z | 6.8h |
| 2026-11-08T22:51:59Z | 11-08 19:17Z | 3.6h |
| 2026-11-07T22:42:53Z | 11-07 19:17Z | 3.4h |
| 2026-11-06T23:43:19Z | 11-06 19:17Z | 4.4h |

### 11.5 本清单自身的实跑记录（2026-11-11 00:5x CST）

| 命令 | 实测输出 | 结论 |
|---|---|---|
| 11.1 的 gb-standards 单源命令 | `pending False \| 窗口 11-12 02:25 CST 未到 \| {'name': 'fd-industry-gb-standards-29896945', 'created': '2026-11-05T02:25:00+08:00', 'state': 'complete'}` | 命令可跑、判据可读 |
| 11.4 的 schedule 查询 | `[.[]\|select(.createdAt > "2026-11-10T19:17:00Z")]\|length` → **0** | 11-11 那次确实还没落地 |
| 三源 label 选择器 | `-l fd-industry/source=<src>` 三源全部有命中；`boxofficemojo-29904194` 无 `Complete` 条件（它是 Failed） | 选择器不靠猜，Failed 与 Complete 可区分 |
| **三条单源命令逐条照抄实跑** | gb-standards / cisa / boxofficemojo 三条都返回 `pending … latest_job`，其中 boxofficemojo 带出 `state: failed`（11-10 那次） | 命令不是只测一条，三条都通 |
| **判据真的会红**（`--now` 推过各窗口） | 11-13 → gb-standards `no_new_job red=True`；11-16 → cisa 同；11-18 → boxofficemojo `no_new_job red=True`（其 `latest_job` 是 11-10 failed） | 三条各自在**自己的**窗口后才翻红，不是一推全红或永不红 |

**写这一节时踩的坑（记下来免得下一轮重踩）**：
1. `for pair in "src 时间"` + `set -- $pair` 在 **zsh** 下不拆词——`$1` 拿到整串、
   `$2` 为空，于是 `--now ""` 让脚本直接退出、JSON 为空。zsh 不自动分词，要用
   `${=pair}` 或干脆写三条显式命令。
2. 用 `bash -c "… python -c "…" …"` 三层引号嵌 python 时极易把内层引号吃掉，
   表现为「命令没输出」。这类一次性脚本宁可落文件再读（`> /tmp/x.json`），
   和退出码那条是同一个道理。

---

## 12. 一周误报率观察 · 口径与每日核对

§11 管的是"三个暂撤源恢复了没有"，这一节管的是**阈值本身准不准**（`min_rows_per_run`
会不会天天冤枉正常源）。两者共用同一批产物（工单 + 遥测），但判的是两件事。

### 12.1 误报定义（两向都能证伪）

一张 rule1c 单算**误报**，必须落在下面两类里，且能被产物证实：

| 类 | 判据 | 怎么证 |
|---|---|---|
| **E1 不可比运行** | 触发那张单的 `last_success` 不是本源的常规调度产出：`image_tag=unknown`（非 k8s dispatcher）、或 `metrics.limit < floor`（被 limit 截断）、或落在一次性批量窗口里 | 工单 `evidence.last_success` + `crawl_runs.metrics`；E1 是**数据不可比**，与阈值无关 |
| **E2 无干预自愈** | 出单后**没改过**该源（`git log --oneline -- spiders/<src>/` 在出单后为空）、且在一个周期内又跑成功且 `rows ≥ floor` | 工单 lifecycle 时间 + `crawl_runs` 达标行 + git 历史 |

**不算误报**（必须显式排除，否则分母会被人为做大）：

- 出单时该源**超窗**（压根没跑）→ 真因在调度/抓取，巡检报得对；
- 出单后有**代码修复提交**（`spiders/<src>/` 有改动）→ 真缺陷被修好，不是阈值敏感；
- 上游**数据期本来就长**（`data_period` 与 `rows` 都说明是空窗）→ 属真空窗，不是误报。

### 12.2 分母取法（三个口径，主口径取严）

| 口径 | 分母 | 分子 | 优劣 |
|---|---|---|---|
| **工单级（主）** | 期间 rule1c 工单**张数** | 上述 E1∪E2 的张数 | 每张单都必须有判定，**不能因为同源多单就少算**——取严 |
| 源级（稳健性） | 期间出过 rule1c 的**源数** | 其中至少 1 张属 E1∪E2 的源数 | 抗重复出单；但会掩盖"同一源反复误报" |
| 日级（趋势） | 观察**天数** | 当天有新误报的天数 | 只用于看有没有集中爆发，不单独当结论 |

**决定记录（硬约束③）**：主口径用**工单级**，因为"每张单都得判一次"比"每源算一次"更严、
也更难糊弄；同时输出源级数做稳健性对照。**两者背离时以工单级为准并说明原因**。
分母只统计**观察期间**（`--days 7`）落盘的 rule1c 单，不追溯历史——历史那批（11-06 探针）
已单独定性，混进来会把率算成 100%。

### 12.3 每日一条核对命令

在有 `FD_CENTRAL_PG_DSN` 的环境（巡检 runner，或本地导出该变量）跑：

```bash
python3 scripts/min_rows_false_positive_report.py --days 7 --from-pg
```

输出就是分子分母的原料——每源的「单数」加总 = 工单级分母，「出单后达标次数 > 0」是 E2 的
候选（**注意它只是候选**：还要查 git 有没有修复提交，才算 E2）。

实测输出（2026-11-11 00:5x CST，`--from-pg` 用假 `psql` 走同一条路径喂 11-06 探针数据）：

```
近 7 天的 rule1c（行数不足）工单回看：共 3 张，涉及 3 个源

source                     单数       最近一张    下限        出单后达标次数
boxofficemojo               1 2026-11-09    25              0
cisa                        1 2026-11-09    25              0
gb-standards                1 2026-11-09    30              0
```

| 项 | 内容 |
|---|---|
| **通过判据** | 每张 rule1c 单都能落到 E1 / E2 / 不算误报 三者之一；无明显归属的单 → 视为"未判定"单列，不许默认当误报 |
| **证据来源** | 工单 YAML（`evidence.last_success`、`lifecycle`、`terminal`）+ `crawl_runs`（`status`/`rows`/`metrics`）+ `git log -- spiders/<src>/` |
| **不通过处置** | 工单级误报率 **> 20%** 且分母 ≥ 5 → 把该源 `min_rows_per_run` 回退（删 manifest 那行）并记一条决定；分母 < 5 时只记录不改 |
| **无 DSN 时** | 命令会以 `ValueError: FD_CENTRAL_PG_DSN is required for --from-pg` 退出（rc=1）。**别把它当"没有误报"**：退化用法是去掉 `--from-pg`（只看单数、最后一列显示"未声明"），并明确标注"未取遥测" |

### 12.4 当前累计（基线，2026-11-11）

| 指标 | 值 | 说明 |
|---|---|---|
| rule1c 工单（全部历史） | **3 张**（`boxofficemojo` / `cisa` / `gb-standards`，11-09T22:30Z 同一批） | 分母（全历史口径） |
| 其中判为误报（`terminal: closed`） | **3 张** | 分子，全部属 **E1**（11-06 批量探针：48 源各 20 行、`image_tag=unknown`） |
| 工单级误报率（全历史） | **100%（3/3）** | 分母 3 且同因，**不可外推** |
| 观察期（`--days 7`）内 | **0 张**新增 rule1c | 观察期从 11-10 起算 |
| rule1c 占全部工单 | 3.3%（3/90） | 参照 |
| 源级 | 8.3%（3/36 已声明源） | 稳健性口径 |

**观察期为什么是"从 11-10 起"而不是按 `--days 7` 硬算**：`--days 7` 在 11-11 会把
11-09 那批（11-06 探针的尾巴）算进来，于是"一周误报率 100%"。那批已单独定性、也已关单，
所以观察期起点固定在**撤声明那一刻**（11-10 06:22Z），此后新增的单才计入误报率。

### 12.5 接入位置（2026-11-11）

接进的是 **`health-inspect.yml` 的「误报率核对（§12，不阻断）」步骤**，紧跟
「观察表追加 + 四项指纹」之后、落库门之前。**仓里没有 04:10 的巡检**——全仓 workflow
的 cron 只有 `health-inspect.yml` 一条巡检，为 `17 19 * * *`（= **03:17 CST**）；
其它 cron 都是各条**告警线**，不是巡检（花卉 09:05 CST / 银行 22:05 / 酒店 20:17 /
餐饮 19:47 / 农业 18:17）。所以「04:10 巡检脚本」按此理解为同一条巡检链，接入点取
实际存在的那条；若确有 04:10 的外部调度器（曾有一个 ZCode 会话级自动化，已确认其
prompt 在磁盘上读不到），它跑的就是本仓这条 workflow 的同一份脚本。

该步骤的特点（与观测面其它步骤一致）：
- **只读**：不写工单、不改 manifest、不碰观察表，只把原料打进 step summary；
- **不阻断**：`set +e` + 末尾 `exit 0`，失败只留 `::warning::`——所以**必须看日志产物**，
  步骤色绿不算跑过（这条在 §10.1 已经栽过一次）；
- 判读提示直接印在摘要里（E1/E2、达标次数只是候选），避免看的人自己猜。

### 12.6 实跑记录与踩到的坑

**踩到的坑（已修）**：`min_rows_false_positive_report.py` 原先是
`floors = inspector.min_rows_floor(REPO, s)`——三个暂撤源的 manifest 里那行已删，于是
报表对**恰好是观察对象的三个源**一律显示「下限 —」「出单后达标 未声明」，等于在最该判的
地方失明。已加 `resolve_floors()`：读不到 manifest 就回退到 `health/withdrawn.py` 里
撤声明时记下的原值（25/25/30）；两条用例钉住「回退生效」与「不覆盖仍在声明的源」，
反向移除回退 → 1 条红。

**一条命令取三行**（自带判空：rc=0 取到、rc=3 该 run 里没有这一步、rc=2 取不到 job）：

```bash
ID=$(gh run list --workflow=health-inspect.yml --event=schedule --limit 1 \
       --json databaseId -q '.[0].databaseId')
JOB=$(gh api "repos/FindDataTechnology/fd-industry-data/actions/runs/$ID/jobs" \
       -q '.jobs[0].id')
OUT=$(gh api "repos/FindDataTechnology/fd-industry-data/actions/jobs/$JOB/logs" 2>/dev/null \
  | sed 's/\x1b\[[0-9;]*m//g' \
  | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z //' \
  | grep -A6 "近 7 天的 rule1c" | grep -vE "^$|^读法" | head -6)
[ -z "$OUT" ] && { echo "run $ID 里没有「近 7 天的 rule1c」：未到点或步骤没跑到，不要填" >&2; exit 3; }
echo "$OUT"
```

**这条命令验过两向**（2026-11-11 01:33 CST）：
- 对已知含该步骤的 run `42864611853` → 取到 4 行（表头 + 三源），rc=0；
- 对步骤上线前的 schedule run `42740120332` → **取不到**并打印「该 run 不含此步骤」，rc=3。
  说明"空输出"能被区分出来，不会把"没跑到"当成"零误报"。

| 项 | 实测 | 结论 |
|---|---|---|
| `--days 7`（无 DSN） | 3 张单、3 个源，最后一列全"未声明" | 可跑，但**不能判 E2** |
| `--days 7 --from-pg`（假 psql） | 3 张单，下限 25/25/30，达标次数 0 | 命令跑通，比值可算 |
| 无 DSN 直接 `--from-pg` | `ValueError: FD_CENTRAL_PG_DSN is required`，rc=1 | 退化行为已写明，别当"零误报" |
| **接入巡检后的首次真跑**（run `42862732510`，2026-11-11 01:17 CST，真 DSN） | 3 张单、3 个源；下限 25/25/30；出单后达标全 0；无 `##[warning]误报率核对失败` | 步骤跑通，分子分母可算：**观察期内新增 0 张**，全历史仍 3 张（全 E1），工单级 100%（3/3，分母 3 不可外推） |
| **`49b292a` 定点验证**（run `42864611853`，2026-11-11 01:28 CST，`headSha=49b292a`） | `conclusion=success`；摘要输出同上（3 张 / 3 源 / 25·25·30 / 达标全 0）；workflow `state=active` | **49b292a 在 Actions 上解析并通过绿跑**——不是靠"1f832d6 绿过所以它也绿"的推断，而是用该 SHA 直接 dispatch 跑出来的（另：`git diff 1f832d6 49b292a -- .github/ scripts/` 为空，两版工作流一字未变） |
| **次日 03:17 CST 的定时 run** | ⏳ **未落地**（2026-11-11 01:33 CST 核对，最新 schedule run 仍是 `42740120332` / 11-10T02:03Z） | 待补。定时触发实测延迟 3.4–6.8h（见 §11.4），预计落在 **06:30–10:00 CST**。到点用上方「一条命令取三行」：rc=0 即拿到、rc=3 说明该 run 里没有这一步（未到点/步骤没跑到），此时**不要**填。**2026-11-11 01:37 CST 照抄实跑 → rc=3**（最新 schedule run 仍是 `42740120332`，早于本步骤上线，故取不到；这不代表 11-11 那次没这一步） |
