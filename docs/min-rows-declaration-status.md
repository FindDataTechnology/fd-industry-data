# `min_rows_per_run` 声明：做到哪一步了

2026-11-09，对应 change `declare-health-min-rows` 任务 2.1 / 2.2。

## 结论先说

**45 个源已写入并验证通过，但还没有提交。**（A 桶 38 + B 桶 7；另有 4 个刻意不声明，见下）

上一版这份文档写的是"一个 manifest 都没改"，那是因为工作树里躺着 136 份别人（已归档 change）
的未提交改动。后来查清了归属，判为可写，于是落地 —— 但**提交这件事仍然需要人定**（见最后一节）。

## 为什么当初停了，又为什么可以继续

```
$ git status --short spiders/ | wc -l
138                      # 136 M + 2 ??
$ git diff --numstat spiders/ | awk '{a+=$1; d+=$2} END {print a, d}'
410 2                    # 新增 410 行，删除 2 行
```

改动内容（每个文件尾部追加三行）：

```yaml
# 接入声明（change: define-ingest-revision-and-coverage, decision 4A）
revision_policy: overwrite_with_history
coverage_capability: gaps_only
```

`410 = 136 × 3 + 2`：多出来的 2 行是 `spiders/inmetals`、`spiders/metal-com` 里
`enabled: false` 挪了位置（配套 2 行删除，无语义变化）。**整棵树只有这一个逻辑改动。**

`openspec/changes/archive/2026-11-09-define-ingest-revision-and-coverage` 已经归档 = 已经裁决。
所以这些改动是**已批准的产出，只是没人提交**。

判它是"活写手"还是"写完了没提交"，看的是 mtime 的**分布**而不是最新的那一个：

```
最新 1.04h  中位 1.04h  最旧 42.38h     分桶 {<2h: 136, >19h: 2}
```

136 份的 mtime 全挤在同一个时刻，之后一小时零写入 —— **一批写完了，不是在持续写**。
（若真是活跃会话，会看到大小不一的年龄，最新那份只有几分钟。）

## 落地时加的并发护栏

即使判定"写完了"，这仍是一棵**共享脏树**：我在读和写之间它可能变。所以 `--apply`
每写一个文件前重读一遍，内容变了就跳过该文件而不是覆盖：

```python
if manifest.read_text(encoding="utf-8") != original:
    skipped.append(manifest)
    continue
```

`plan()` 因此返回 `(path, original, new_text)` 三元组 —— 光有"要写什么"不够，
还得记住"写之前它长什么样"。

**这道护栏是验证过的，不是写着好看**：把护栏从副本里删掉，重跑同一场景 ——

| 实现 | written | skipped | 另一会话那行还在？ |
|---|---|---|---|
| 无护栏（旧） | 1 | 0 | **不在**（被静默吃掉） |
| 有护栏（新） | 0 | 1 | 在 |

也就是说，没有这道护栏，`--apply` 会**无声地**抹掉一份已归档 change 的产出，而
`git diff` 里看不出任何异常（因为新写的内容覆盖了它）。

## 验证结果

| 项 | 结果 |
|---|---|
| 脚本单测 `tests/test_declare_min_rows.py` | **9/9**（含并发护栏正反两例、跳过项须有理由） |
| `--apply` 实际写入 | **45 / 45**（首批 20 + §5 复算补的 18 + B 桶 7） |
| 幂等复跑 | `可声明 0 个源`，20 个全部"已声明，跳过" |
| 落点正确性（紧跟 `data_period:`） | **20/20**，字段在第 7–12 行 |
| `validate_manifests.py` | **`162 manifests, 0 violations`** |
| 两侧读取器一致（真实文件） | **45/45 同值** |
| 全量套件（除 3 个缺 lxml 的模块） | 53 红 / 1166 绿 / 2 skip；**与 HEAD 基线对照零新增红**（基线 55 红，我这棵树的失败集合是它的严格子集） |

读取器一致性的实测值（45 个源逐一同值，下面只列第一批；第二批见 baseline §3.1、B 桶见 §3.2）：

```
nmc-weather=20  eia-v2=20  kitco=1  bls=1  carbon-monitor=20  zgdypf-boxoffice=20
open-meteo=20  nasa-power=20  wto-eping=5  usgs-nwis=20  noaa-ncei=10  noaa-swpc=4
elexon-bmrs=15  nasa-firms=20  gdacs=3  entsoe-e=20  steam-ccu=5  un-comtrade=2
fao-food-price=15  hkex-disclosure=50
```

单个文件长这样（`spiders/bls/manifest.yaml`）：

```yaml
data_period: day
min_rows_per_run: 1    # runs=47 p10=1 p50=1; p10=1 — 见 docs/min-rows-coverage-baseline.md §3
```

## 过程中的四个真缺陷（都已修）

1. **单个 alternation 正则匹配到 `name:` 而不是 `data_period:`** —— 因为 `name` 在文件里出现更早，
   于是字段被插到了 `name:` 和 `schedule:` 之间，把"这个源什么时候跑"的两行硬生生隔开。
   **我第一版还以为是换行问题，推理了两次都没对，打印真实字节才看见。**
2. **错误路径自己会抛**：`manifest.relative_to(REPO)` 在源缺失分支上抛 `ValueError`，
   把"少一个源"变成 traceback。
3. **注释里出现两个 `#`**，YAML 合法但读起来像两条注解。
4. **无并发护栏时静默覆盖**（上表已证），且这种丢失在 diff 里不可见。

## 第二批 18 个（§5 SQL 复算才发现）

baseline §3 当初只列了前 20 个 A 桶源。按 §5 的 SQL 复算全表，A 桶其实是 **40 个**，
去掉已声明的 20 个，还剩 20 个 —— 其中 2 个刻意不声明，其余 18 个已落地：

- **`inmetals`**：`parked: true` + `enabled: false` + 无 `schedule`。巡检不覆盖停用源，
  现在声明只会留一条永不生效的配置。等它复跑再补。
- **`dispatch-smoke`**：冒烟任务，0 行本属正常（任务 5.3 待定）。

这两个写在脚本的 `SKIPPED` 字典里，而不是"默默漏掉"——否则下一个读的人分不清
"忘了"和"决定不加"。单测里有一条断言两者不相交、且每个跳过项都必须带理由。

阈值口径顺带修正了一处：`limit` 在这些 manifest 里**都没有显式声明**（100 是代码默认），
所以判"被上限截断"要看 `p50` 是否**恰好等于 100**，不能看 `p10 ≥ 100`。
`sec-edgar-facts` 的 p10=748 是分页累加，不是截断，按 20% 取 150 才是对的。

## 第三批：B 桶 7 个（任务 3.1）

A 桶之外还有 B 桶（零行占比 <20%：多数有数、偶尔真的空），仍属快照型语义，照样声明，
阈值取 p10、p10=0 的取 1：

| source | n | zero | p10 | 已声明 |
|---|---|---|---|---|
| eastmoney-data | 59 | 1 | 2 | 2 |
| portwatch-imf | 40 | 6 | 0 | 1 |
| eastmoney-futures | 33 | 1 | 1 | 1 |
| fangchan-market | 31 | 1 | 79 | 79 |
| cpcaauto-monthly | 30 | 1 | 30 | 30 |
| nbs-stats | 10 | 1 | 18 | 18 |
| cninfo-operating | 6 | 1 | 5 | 5 |

不声明 2 个：`metal-com`（`parked: true`）、`rmfyalk-case-crawl`（遥测源名在 `spiders/` 下
没有对应目录，只有已停用的 `spiders/rmfyalk`；声明按目录落地，名对不上不能硬声明）。

**加进去之后巡检出单数没变**（10 张 / rule1c 3 张），7 个源的最近一次行数都在下限之上。
其中 3 个"紧贴下限"——`eastmoney-data` 2/2、`eastmoney-futures` 1/1、`fangchan-market` 79/79——
任何回落即出单，已写进任务 3.2 的观察名单，观察期内误报优先回退这三个。

## 上线前实测：规则会误报 10 个，改在规则而不在阈值

声明写完不等于能用。按真实 `crawl_runs` 逐源算"窗口内有几次成功低于下限"：

```
gdacs 3/27(最低2)  boxofficemojo 25/6(20)  jodi-gas 25/5(20)  eurostat-api 10/10(2)
imf-pcps 30/7(20)  mohurd-yearbook 7/7(4)  gb-standards 30/7(20)  open-meteo 20/36(5)
cisa 25/6(20)  un-comtrade 2/15(1)
```

**10 个源会在巡检第一次跑时就各自出一张工单**，而它们最近一次都正常。

根因不在阈值，在规则 1c 的实现：它扫**回看窗内任意一次成功**，于是历史上一朝发生过一次低产，
该源就永久挂单，直到那条 run 滑出窗口。而它自己的 trace 文案写的是"最近成功 N 次中最低"——
**说明文字和实现对不上**。

规格（`source-output-floor`）只承诺两件事：成功但**写出 0 行**要出单；上限截断型源
"上游少给几条时 MUST NOT 误报"。让一次陈年异常永久生效，两条都不支持。

### 改法

`triage.py` 规则 1c 改为**只看最近一次成功**：

```python
latest_rows = last_success.rows if last_success.rows is not None else 0
if latest_rows < floor:
    ...
```

历史低位降级为**诊断信息**写进 trace（"窗口内另有 N 次低于下限"），不参与判定——
它区分"这个源一向如此"和"这次突然掉"。副作用是好的：工单会在下一次正常运行时**自然消解**。

反向验证（同一组输入，旧实现 vs 新实现）：

| 场景 | 旧实现出单 | 新实现出单 |
|---|---|---|
| 两个月前 2 行、最近 100 行 | **是** | **否**（判别成功） |
| 两个月前 100 行、最近 5 行 | 是 | 是 |
| 两次陈年低位 + 最近 3 行 | 是 | 是 |

### 改完再算一次真实分布

38 个 A 桶源：**35 个 ok / 3 个出单**（`boxofficemojo` 25/20、`cisa` 25/20、`gb-standards` 30/20）。
7 个 B 桶源：**7 个全部 ok**，加进去之后巡检出单数**不变**（仍 10 张、rule1c 仍 3 张）。
三个都是"最近一次只写了 20，平时 100"。

## 顺带查出来的一件事：2026-11-06 的平台侧波次

这三个不是各自坏了。按当天全体源统计：

```
     d      | runs | srcs | at20 | at100
------------+------+------+------+-------
 2026-11-05 |   35 |   12 |    0 |    31
 2026-11-06 |   96 |   48 |   48 |    31     ← 48 个源各跑一次、每次恰好 20 行
 2026-11-07 |   48 |   18 |    0 |    33
```

前后两天 20 行出现 **0 次**。也就是说 11-06 有一次**全局小 limit 的额外波次**（每源一次、恰好 20 行），
那不是源退化。

### 波次的身份：未走调度的批量动作

按 `kind` + 镜像来源拆 11-06：

```
    kind     | imgclass | runs | at20 | srcs
-------------+----------+------+------+-----
 runtime     | tagged   |  108 |    0 |   24     ← 带标签的生产运行，20 行一次没有
 runtime     | unknown  |   82 |   48 |   63     ← 全部 48 次 20 行都在这一格
 concept     | null     |   56 |    0 |    2
 federation  | tagged   |   10 |    0 |   10
```

`FD_IMAGE_TAG` 由 k8s dispatcher/cronjob 注入，未设时默认字符串 `"unknown"`
（`runner_cli.py` 三处 `os.environ.get("FD_IMAGE_TAG", "unknown")`）。
所以这 48 次是**没走 k8s 调度**的调用；且每个源当天只跑了这一次（`afdc` 两次：20 与 225），
`kind` 仍是 `runtime`，事后完全看不出是批量动作。

### 观测缺口：已经补上记录侧

`crawl_runs` **没有记录本轮用的 limit**（`metrics` 为 NULL，也无 limit 列），
所以从数据上无法区分"这次全量跑得少"和"这次用了更小的 limit"。

记录侧已补：`runner_cli` 把本轮 `--limit` 写进 `crawl_runs.metrics.limit`——
`metrics` 本来就在传（`dropped_params` / 窗口 / `owner` / `upstreams` / `accept`），
加一个键不动表结构。两条原本"精确集合"的断言已同步改成包含 `limit`，
并发规则是：**这个契约变宽必须显式改测试，不能顺手放过**。

判定侧（规则 1c 跳过 `limit < floor` 的运行）**暂不启用**：历史 run 都没有 limit，
现在切会让同一回看窗里的比较口径不一致——一部分源按"可比"判、一部分按"不可比"跳过。
等窗口内全部带 limit 之后再做。

## 端到端：在真实遥测上跑巡检，以及一个差点骗过我的上限

在真实 30 天遥测（3402 条、127 个源）上跑 `scripts/health_inspect.py --snapshot --emit` 到临时目录：
**出单 10 张，其中因行数出单的只有 3 张**（`boxofficemojo` rows=20<25、`cisa` rows=20<25、
`gb-standards` rows=20<30，均为 2026-11-06）。另外 7 张走的是既有规则（`rule3` 反爬、
`rule5` 网络、`rule6a` 源改版、`rule2` 证据不足），**没有被行数规则抢走**——
`test_failed_runs_keep_their_own_category` 那条纪律在真实数据上也成立。

反向验证：把快照里 `nmc-weather` 最近一次成功从 **100 行改成 0**，同一份快照重跑，
工单出现且是 `rule1c:success-but-too-few-rows 最近一次成功 rows=0 < min_rows_per_run=20`，
rule1c 单从 3 张变 4 张。规则确实会因为"0 行"出单，不只是因为在测试里被调用。

### 差点骗过我的东西：每日出单上限

第一次跑反向验证时，篡改后的快照**只出 5 张、且没有 nmc-weather**，看起来像"规则不出单"。
实际是 `HealthConfig.max_daily_tickets` 默认 **5**，多出来的被 `deferred: daily cap reached` 挡掉了：

```
deferred 共 6 个；其中 nmc-weather 1 个 → {'reason': 'daily cap reached'}
```

把 `max_daily_tickets` 提到 500 重跑，才拿到真实数字（10 / 11 张）。
**以后任何"巡检出了几张单"的结论，都必须先确认这个上限没有截断**，
否则统计到的是限额而不是检出能力。

## 一次差点误判为"功能坏了"

用 `min_rows_floor(Path("spiders"), s)` 核对真实文件时，20 个源全返回 `None`，
而 `_min_rows_per_run` 都返回了值 —— 看起来是"写入没生效"。实际是**我的调用传错了根**：
`min_rows_floor` 内部自己拼 `spiders/<source>`，我多传了一层 `spiders`。

之所以错得这么安静，是因为它把 `FileNotFoundError` 一起吞了：

```python
except (yaml.YAMLError, OSError):
    return None          # 路径不存在 = 未声明，两者无法区分
```

这是**既有代码**的行为、本次未改，但下次核对时务必先断言 manifest 路径真实存在，
否则"读不到"会被当成"没声明"。

### 那个缺口已经补了：未登记的源现在有名有姓

`skipped_unknown_period` 以前把两件事混在一起：**有 manifest 但定不出周期**（本仓的源）
和**根本没有 manifest**（没登记的新面孔）。后者被静默跳过，谁也不知道。

已分开（`Plan.unregistered` + 摘要字段 `unregistered_sources`）。真实快照 + 生产 scope 实跑：

```
unregistered_sources: ['example-stock', 'yfinance']
skipped_unknown_period: []
```

3 条新测试钉住三条边界（无目录→unregistered；已排除→不出现；有目录缺周期→仍进 skipped），
并做了反向验证：删掉分流分支后 1 failed / 2 passed。

## 更正：那 19 个"没有 manifest"的源，17 个是**早就排除**的

上一版这份文档把"133 个遥测源里 19 个无 manifest"写成了"机制覆盖不到"。**这是我漏查了一步。**

`config/health-scope.json` 早在 2026-11-06（`add-fleet-lifecycle-adjudication 1.3`，
commit `b0869d2`）就显式排除了 **17 个跨仓特殊 Job**，逐源带理由：
"巡检判定的对象=本仓 manifest 驱动的舰队单元；law 线 k8s CronJob / 适配器 / observer
不属本巡检范围，其遥测走 `report_special_run` / `federation-observer` 兜底面"。

对账（90 天，133 个有成功记录的源）：

| 项 | 源数 |
|---|---|
| 无本仓 manifest | 19 |
| 其中已在 `excluded_sources` | **17** |
| 真正未被排除、也没有 manifest | **2**（`flk-law-backfill`、`polygon`，均 D 桶） |

所以本仓 A 面 40 个全有目录、也全部处理完（38 声明 + 2 跳过）；
E 桶那 14 个不是"该补没补"，是**按裁决不归这里判**。

### 真正剩下的那件事：残余人选会漂移

换 30 天窗口（127 个出现过的源，含失败）再算，残余变成 `example-stock`、`yfinance`——
和 90 天口径的两个完全不同。说明**新源冒出来时，若既没有 manifest 也没进排除表，
会被静默归入 `skipped_unknown_period` 而不报警**（那一轮 `skipped_unknown_period` 有 16 条）。

这是 scope 卫生问题，不是行数下限能力能解决的：排除表是手工维护的反向白名单，
新成员不会自己报到。已记为后续任务 7.2（巡检摘要里点名"无 manifest 且未排除"的源）。

## 底座层面的那句话仍然成立，但理由要改

`min_rows_per_run` 只覆盖"有 manifest 的产品"。law 线（`fd-law-data`，Node/Docker +
k8s CronJob 直接写库，**根本没有 manifest 面**）与 scraw 适配器（`akshare`/`wbgapi`）
走的是 `report_special_run` 兜底面，而不是被漏掉了。

要覆盖它们，得让声明**与遥测同源**（例如以 source 为键的声明表），或让这些产品也接 manifest。
这是底座层面的选择——但它现在是一条**有意识的边界**，不是一处遗忘。

### 顺手抓到的解析器缺陷：一段调用栈被当成"源名"

翻生产 `reports/health-tickets/last-run.json`（2026-11-07 那轮）时看到：

```
skipped_unknown_period: [
  'at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)',
  'example-stock', 'fred-cpi', ...
]
```

第一项是一段 **Node 调用栈**。根因在 `load_runs_from_psql`：它把短行补成 7 列
（`cols += [""] * (7 - len(cols))`），于是 psql 输出里混进的任何非表格文本都能
"长成"一条遥测记录——源名取到整行、状态为空。

补列本身是对的（运维的 SQL 可能少给 watermark 列），但**补列之前必须先确认这行的状态合法**。
已改为状态列 ∈ `{success, failed, cancelled, partial}` 才接受，丢弃的行数走 stderr 报出来。

这个缺陷的代价不只是多一条假源：那一轮 `healthy` 只有 84（我本地跑同一窗口是 101），
而且 12 条"周期未知"里混着 `fred-cpi`、`hkex-disclosure`、`sec-edgar-facts` 这些
**明明有 manifest 的源**——解析错位会把真实源挤进"跳过"，等于漏判。

3 条测试钉住（调用栈行被丢 / 正常行与垃圾行共存 / 5 列短行仍合法），
反向验证：去掉校验后 2 failed 1 passed。健康相关套件 270 passed。

## 生效条件：得落到 **GitHub 的 main**，而它和本地不是同一条线

巡检的实际跑法（`.github/workflows/health-inspect.yml`）：

```
cron: "17 19 * * *"                                        # 每日 03:17 CST
git clone --depth=1 https://github.com/FindDataTechnology/fd-industry-data.git src
python3 scripts/health_inspect.py --from-pg --emit
git add reports/health-tickets && commit && git push origin HEAD:main
python3 scripts/health_inspect.py --from-pg --deliver --submit-new --sync-status
```

**它克隆的是 GitHub，不是本地工作树，也不是 gitee。** 而本地 main 跟的是 gitee：

| 远程 | main | 时间 | `spiders/` 目录数 |
|---|---|---|---|
| github (origin) | `36d5836` | 2026-11-09 01:25 | **127** |
| gitee / 本地 HEAD | `7d94f1b` | 2026-11-09 15:49 | **163** |

（`git ls-remote` 实测，不是陈旧引用。）

### 差的那 36 个目录：不是被删，是还没上去

```
只在 github 有: []            # GitHub 是 gitee 的子集
只在 gitee  有: 36 个
```

其中 34 个来自**一个提交**：`c1daa91`（2026-11-09 11:54，shichengsi，
"spiders: 批次六交付（34 个新单元 + 验收台账）"），该提交**只在 gitee/main 上**。
`git log github/main -- spiders/<x>/manifest.yaml` 对它们全是空的——
**从未在 GitHub 存在过**，所以不是谁裁剪掉了，是这批交付还没推到 GitHub。

我声明过的 45 个里有 **2 个在这 36 个里**：`hkex-disclosure`、`sec-edgar-facts`。
它们在有 manifest 的线上能声明，但巡检读的那条线上连目录都没有。

### 这解释了我先前看不懂的那批"周期未知"

生产 `last-run.json`（2026-11-07）的 `skipped_unknown_period` 里有
`fred-cpi`、`hkex-disclosure`、`sec-edgar-facts`——我上一轮归因为"解析器错位"，
**那只对了一半**：解析器确实把一段 Node 调用栈当成了源名（已修），
但这三个是**真的在 GitHub 上没有 manifest**。同一窗口本地算出 101 healthy、生产只有 84，
差的正是这批只在 gitee 上的单元。

所以准确的说法是：**巡检面 = GitHub 的 manifest 面**，而交付面 = gitee。
两条线没同步之前，任何以 manifest 为载体的声明都只能覆盖 GitHub 上已有的那 127 个。

### 量化：生产巡检漏看 11 个源——但今天不漏信号

同一回看窗（2026-10-08 → 11-08，生产那轮的真实窗口）+ 两种 manifest 面各跑一次：

| manifest 面 | healthy | 出单 | 顺延 | 未登记 |
|---|---|---|---|---|
| `github/main`（巡检实际读的） | **84** | 5 | 2 | **11** |
| 本地 / gitee（交付实际落的） | 89 | 5 | 6 | 2 |

`github/main` 那行的 84 **与生产 `last-run.json` 的 84 完全一致**——两条线的目录差就是全部原因，
没有别的残差。（先前拿"截止今天"的快照比"截止 11-07"的生产数字得出 101 vs 84，
那是**标尺不同**，不是解释不清。）

**这 11 个源今天有没有被漏掉的真问题？没有。** 逐个算 verdict（不是看它在不在出单列表里）：

```
fred-cpi / hkex-disclosure / ico-coffee-price / jp-foodservice-trend / restaurant-rpi
samr-food-inspection / sec-edgar-8k-operating / sec-edgar-facts / tpex-monthly-revenue
→ 9 个全部 rule1:healthy（最近成功 4–8 小时内，尾部无连续失败）
另 2 个（example-stock、yfinance）两条线都没有 manifest
```

所以目录差的代价是**覆盖率**（11 个源的生产可见性为零），不是**信号丢失**。

真正因两条线而不同的信号只有一类：**我新加的 3 张 rule1c 单**
（`boxofficemojo`、`cisa`、`gb-standards`）只在 gitee 视角下存在——
因为 `min_rows_per_run` 写在本地 manifest 里，GitHub 上没有。
（`iea-ev`/`nasa-firms`/`socrata-chicago` 在两边的差异是每日 5 单上限造成的位移，不是判定变了。）

### 我这批改动到"生效"还差三步### 我这批改动到"生效"还差三步

1. 本地 179 份未提交改动落成提交（三类：已归档 change 的 136 份 manifest 批量、
   我的 45 份声明、代码修正 + 7 份新文档/脚本/测试）；
2. 合进 GitHub 的 main（本地与 github/main 分叉 44/63，需先对齐）；
3. 批次六那 34 个单元也得推上去，否则 `hkex-disclosure`/`sec-edgar-facts` 的声明无处可放。

前两步与合并风险我已核：入向 44 条没碰我改的任何文件（其 7 份 manifest 全是农业线）。

## 我在这条线上犯过的同一类错，记三次

三轮里我三次把**推断当结论**发出去，三次都被下一步的实测推翻：

| 我说过 | 实际 |
|---|---|
| "133 个遥测源里 19 个无 manifest，机制覆盖不到" | 17 个早在 `health-scope.json` 里按裁决排除，真残余 2 个且会漂 |
| "生产把 fred-cpi 等判成周期未知，是解析器错位" | 解析器确实有缺陷（已修），但那几个是**GitHub 上真的没有 manifest**，两条线的问题 |
| "漏看的 11 个源里 4 个是真信号" | 逐个算 verdict：9 个**全部健康**；差异来自每日 5 单上限的位移 |

共同点：我都是拿**推导的算术**（差几个、除一下）当测量结果，而没有把那个东西本身跑一遍。
三条纪律，写下来给自己：

1. 报数之前先问"这个数是算出来的还是数出来的"，算出来的不算;
2. 差额一定要落到**逐个对象**，落不下来就说明口径不同（比如窗口末端不同）;
3. 每次"我发现了 X"都要先问一句"有没有一个更普通的解释"，尤其是
   "配置里已经写好的东西"（`health-scope.json` 就差点被我当成缺口报上去）。

## 下一步

### 还差提交（需要人定一件事）

20 份声明与那 136 份已归档改动**混在同一批未提交文件里**。谁先 commit，谁的提交就会把
另一个人的工作一起带走；任何一方 `git checkout -- .` 都会同时抹掉两人的工作。
所以：

- 要么由**拥有那 136 份的会话**一并提交（我的 20 份随之入账）
- 要么明确授权我提交，并按逻辑拆成两个提交（`define-ingest-revision-and-coverage` 的批量 +
  `declare-health-min-rows` 的 20 份声明）

### 还没做的任务

- 2.3：抽查 3 个源真实跑一次，确认巡检不出"行数不足"误报
- A 桶剩余 19 个源：需按 baseline §5 的 SQL 对 `crawl_runs` 复算后再声明
- 3.x（B 桶）、4.x（只补 `data_period` 那桶）、5.1（D 桶待定清单）、5.3（冒烟类源的语义）
- 6.1–6.3：反向验证（人为把产出压到 0，巡检必须出单）、全量巡检复核、`openspec validate --strict`
