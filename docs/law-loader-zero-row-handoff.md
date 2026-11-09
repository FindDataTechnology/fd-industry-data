# law / loader 线零行交接单

> change `harden-crawl-delivery` 任务 3.5。**本文件不改那些仓的代码**，只把实测
> 证据、归因与还缺什么证据写清楚，交给对应仓库的负责人。

## 先修正一个说法

3.1 的分线表写着「零行绝对量几乎全在 law/loader 线（83.3%）」。这句话作为**观测**
成立，但作为**归因**是错的 —— 查 `policy_runs` 之后，那些零行里绝大部分不是
「取数失败」：

```
近 7 天 policy_runs（概念线）：
  redundant   99     ← 计划有 cells，但 rows_new=0（重复爬同一个冻结窗口）
  failed      55     ← 基础设施：launch timeout / deadline exceeded / 无可用 worker
  no_op       21     ← plan_cells = 0：计划本身为空，所以"成功但没做事"
  zero_yield   4
  success     31
```

**所以「先修 akshare」这个建议是错的**。正确的是：先分清零行的三种成因，因为
它们的修复归属完全不同（政策窗口 / 集群容量 / 概念登记），没有一项在爬虫代码里。

## 实测（近 7 天，2026-11-10 复算）

| 源 | ok | zero | rows_total | 末次运行 | 末次错误 |
|---|---|---|---|---|---|
| `akshare` | 140 | 110 | 17,222,863 | 2026-11-09 13:37 | `job cheap/crawl-policy-7-… failed before the run` |
| `wbgapi` | 14 | 14 | 0 | 2026-11-09 06:07 | （空） |
| `ccdi-supervisory-load` | 7 | 7 | 0 | 2026-11-08 21:10 | （空） |
| `party-regulation-load` | 7 | 7 | 0 | 2026-11-08 21:00 | （空） |
| `mfa-treaty-load` | 7 | 7 | 0 | 2026-11-08 20:40 | （空） |
| `mfa-treaty-crawl` | 7 | 7 | 0 | 2026-11-08 19:20 | （空） |
| `flk-law-embed` | 7 | 7 | 0 | 2026-11-08 21:30 | （空） |
| `ccdi-supervisory-crawl` | 7 | 7 | 0 | 2026-11-08 19:50 | `fetch failed` |
| `party-regulation-crawl` | 7 | 7 | 0 | 2026-11-08 19:40 | （空） |
| `rmfyalk-case-load` | 7 | 6 | 15 | 2026-11-08 21:30 | （空） |
| `gov-rules-crawl` | 7 | 5 | 6 | 2026-11-08 19:30 | （空） |
| `gov-rules-load` | 7 | 5 | 6 | 2026-11-08 20:50 | （空） |

两条**结构性事实**必须写在前面：

1. **这些源都不写 `crawl_items`**（实测 akshare / wbgapi 的 `crawl_items` 计数都是 0）。
   它们是 loader / 概念线执行体，产物落在 `semantic_observations`。所以「零行」
   在 `crawl_runs.rows_written` 里，不能用行业线那套「看条目」的方法复验。
2. **`akshare` 的 110 次零行里，带 `error_head` 的是 0 条**（实测 0/110）。零行且无错
   = 空心成功，具体成因只能从 `policy_runs` 反查。

## 逐源归因

### `wbgapi`：计划为空（已确证，不需再取数）

```
crawl_policies: id=4 country-macro-yearly (yearly, since_last, ["wbgapi"])
                id=6 wbgapi-wdi-probe    (yearly, since_last, ["wbgapi"])
policy_runs:    近 8 次全部 status=no_op, plan_cells=0, rows_new=null
```

`plan_cells = 0` 是判据：**planner 没编出任何 cell**，执行体自然没有取数，于是
`crawl_runs` 记的是「成功、0 行」。这不是 wbgapi 上游的问题 —— 直连实测
`api.worldbank.org/v2/country/CHN/indicator/NY.GDP.PCAP.CD?format=json&date=2023`
返回 200 且有值（12951.18）。

- 归属：`fd-open-data-mcp` 的 planner / 登记面（`crawl_planner`、`entity-source-config`）
- 还缺的证据：这两个 policy 的 concept/entity 绑定是否为空（`enabled=t` 但无实体可编），
  以及 `adapters/wbgapi.py` 的 `build_params` 是否因 `binding.column` 缺失而不生成 cell
- **不要在 wbgapi 上改取数逻辑** —— 取数是好的，计划是空的

### `akshare`：34% 有行、66% 零行，行数量级跨 4 个数量级

```
2026-11-09 13:37   1,812 行
2026-11-09 12:37   4,136,151 行
2026-11-09 10:22   25,960 行
2026-11-09 07:07   0
2026-11-09 06:52   600
2026-11-09 06:37   0 / 3 / 0（同一分钟三条）
```

同一分钟出现三条 `crawl_runs`（0/3/0 行）= 三个 policy 并行，各自覆盖不同 cell。
所以 `akshare` 的零行率**不能按源看**，要按 policy 看：

- 归属：`fd-open-data-mcp`（policy 窗口与 planner）
- 还缺的证据：
  1. 每个 akshare policy 的 `date_policy`（`redundant` 99 次说明大量 policy 在
     重复爬同一个冻结窗口 —— 这是 `crawl-visibility` 已有「redundant streak」信号的那类）
  2. `failed` 55 次的分布：`launch failed: <urlopen error timed out>`（24 次）、
     `deadline exceeded after 60s`（9 次）、`pod 从未创建`（3 次）、
     `no eligible worker cluster`（2 次）——**全部是基础设施，不是源**
- 建议动作（不在本 change）：把 99 次 redundant 的 policy 窗口改成会推进的
  `trailing`，或降低其频率；`no eligible worker cluster` 归集群容量（`clusters` 表）

### law 线 `.mjs` 单元（ccdi / mfa / party / gov-rules / flk）

六个源两类各 7/7 零行（crawl + load 成对），产物 0–15 行。`ccdi-supervisory-crawl`
的末次错误是 `fetch failed`，其余为空。

- 归属：`fd-law-data`（`bin/*.mjs`，k8s CronJob 见 `fd-law-data/k8s/*-cronjob.yaml`）
- 还缺的证据：
  1. 这些源是否**按设计就是这样**（抓取 + 装载成对，crawl 产 0 行、load 消费 0 行）
     —— 若是，它们应像 E 桶那样**显式标注「常态 0 行」**，而不是每轮报成功
  2. `fetch failed` 的具体上游与 HTTP 状态（`error_head` 只留了这两个词，信息不足）
  3. 该不该纳入本仓巡检口径：它们已在 `config/health-scope.json` 的
     `excluded_sources` 里（联邦成员），本仓不对其出单

## 给接手方的三句话

1. **`wbgapi` 的计划是空的**（`plan_cells=0`，已确证），修 planner 不修爬虫。
2. **`akshare` 的零行主要来自重复窗口与集群容量**（99 redundant + 55 failed），
   按 policy 看而不是按源看。
3. **law `.mjs` 那六个先确认是不是「常态 0 行」**；若是，改为显式标注，
   让它们从「成功但空」变成「按设计无新数据」——这正是本 change 给行业线做的
   `ok_empty` 那件事，只是这边还没做。

## 复算

```bash
# 分线总数（本仓）
python3 scripts/zero_row_by_line.py --days 7

# 概念线成因分布
psql "$FD_CRAWL_DB_URL" -c "select status, count(*) from policy_runs
  where started_at > now()-interval '7 days' group by 1 order by 2 desc"
```
