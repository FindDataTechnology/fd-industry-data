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
