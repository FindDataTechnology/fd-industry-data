# 接入漏斗口径（`scripts/onboarding_funnel.py`）

> change `harden-crawl-delivery` 任务 4.4。四段里**只有三段有事实来源**，
> 第四段明确报 `unknown` —— 不编数。

## 四段

| 段 | 来源 | 当前值 |
|---|---|---|
| candidates | **无结构化来源** | `unknown` |
| landed | `spiders/*/manifest.yaml` 存在 | 162 |
| lit | landed 且有非空 `schedule`，且未 `parked`/`enabled: false` | 122 |
| fresh | lit 且 `cycles_since_last_good == 0` | 75（lit 的 61.5%） |

```
landed 162 ──┬─ stopped 31（parked / enabled:false）
             └─ lit   122 ──┬─ fresh     75
                            ├─ at-risk   40   ← 只欠一期
                            └─ stale      7
```

`at-risk` 单列，是因为判据层（D2）把「欠 1 期」当 trace 不出单；漏斗里既不能算
「新鲜」，也不该混进「陈旧」。**75 + 40 + 7 = 122**，三段互斥且穷尽。

## 为什么 candidates 是 unknown

候选只存在于 `data-source-scouting/INDUSTRY-DATA-SOURCES.md` 的手写表格里（300 行、
多张表、状态列格式不一）。硬解析会得到一个**没人能复算**的数字 —— 那正是这份
change 一直在反的那种「判据看着绿、实际不可核」。

要让它变成可计量，需要一次性人工把 scouting 表转成结构化清单
（如 `data-source-scouting/candidates.json`），之后漏斗才能补上第一段。
在那之前，**"还有多少候选没落仓"这个问题没有可信答案**，只能人工读文档。

## 复算

```bash
export FD_CRAWL_DB_URL=<中央库 DSN>
python3 scripts/onboarding_funnel.py --dsn "$FD_CRAWL_DB_URL"
python3 scripts/onboarding_funnel.py --dsn "$FD_CRAWL_DB_URL" --json
python3 scripts/onboarding_funnel.py            # 只看仓库侧三段，fresh 报 unknown
```

## 一条会误导的读数（已修）

首版漏斗把 `eia-v2`、`nmc-weather` 判成 stale。它们**每小时**跑，而 D2 的告警
门槛是「连续 2 个周期」—— 对它们等于 2 小时。已在 `freshness.py` 加
`MIN_ALERT_WINDOW_HOURS = 24`：计数达门槛但距上次成功不足 24h 时压回 1（trace-only）。

这条护栏放在**计数之后**：压在计数之前会把「刚成功过」也压成 1（第一次就是
这么写错的，被 `test_good_run_in_the_current_cycle_is_zero` 抓出来）。

## 边界

- `stopped` 与「有没有 schedule」正交：多数 `parked` 源根本没有 schedule 行，
  只统计「有 schedule 的停用源」会漏掉它们（实测漏 27 个）。
- 「fresh」是严格口径（0 个应跑周期未成功），不是「最近跑过」。
- 漏斗不产出工单，它只回答「落仓的东西有多少真在出数」。

## 与「语义覆盖三联账」的分工（2026-11-21 新增）

本漏斗量的是**爬取层**（落仓 → 点亮 → 新鲜）。它不回答「抓到的这些东西能不能
按概念读到」——那是另一个问题、另一条闸：

| 闸 | 量什么 | 判据 | 出口 |
|---|---|---|---|
| `health_inspect.py`（`health-inspect.yml`） | 源活不活 | `crawl_runs` 连续失败、水位冻结 | 工单 → 自愈 agent |
| `onboarding_funnel.py`（本文件） | 落仓→点亮→新鲜的漏斗 | 每段计数 | 运维视图 |
| `check_observation_lag.py` | 已有抽取器活着吗 | 观测水位 vs 爬取水位 | 门禁（lagging 才红） |
| `semantic_coverage.py`（`semantic-coverage.yml`） | **哪些源根本没接进观测层** | `frozen` / `unwired` 基线 diff | 每日红/绿 |

`semantic_coverage.py` 的实测（2026-11-21，真库）：125 个亮着的源里
**served 34 / frozen 28 / unwired 60 / unknown 3**。也就是说另外 91 个源在
本漏斗里可能是「fresh」的，但它们的数据永远不会变成可检索指标。
**两个数不可互换引用**：漏斗的 fresh 不等于语义层可用。

`frozen` 与 `unwired` 是两种病：
- `frozen` = 观测层有行、当前没有可跑的抽取器（历史抽过，静止变旧）→ 找回旧抽取器
- `unwired` = 观测层零行、没有抽取器 → 新建一个

两档各有一份基线（`config/frozen-baseline.txt` / `config/unwired-baseline.txt`），
基线外新增报红。**不许把两档合并成一个数**：合并后一档修好、另一档新增会互相抵消。

## 欠账复测队列（2026-11-10 起）

`candidates.json` 里 `accepted && landed_slug=null` 的条目（当前 40 条）是「侦察簿说接了、
但从未落仓」的真实欠账。处置纪律：

1. **逐批活性复测**：每批 5 条上下，用 `fd-source-probe`（`probe_endpoint`）实测端点活性；
   结论写回侦察簿（表头 `源 | 本轮实测 | 定案`，签名已在 extract_candidates 登记）。
   第一批（US 官方 5 条）已按此办结：2 条活跃待接、1 条路径漂移、1 条出口拦截、1 条维持。
2. **复测不改「接不接」**：活性事实只更新候选清单；出单判定仍归生成链（定案岗 +
   人审门）。Census 这类「换出口可能就通」的，解锁动作记进侦察簿，不在这里顺手接。
3. **探针负例回归**：花卉 P4 的教训（首页可达 ≠ 有数据通道）——复测一个源要看的是
   **结构化数据端点**，不是首页 200。四个 ★接 门户站全部因这一条降级。
