# 全集群 `limit` 截断扫描 · 2026-11-11

> 起因：`usaspending-awards` 首爬（`docs/usaspending-awards-window-decision.md` §6）真跑抓出
> 「`--limit 100` 把 13 个月窗口截成 100 行」。既然如此，就该问一句：
> **其他单元是不是也在被同一个默认值切？** 本文是那次全量扫描的记录。

## 一、结论

| 类 | 判据 | 实例 | 处置 |
|---|---|---|---|
| **A 固定全集被切＝真缺陷** | 数据集是**固定历史集**（没有"更新的行"），spider 全量取回后切片 | `usgs-nwis` 366→100、`nasa-power` 732→100、`afdc` 225→100 | **已修**：显式声明 `limit` 覆盖全集 |
| **B 滚动尾部＝设计如此** | 时间序列，每轮取最新 N 条，历史靠逐轮累积 | `eia-v2`、`nmc-weather`、`entsoe-e`、`socrata-ny/chicago` | **不改**（改了反而多付上游代价） |
| **C 语义本非行数** | 一日报/快照，100 条就是全部 | `ted-notices`（单日公告）、`zgdypf-boxoffice`（日榜） | **不改** |

**A 类的共同点是"零额外代价可修"**：行已经被取回来了，切片把它们丢掉——修法只是把上限抬到全集之上。

## 二、方法（三段，每段都可复算）

### 2.1 manifest 面：谁在吃默认值

`runner_cli.py:577` 里 `--limit` 的默认值是 **100**，manifest 顶层不写 `limit:` 就用它。
按 `spiders/*/manifest.yaml` 统计：

| 项 | 数 |
|---|---|
| 单元总数 | 165 |
| 已排期（有 `schedule:`） | 126 |
| **已排期且未声明 `limit`（吃默认 100）** | **119（94%）** |

也就是说"默认 100"不是个别情况，而是**绝大多数单元的隐式契约**。

### 2.2 DB 面：谁真的钉在上限上

`rows_written` **恰好等于上限**才是截断指纹——真实数据量很少长期精确落在某个整数上。
用众数值占比（下称 peg ratio）排序，`crawl_runs` 全量（5342 次运行 / 149 个源）：

```sql
-- 每个源的行数众数及其占比（只统计成功运行）
select source, rows_written, count(*) from crawl_runs
where status='success' and rows_written is not null
group by source, rows_written;
```

钉在 100 上、且运行数足够多（≥25 次）的：`nmc-weather`（884/886）、`eia-v2`（770/770）、
`carbon-monitor`（40/42）、`zgdypf-boxoffice`（39/41）、`open-meteo`（36/38）、
`worldbank-procnotices`（36/37）、`usgs-nwis`（36/37）、`nasa-power`（36/37）、
`portwatch-imf`、`nasa-firms`（31/31）、`entsoe-e`（27/27）、`ted-notices`（26 次）。

> ⚠️ **peg ratio 只能证明"被上限钉住"，不能证明"丢数据"**。
> `docs/min-rows-coverage-baseline.md` §3 早已把这类源标成"上限截断型"，但那是为了取
> `min_rows_per_run` 的阈值（上限的 20%），**没有回答"被切掉的那部分是不是永久丢失"**。
> 这两件事必须分开：本文补的就是后半句。

### 2.3 真实环境量全集：`limit=5000` 逐个跑

在 `tencent` 集群用 runner 镜像 + `/content/spiders` 实跑（2026-11-11，一次性 Job）：

| 单元 | 实测全集 | 生产实际落库 | 丢失 | 判定 |
|---|---|---|---|---|
| `jodi-gas` | **16,556** | 100 | 99.4% | A，已修 |
| `nasa-power` | **732** | 100 | 86.3% | A，已修 |
| `boxofficemojo` | **600** | 100 | 83.3% | A，已修 |
| `imf-pcps` | **1,254** | 100 | 92.0% | A，已修（见 §4.3 的三次测量） |
| `usgs-nwis` | **366** | 100 | 72.7% | A，已修 |
| `afdc` | **225** | 100 | 55.6% | A，已修 |
| `cisa` | **200** | 100 | 50.0% | A，已修 |
| `mohurd-yearbook` | **>5,000**（200000 时上游读超时） | 100 | ≥98% | A 类但**未修**：规模与耗时要一起定 |
| `gb-standards` | 未测（240s 未跑完，分页翻库） | 100 | 未知 | 未修 |
| `carbon-monitor` | 未测（读 65 万行索引，未跑完） | 100 | 未知 | 未修 |
| `eurostat-api` | 2,009 | 100 | — | **不走这条**：provider 按需取数，不并入 `--limit` 轮转 |
| `socrata-ny` | **403 Forbidden**（`$limit=5000` 被上游拒） | 100 | — | 不可简单抬高 |
| `amis-market` | 网络不可达（BigQuery 需代理） | 100 | 未测 | 未修 |
| `entsoe-e` | 缺 token（人工审批源） | 100 | 未测 | 未修 |
| `usda-quickstats` | 缺 `QUICKSTATS_API_KEY` | 100 | 未测 | 未修 |
| `jodi-oil` | 上游 `2025.csv` 取数失败 | 2 | — | 另一类缺陷（上游变了），另行处理 |

## 三、为什么 A 类是"永久丢失"而 B 类不是

`revision_policy: overwrite_with_history` 让每次运行都追加一份快照，消费侧取最新 run。
于是**"重取"能纠正历史**——但前提是该行曾经进过某个窗口。

- **B（滚动尾部）**：`eia-v2` 每轮取"最新 100 个观测"。今天最新的 100 条，明天有一部分落到
  第 101~200 位，被明天那一轮取到。历史是**逐轮累积**出来的，只要每轮增量 ≤ 100 就不丢。
- **A（固定全集）**：`nasa-power` 取的是 1984 年整年。**没有"明天更新"的行**。每轮重新取回
  同样的 732 行，再切掉同样的 632 行——下一轮切掉的是**同一批**。所以那些行永远不会进库。

> 判别口诀：**问"下一轮会不会取到这次被切掉的行"**。会 → B，不会 → A。

## 四、本次改动（固定全集单元）

### 4.1 第一批（`41af218`，三个源）

| 文件 | 改动 | 依据 |
|---|---|---|
| `spiders/usgs-nwis/manifest.yaml` | `+limit: 500` | 全集 366，1.37 倍余量 |
| `spiders/nasa-power/manifest.yaml` | `+limit: 800` | 全集 732，1.09 倍余量 |
| `spiders/afdc/manifest.yaml` | `+limit: 500` | 全集 225，2.2 倍余量 |

### 4.2 第二批（`3a7c63c`，四个源）

| 文件 | 改动 | 全集 | 修前落库 | 丢失 | 切片方向 → 丢的是 |
|---|---|---|---|---|---|
| `spiders/jodi-gas/manifest.yaml` | `+limit: 20000` | 16,556 | 100 | **99.4%** | `[-limit:]` → 最早的历史期次 |
| `spiders/boxofficemojo/manifest.yaml` | `+limit: 700` | 600 | 100 | 83.3% | `[:limit]` → 第 2、3 个年份整页 |
| `spiders/imf-pcps/manifest.yaml` | `+limit: 1600` | 1,254 | 100 | 92.0% | `[-limit:]` → 最早期次 |
| `spiders/cisa/manifest.yaml` | `+limit: 300` | 200 | 100 | 50.0% | 先 legacy 吃满额度，门户拿不到行 |

### 4.3 为什么"量全集"必须量生产那份代码，而不是镜像里的副本

`imf-pcps` 我量了三次、错了两次，两次错误的成因不同，都值得记：

| 次 | 做法 | 得到 | 真相 |
|---|---|---|---|
| 1 | 集群 Job 里 `import /content/spiders/imf-pcps/spider.py` | 418 行 | ❌ 镜像里烘焙的 `/content/spiders` **比 HEAD 旧**，那时 `SEED_SERIES` 只有 `PCOIL` 一条 key；而**生产运行是 run 时 checkout 最新代码** |
| 2 | 依此设 `limit: 600` | 真跑恰好 **600** | ❌ 等于上限 → 仍在截断 |
| 3 | 抬到 1200 | 真跑恰好 **1200** | ❌ 仍等于上限 |
| 4 | 改用 `fetch_series` 逐 key 量 + 读 HEAD 源码 | 3 条 key × 418 期 = **1,254** | ✅ 取 1600 |

两条教训：

1. **测量环境必须等于生产环境。** 镜像里带的 `/content/spiders` 是构建时快照，
   runner 运行时会重新 checkout——拿镜像副本量，量到的是"构建那天"的代码。
2. **返回值等于上限时，它什么也没告诉你。** `limit=600 → 600 行` 与 `limit=1200 → 1200 行`
   既可能是"全集就这么大"，也可能是"被切了"，从返回值上完全分不开。
   判据只能是：**把上限抬到返回值小于上限为止**。

三处都在注释里写了实测全集、丢失比例、以及"这不是滚动尾部"的理由。
第二批四处同理：注释里写明切片方向（`[:limit]` 还是 `[-limit:]`），
因为**切片方向决定丢的是头还是尾**——这是同一行 `limit` 下完全不同的两种损失。

### 4.4 真跑验证（同日、同单元、修前修后对照）

仓库改完 + Argo 渲染生效后，各触发一次真实运行，再用 DB 独立核对
（不依赖 runner 自己的日志行）：

| 单元 | 修前 | 修后 | `rows_written` | `crawl_items` | 一致 |
|---|---|---|---|---|---|
| `usgs-nwis` | #5346 → 100 | #5384 | **366** | **366**（366 个 distinct date） | ✅ |
| `nasa-power` | #5331 → 100 | #5385 | **732** | **732** | ✅ |
| `afdc` | #5188 → 100 | #5386 | **225** | **225** | ✅ |
| `jodi-gas` | #5036 → 100 | #5393 | **16,556** | **16,556** | ✅ |
| `boxofficemojo` | #4499 → 20 | #5390 | **600** | **600** | ✅ |
| `cisa` | #4504 → 20 | #5391 | **200** | **200** | ✅ |
| `imf-pcps` | #4786 → 417 | #5399 | **1,254** | **1,254**（418 个 distinct period × 3 key） | ✅ |

`error_head` 全为 `NULL`。这一列是必要的：只看 `rows_written` 会被第二层
（writer cap）骗过，必须 `run_id` 关联 `crawl_items` 才是"真落库"。
七个值里六个 < 1000，第二层不参与；`jodi-gas` 16,556 ≥ 1000，靠 chart 把
`FD_WRITER_MAX_ITEMS` 同步为 20000（实测 CronJob env `WRITER=20000`），故也相等。

Argo 核对（判据看**集群里渲染出的 args**，不看仓库里的 YAML）：两批 App 均
`Synced` 到 `41af218` / `3a7c63c`，实测 args = `500 / 800 / 500` 与
`20000 / 700 / 300 / 600(→1200)`。

同时修正了 `usgs-nwis` / `nasa-power` 的 `min_rows_per_run` 注释：原写
「`p50=limit 截断型`，取上限 20%」——`limit` 一旦显式声明为 500/800 并覆盖全集，
"上限的 20%"这个口径就不成立了。**阈值本身没动**（20 仍是一个粗下限），只把注释改诚实。
`imf-pcps` / `jodi-gas` 同理（原注释写 `p50=100`，那是默认上限造成的假象）。

落库第二道闸复核：writer 默认 `FD_WRITER_MAX_ITEMS=1000`，366 / 732 / 225 / 600 /
200 / 600 均低于此；`jodi-gas` 的 16,556 靠 chart 同步（见下）。

> **别把两种截断混成一种**。同一天另一笔提交 `f934d6a` 修的是**第二层**：
> `--limit` 只控制"单元返回多少行"，**不控制"能落多少行"**——writer 默认只落 1000 行。
> 实测 `mct-star-hotel` 声明 `limit: 20000`、runner 报 `rows_written=8482`，但
> `crawl_items` 只有 1000 行。该提交改成：chart 在 `limit ≥ 1000` 时把
> `FD_WRITER_MAX_ITEMS` 抬到同一个值。
>
> 两层的判据不同，不要互相顶替：
>
> | 层 | 症状 | 判据 | 修法 |
> |---|---|---|---|
> | 第一层 `--limit` | 单元返回行数被切 | `crawl_runs.rows_written == limit` | manifest 声明 `limit:` |
> | 第二层 writer cap | 返回了但没落 | `rows_written > crawl_items 实际行数` | chart 同步 `FD_WRITER_MAX_ITEMS`（已修） |
>
> 本文的 366/732/225 都在 1000 以下，所以只涉及第一层；扫 `rows_written` 时也**不能只看它**
> ——它记的是 runner 报的数，不等于真落库的行数。

#### 4.1 第二层的生产残留实测（`rows_written` vs `crawl_items`）

按 `run_id` 关联最近 400 次成功运行，找 `rows_written > crawl_items 实际行数` 的（2026-11-11）：

| 运行 | 源 | `rows_written` | 真落库 | 差 | 判定 |
|---|---|---|---|---|---|
| 4882 | `mct-star-hotel` | 8482 | **1000** | 7482 | ✅ 真截断，即 `f934d6a` 修的 writer cap；它声明 `limit: 20000`，chart 修后会把 cap 同步上去 |
| 5371 | `clinicaltrials-cn` | 1381 | **1000** | 381 | ✅ 真截断，但成因是旧 `limit: 100` 语义=**月数**（100 个月 → 1381 行）；`f934d6a` 已改成 `limit: 1`（1 个月 ≈ 400–500 行），前向不再触发 |
| 5234 等 | `akshare` | 4,149,850 | **0** | 全部 | ❌ **不是截断**：`akshare` 是跨仓遥测作业，只 `report_run` 不写 items（见 `min-rows-coverage-baseline.md` §6） |
| 5108 / 5113 / 5124 | `wbgapi` / `ccdi-supervisory-*` | 20 / 8 | 0 | 小额 | ❌ 增量型 0 行语义，同属遥测侧 |

结论：**第二层残留只有修复前的两笔**，没有新增面。而 `akshare` 说明这个判据也有假阳性——
`items=0` 可能是"这条链路本就不写 items"，不是"落了 0 行"。**先问该源的写入路径，再判截断。**

## 五、刻意**不**改的部分

| 源 | 为什么不动 |
|---|---|
| `eia-v2`（770/770 钉在 100） | B 类：时间序列取最新尾部；抬高会成倍增加上游请求与落库量，收益不明 |
| `socrata-ny` / `socrata-chicago` | 上游对 `$limit` 有上限（实测 5000 → 403），盲抬会直接让单元**变红**——这是"修一个洞挖另一个洞" |
| `gb-standards` | `limit=5000` 150s 内没跑完，全集规模未知；先量再改，不估着填 |
| `ted-notices` | C 类：单日公告，100 条足够 |
| `carbon-monitor` | 全集未量（65 万行索引，慢）；且它取的是"最新 N 行"滚动语义，与 B 同类 |
| `amis-market` / `entsoe-e` | 环境原因测不了（BigQuery 需代理 / 需人工审批 token），不凭猜测改 |

## 六、为什么不加门禁

想加一条 `"已排期且未声明 limit"` 的规则，**否决**：

1. 119/126 会命中——一个 94% 都红的门只会训练人忽略它（与
   `docs/silence-discipline-drift-2026-11-11.md` §4 否决 `generation-silence-violation` 同因）；
2. **"未声明 limit"不等于"丢数据"**：判丢需要知道"全集多大"，而全集规模只有真跑才知道
   （`socrata-ny` 的 403 就是反例——上限不是想抬就能抬）。

所以这条只能靠**逐源的实测**，不能靠静态门。可执行的替代是本文 2.3 那一段：需要时用
`limit=5000` 实跑量全集，再决定要不要声明。

## 七、复现命令

```bash
# 1) manifest 面
python3 - <<'PY'
import pathlib, yaml
rows=[]
for p in sorted(pathlib.Path('spiders').glob('*/manifest.yaml')):
    m=yaml.safe_load(p.read_text()) or {}
    rows.append((p.parent.name, bool(m.get('schedule')), m.get('limit')))
print('units', len(rows), 'scheduled', sum(r[1] for r in rows),
      'scheduled_no_limit', sum(1 for r in rows if r[1] and r[2] is None))
PY

# 2) DB 面：见 §2.2 的 SQL（只读）
# 3) 量全集：集群内一次性 Job，对目标源调 fn(limit=5000) 打印 len(rows)
```

## 八、教训

- **「被上限钉住」与「数据丢了」是两个判据。** 前者一行 SQL 就能看出（`rows_written == limit`），
  后者必须回答"全集多大"和"下一轮会不会取回来"。只验前者会把
  `eia-v2`（正常）与 `nasa-power`（真丢）判成同一件事。
- **默认值也是契约。** `--limit` 默认 100 写在 runner 里，119 个单元没声明就继承了它。
  默认值不会出现在任何一个 manifest 里，所以 review 时看不见——这类缺陷只有"真跑 + 量全集"能抓。
- **别盲抬上限。** `socrata-ny` 把 `$limit` 抬到 5000 直接 403：上限是上游的约束，
  抬高之前先测"上游接受多大"。
