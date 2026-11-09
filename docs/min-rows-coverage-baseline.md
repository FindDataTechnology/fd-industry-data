# `min_rows_per_run` 覆盖率基线（2026-11-08）

> 结论先说：**不要给全部源批量补这个字段**。实测 124 个有成功运行的源里，只有 **48 个**能安全声明；
> **14 个**声明后会天天误报（它们本来就是"本轮无新数据"的增量源）。

## 1. 现状

- `spiders/*/manifest.yaml` 共 **160** 份，声明 `min_rows_per_run` 的只有 **1** 份（`aiph-stats`，20 行）。
- 声明 `data_period` 的有 **133** 份。
- 该字段的作用：健康巡检的规则 1c——"运行成功但写出 0 行"要出工单（花卉线 F3 的问题）。
  没有声明的源，这个规则**不生效**，于是"每天绿但板上没数"。

## 2. 为什么不能批量补

近 90 天 `crawl_runs` 里成功运行的 `rows_written` 分布，按"0 行占比"分桶：

| 桶 | 源数 | 运行数 | 含义 | 该怎么做 |
|---|---|---|---|---|
| A 从未出现 0 行 | **39** | 2313 | 快照型，每轮必出数 | **可安全声明** |
| B 0 行占比 <20% | **9** | 238 | 多数出数、偶有 0 | 可声明，阈值取低分位 |
| C 混合 | 3 | 415 | 需人看 | 逐源判定 |
| D 样本不足（<5 次成功） | 59 | 147 | 判不了 | 等跑够再判 |
| E 常态 0 行 | **14** | 1436 | 增量型：本轮无新数据是**正常** | **不可声明**，改用"连续 N 期 0 行" |

E 桶里是 `wbgapi`（58/58 全 0）、`ccdi-supervisory-crawl`（29/29）、`mfa-treaty-*`、
`party-regulation-*`、`flk-law-embed` 这类——它们本来就没有"每轮必出数"的语义。

## 3. A 桶清单与建议阈值

阈值取**观测 p10**（向下取整到合理档），且必须 **小于等于调度上限**（默认 `limit: 100`，
所以 p50=100 的源其实是"每轮都被上限截断"，阈值应该设成上限的一个比例而不是 100）。

A 桶前 20 个（完整清单用第 5 节 SQL 复算）：

| source | 成功次数 | p10 | p50 | 建议 `min_rows_per_run` |
|---|---|---|---|---|
| nmc-weather | 818 | 100 | 100 | 20（上限截断型） |
| eia-v2 | 703 | 100 | 100 | 20 |
| kitco | 63 | 1 | 1 | 1 |
| bls | 47 | 1 | 1 | 1 |
| carbon-monitor | 39 | 100 | 100 | 20 |
| zgdypf-boxoffice | 39 | 100 | 100 | 20 |
| open-meteo | 35 | 100 | 100 | 20 |
| nasa-power | 34 | 100 | 100 | 20 |
| wto-eping | 34 | 10 | 10 | 5 |
| usgs-nwis | 34 | 100 | 100 | 20 |
| noaa-ncei | 33 | 27 | 29 | 10 |
| noaa-swpc | 33 | 8 | 8 | 4 |
| elexon-bmrs | 32 | 32 | 32 | 15 |
| nasa-firms | 29 | 100 | 100 | 20 |
| gdacs | 27 | 6 | 9 | 3 |
| entsoe-e | 25 | 100 | 100 | 20 |
| steam-ccu | 16 | 11 | 12 | 5 |
| un-comtrade | 15 | 4 | 5 | 2 |
| fao-food-price | 10 | 38 | 116 | 15 |
| hkex-disclosure | 9 | 221 | 226 | 50 |

### 3.1 复算补全（2026-11-09，§5 SQL 实测）

上表只留了前 20 个。全表复算共 **40 个** A 桶源（`n≥5 且 zero=0`，近 90 天），
除上面 20 个之外还有下面这些，已全部声明（值取自同一口径：p50=100 被默认上限截断的取 20%，
其余取 p10 的一半左右并向下取整到整档）：

| source | 成功次数 | p10 | p50 | 已声明 |
|---|---|---|---|---|
| worldbank-procnotices | 35 | 100 | 100 | 20 |
| wire-finddatatech-cloud | 31 | 1 | 1 | 1 |
| eurostat-api | 10 | 18 | 100 | 10 |
| socrata-ny | 9 | 100 | 100 | 20 |
| usda-quickstats | 8 | 12 | 12 | 6 |
| mohurd-yearbook | 7 | 14 | 100 | 7 |
| imf-pcps | 7 | 68 | 100 | 30 |
| gb-standards | 7 | 68 | 100 | 30 |
| chinawuliu-logistics | 7 | 27 | 35 | 10 |
| moa-hog-price | 6 | 28 | 36 | 12 |
| boxofficemojo | 6 | 60 | 100 | 25 |
| cisa | 6 | 60 | 100 | 25 |
| fao-empres | 6 | 46 | 84 | 20 |
| socrata-chicago | 6 | 100 | 100 | 20 |
| sec-edgar-facts | 5 | 748 | 835 | 150（分页累加，非 100 截断） |
| openchargemap | 5 | 100 | 100 | 20 |
| amis-market | 5 | 100 | 100 | 20 |
| jodi-gas | 5 | 52 | 100 | 25 |

**刻意不声明的两个**（复算落在 A 桶，但不是漏了）：

- `inmetals`（29 次 / p10=1）—— `parked: true` + `enabled: false` + 无 `schedule`，巡检不覆盖停用源；
  等它复跑再补，现在声明只会留一条永不生效的配置。
- `dispatch-smoke`（45 次 / p10=1）—— 冒烟任务，0 行本属正常（见 C 桶，任务 5.3 待定）。

`limit` 在所有这些 manifest 里都**没有显式声明**，100 是代码默认值，
所以判"是否被上限截断"要看 `p50` 是否**恰好等于 100**，不能看 `p10 ≥ 100`
（`sec-edgar-facts` 的 p10=748 是分页累加，不是截断）。

B 桶（可声明但阈值要低）：`eastmoney-data`(1/57)、`eastmoney-futures`、`fangchan-market`、
`metal-com`、`cpcaauto-monthly`、`nbs-stats`、`portwatch-imf`(6/39)、`ted-notices`(9/34)、
`rmfyalk-case-crawl`、`cninfo-operating`。

C 桶（需人看）：`akshare`（375 次成功里 206 次 0 行——它是一组子适配器的集合，按子命令分别成立）、
`legal-sources-report`（6 次成功 4 次 0）、`dispatch-smoke`（冒烟任务，0 行本属正常）。

### 3.2 B 桶（多数有数，2026-11-09 落地）

B 桶 = 零行占比 <20%：多数时候有数、偶尔真的空。**仍属快照型语义，所以照样声明**，
阈值按任务 3.1 取 p10，p10=0 的取下限 1。

| source | n | zero | p10 | p50 | 已声明 |
|---|---|---|---|---|---|
| eastmoney-data | 59 | 1 | 2 | 2 | 2 |
| portwatch-imf | 40 | 6 | 0 | 100 | 1 |
| eastmoney-futures | 33 | 1 | 1 | 1 | 1 |
| fangchan-market | 31 | 1 | 79 | 79 | 79 |
| cpcaauto-monthly | 30 | 1 | 30 | 31 | 30 |
| nbs-stats | 10 | 1 | 18 | 100 | 18 |
| cninfo-operating | 6 | 1 | 5 | 50 | 5 |

不声明 2 个：`metal-com`（`parked: true`）、`rmfyalk-case-crawl`（遥测源名在 `spiders/` 下没有对应目录，
只有已停用的 `spiders/rmfyalk`；声明按目录落地，名对不上就不能声明）。

**观察期（任务 3.2）**：一周内统计这 7 个源的误报次数，>20% 的回退。
其中 3 个"紧贴下限"（最近一次行数恰等于下限，任何回落即出单）：`eastmoney-data` 2/2、
`eastmoney-futures` 1/1、`fangchan-market` 79/79 —— 若误报，优先回退这三个。

## 4. E 桶该用什么规则

增量源要抓的不是"0 行"，而是"**连续 N 期都没有新数据**"。现有零件已经有：
`reconcile_absent` 的 period-longer-than 判据（花卉线 F14 用过）。
所以 E 桶的正确处置是**声明 `data_period` + 交给周期缺失规则**，不是声明 `min_rows_per_run`。

## 4.1 结构性事实：巡检的遥测面 ≠ 本仓的 manifest 面

复算 133 个遥测源，逐个看 `spiders/<source>/manifest.yaml` 是否存在：

| 桶 | 有本仓目录 | 无目录 |
|---|---|---|
| A_可声明 | 40 | 0 |
| B_多数有数 | 8 | 1 |
| C_混合 | 1 | 2 |
| D_样本不足 | 65 | 2 |
| E_常态0行 | **0** | **14** |
| 合计 | 114 | 19 |

**E 桶 14 个源在本仓一个 manifest 都没有**（`federation-observer`、`wbgapi`、`mfa-treaty-*`、
`ccdi-supervisory-*`、`gov-rules-*`、`party-regulation-*`、`rmfyalk-case-load`、`flk-law-*`）。

但这**不是覆盖缺口**：`config/health-scope.json` 早在 2026-11-06
（`add-fleet-lifecycle-adjudication 1.3`，commit `b0869d2`）就把 17 个跨仓特殊 Job 显式排除，
逐源带理由——"巡检判定的对象=本仓 manifest 驱动的舰队单元；下列跨仓特殊 Job 不属本巡检范围，
其遥测走 report_special_run/federation-observer 兜底面"。

对账（90 天口径，133 个有成功记录的源）：

| 项 | 源数 |
|---|---|
| 无本仓 manifest | 19 |
| 其中已在 `excluded_sources` | **17** |
| **真正未被排除、也没有 manifest** | **2** |

残余两个是 `flk-law-backfill`、`polygon`（都在 D 桶，n=3 / n=1）。

**残余会漂移**：换 30 天窗口（127 个出现过的源，含失败）再算，残余变成
`example-stock`、`yfinance`。也就是说新源冒出来时，若既没 manifest 也没进排除表，
会被**静默归入 `skipped_unknown_period`** 而不报警——这是 scope 卫生问题，
不是本能力能解决的（已记为后续任务 7.2）。上一轮我把"19 个无 manifest"写成"机制覆盖不到"，
是我没先查排除表，已经更正。

## 4.2 E 桶：本桶不声明 `min_rows_per_run`

E 桶 14 个源零行占比 ≥80%（`flk-law-embed` 22/22、`federation-observer` 1007/1007），
属**增量型**语义：本轮没有新数据是正常的。按 spec 的「增量型源不得用行数下限判缺」，
本桶 MUST NOT 声明行数下限，正确的缺失判据是**周期缺失**（连续多个周期无新数据）。

写在这里是为了防止后来人"看它天天 0 行，顺手补个下限"——那会制造每天重出的误报单。

## 5. 复算 SQL

```sql
with s as (
  select source, rows_written rw from crawl_runs
  where status='success' and started_at > now() - interval '90 days'
), a as (
  select source, count(*) n,
         count(*) filter (where coalesce(rw,0)=0) zero,
         percentile_cont(0.10) within group (order by coalesce(rw,0)) p10,
         percentile_cont(0.50) within group (order by coalesce(rw,0)) p50
  from s group by source
)
select source, n, zero, p10::int, p50::int,
       case when n<5 then 'D_样本不足'
            when zero=0 then 'A_可声明'
            when zero::numeric/n < 0.2 then 'B_多数有数'
            when zero::numeric/n < 0.8 then 'C_混合'
            else 'E_常态0行' end bucket
from a order by bucket, n desc;
```

入口：`dashboards/sql/fd_open_data.sh`（只读，走 mesh `100.64.0.3:30432`）。

## 6. C 桶判定：`akshare` 到底该怎么看（2026-11-08 补）

`akshare` 在 `crawl_runs` 里 375 次成功、206 次写出 0 行——看起来像重灾区，但**它不是"每轮必出数"的源**：

- `crawl_runs` 里**没有子命令维度**（`metrics` 全空、`identity_alias` 全空），所以按行数下阈值无法区分它的 11 个子适配器。
- 真正的取数证据面是 `fetch_log`（有 `function_id`）。近 30 天按 function 拆：

| function_id | ok | 失败 | 失败率 |
|---|---|---|---|
| 489 | 1400 | 42 | **2.9%** |
| 475 / 524 / 1118 / 1119 / 1123 / 1129 / 1147 / 1149 / 1151 / 1152 | 336–1050 | 0 | 0.0% |

结论：**`akshare` 不声明 `min_rows_per_run`**。它的健康判据是 `fetch_log` 的 per-function 失败率
（当前仅 489 有 2.9% 波动，其余全清）。把它当快照源下阈值会让"本轮无新增观测"天天报错。

复算：

```sql
select function_id,
       count(*) filter (where status='ok')  ok,
       count(*) filter (where status<>'ok') bad,
       round(100.0*count(*) filter (where status<>'ok')/count(*),1) err_pct
from fetch_log
where source='akshare' and timestamp > now() - interval '30 days'
group by 1 having count(*) >= 50 order by err_pct desc;
```

## 7. 落地顺序

1. 先给 A 桶 39 个源声明（改动只是 manifest 加一行，零代码）。
2. B 桶 9 个源阈值取 p10，观察一周误报率。
3. D 桶 59 个源：等成功次数 ≥5 再判（写进巡检的"待定"清单，不要现在拍）。
4. E 桶 14 个源：**只补 `data_period`**，走周期缺失规则。
5. C 桶 3 个源：`akshare` 需按子命令拆，`legal-sources-report` 与 `dispatch-smoke` 需人定语义。

验收：健康巡检对 A 桶源产生 0 条"行数不足"误报，且人为把某个 A 桶源的产出压到 0 时确实出单。
