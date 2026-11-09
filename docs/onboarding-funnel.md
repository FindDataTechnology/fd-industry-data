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
