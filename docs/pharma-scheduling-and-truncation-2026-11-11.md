# 医药两源：生产调度查证与真跑对照（2026-11-11）

本文件记录两件事：**生产调度其实早就自动生成了**（不是"待点亮"），以及由此暴露的
两层截断——以及每一条的实测对照。

## 1. 调度不需要"点亮"：AppSet 已经自动渲染了

`fd-infra-deploy/argocd/industry-appset/applicationset.yaml` 用 **git 生成器扫
`spiders/*/manifest.yaml`**，每个源渲染一个 CronJob。所以两个医药单元合入 main 后，
生产里立刻就有了：

| CronJob | schedule | suspend |
|---|---|---|
| `fd-industry-clinicaltrials-cn` | `47 2 6 * *`（Asia/Shanghai） | false |
| `fd-industry-nrdl-national-catalog` | `19 3 8 1 *` | false |

**判断口径**：想知道一个源有没有排期，看 `kubectl -n fd-industry get cronjob`，不要问
manifest 的 `enabled`——`enabled` 只影响 chart 渲染 `suspend`，不影响是否被生成。

## 2. 第一层截断：`--limit` 缺省 100

chart 渲染 `--limit {{ .Values.limit | default 100 }}`，而两个单元都没声明 `limit`：

- `nrdl-national-catalog` 的 `limit` 语义是**行数**→ 只落 100 行（全量 3911）；
- `clinicaltrials-cn` 的 `limit` 语义是**月数**→ 会去取 100 个月。

已补：`clinicaltrials-cn: 1`（月增量，约 400–500 行）、`nrdl-national-catalog: 5000`。
Argo 同步后实测参数：`["nrdl-national-catalog","--limit","5000",...]`。

## 3. 第二层截断：writer 落库上限 1000

`--limit` 只控制"单元返回多少行"，**不控制"能落多少行"**。`fd_industry_data/writer.py`
的 `_max_items()` 默认 1000，只有**回填窗口运行**会被 runner 抬到 100000；而
`--window-auto` 只在"该源从未成功过"时给窗口，成功一次后即回落。

### 对照实验（同一单元、同一镜像 sha-41af218、同一数据）

| run id | 传 `FD_WRITER_MAX_ITEMS` | `rows_written` | **真落库 crawl_items** |
|---|---|---|---|
| 5387 | 否 | 3911 | **1000** |
| 5389 | 是（5000） | 3911 | **3911** |

**这条对照说明 `rows_written` 不能当落库判据**——两行都报 3911，真落库差 3.9 倍。
判据必须是 `select count(*) from crawl_items where run_id = ...`。

### 既有受害源（不是本批引入）

`mct-star-hotel` 声明 `limit: 20000`、runner 报 `rows_written=8482`，`crawl_items` 只有
**1000**。即"声明了 limit 也照样被 writer 截断"，与 `docs/limit-truncation-sweep-2026-11-11.md`
里记的第一层截断是两个不同的闸。

### 修法

chart（`k8s/cronjob-template/templates/cronjob.yaml`）在 `limit ≥ 1000`（快照类源的特征）
时，把 `FD_WRITER_MAX_ITEMS` 设为同一个值；`limit` 缺省或较小的增量源行为**完全不变**。
`helm template` 四态实测：未声明 / 100 → 不设；1200 / 20000 → 设。
全仓仅 5 个单元声明 `limit ≥ 1000`（dounan-flower-price / mct-tourism-demand /
mct-star-hotel / nrdl-national-catalog / usaspending-awards）。

## 4. 顺带修好的一件：镜像构建长期红

`main` 上连续三次 `image` 构建都失败在同一处：

```
/usr/local/lib/python3.12/site-packages/playwright/driver/node:
  error while loading shared libraries: libatomic.so.1
```

`playwright install --with-deps` 救不了——那是要先让 node 能跑起来才轮得到装依赖。
Dockerfile 的 apt 行补 `libatomic1` 后构建转绿（run 42976964518 success）。

**踩到的坑**：改完只推 gitee（Argo 盯 gitee）**不会**触发 CI——GitHub Actions 看的是
github 远端。这次实测：第一次只推 gitee，CI 毫无反应；补推 github 后立刻开始构建。

**另一个假判据**：`docker manifest inspect ccr.../fd-industry-runner:sha-xxx` 在本机/relay
机上对**任何** tag 都返回失败（含已知存在的 `e6f5f01`）——认证问题造成的假阴性。判镜像
可用的可靠办法是拉起一个 pod：能 `Completed` 并打出预期输出即真可用。

## 5. 镜像滚动（已做，2026-11-11 16:0x）

两个 CronJob 原先用 `sha-e6f5f01`（**不含** `pdftext.extract_words`），
下一次触发会直接 `AttributeError`：

| CronJob | 下次触发 | 原 tag |
|---|---|---|
| `fd-industry-nrdl-national-catalog` | 12-08 03:19 | sha-e6f5f01 |
| `fd-industry-clinicaltrials-cn` | 12-06 02:47 | sha-e6f5f01 |

### 为什么不走 `kubectl patch`

对 CronJob 手工 patch 镜像会被 **AppSet 重渲染覆盖**（实测报成功、读回未变，
对象上带 `argocd.argoproj.io/tracking-id`）。唯一能留住的是 **ApplicationSet
`helm.parameters` 里的 `image`**。

### 做法

1. 选 tag：`fd-industry-data` main tip 是 `3a7c63c`，镜像 `sha-3a7c63c` 已出片。
   核对方向：线上其余活影像 `ea21592/92ae150/01c5ffb`（10-13/10-01/11-08）都比它旧，
   `e6f5f01`(11-10) 也旧 —— **这是前进不是回退**（AGENTS.md 里"别把别人的新片回退"的正向对照）。
2. 验证：**不用 `docker manifest inspect`**（对任何 tag 都假阴性，含已知存在的 `e6f5f01`），
   直接拉 pod 实跑 → `extract_words: True`、签名一致、`writer cap default: 1000`。
   注意 relay 有 ≤5 分钟时延：`sha-3a7c63c` 刚出片时 ccr 拉不到（ImagePullBackOff），
   过一个 relay 周期才有。
3. 改 `fd-infra-deploy/argocd/industry-appset/applicationset.yaml` 的 `image` 值
   （同时把 `repoURL` 由 `https://` 对齐成集群实况的 `git@`），**只把 `image` 一个值
   打到线上 spec**（读回核对），不做整体 `replace`。
4. 收敛实测：126 个 CronJob 里 **123 个到 `sha-3a7c63c`**；`bls`/`cncotton`
   仍是 `01c5ffb` 且 `suspend: true`——它们 manifest 本就 `parked` 且无 `schedule`，
   渲染为空、Argo 拒绝清空，属既存 OutOfSync（已挂起，无风险）。

### 三参数已整体归位（16:2x，与镜像同一窗口）

上面那条"渲染为空 → Argo 拒绝清空"才是 `bls`/`cncotton` 的根因，
所以把仓内多出的 `scheduleTz`/`parked`/`enabled` 一起归位了（`reconcile-industry-appset.sh tencent apply`）：

| 项 | 归位前 | 归位后 |
|---|---|---|
| CronJob 总数 | 126 | **151** |
| 挂起（suspend） | 2 | **27** |
| 在跑（未挂起） | 124 | **124**（一个没变） |
| Argo OutOfSync | 2 | **0** |

- 新增的 25 个全是**此前根本没有 CronJob** 的 parked 源（渲染为空、Argo 不肯清空，
  所以从没被创建过），现在补成 `suspend: true` + 占位 schedule `0 0 31 2 *`；
- 既有源里只有 `bls`/`cncotton` 两笔变了：旧 schedule（`23 3 * * *` / `25 6 5 * *`）
  归零、镜像随全局到 `sha-3a7c63c`，挂起状态不变；
- **没有停掉任何正在跑的源**（运行中 124 → 124），这是归位前后逐条比对得到的，
  不是"应该不会"。

> 订正：动手前我判断"31 个 parked 源正在触发、归位会被停掉"，实测是错的——
> 其中 25 个连 CronJob 都没有，另 2 个早已手工挂起。判据是
> `fd-industry/source` 标签与集群实况逐条比对，不是数 manifest。

### 评测层：跑通了，且已装进真正的周巡检机

医药两份基线（`fd-data-quality/baselines/pharma-*.yaml`）对着**已落库的生产数据**验收：

| 面 | 结果 |
|---|---|
| golden 回放 | nrdl 2 个样本 ok（3911 行）、ct 1 个样本 ok（432 行），diffs=0 |
| `coverage_scan` | 两源 `gaps=0 critical=0 status=success` |
| `pharma_boundary_check` | PASS —— 2 份基线 / 2 个单元 manifest / 5 个族，无价格越界 |
| `pharma_gap_attribution` | 无可归因缺口行（gaps=0 的自然结果） |
| `pharma_readiness` | live=2（golden 一致 ✓） degraded=3（各带三视角状态码） |

周巡检的真实位置是 **100.64.0.3（guangzhou-xinru，master）**，不是我能先摸到的
100.64.0.6——`.6` 是 NBS worker 机（跑 `fd-nbs-worker-4`），按安装脚本 design
worker 机 `FD_NBS_CRON=0`，本来就不写巡检 cron。已在 master 上完成：

1. `/opt/fd-data-quality` 不是 git 检出、基线停在 5 份（Sep 21）。用
   `git archive master` 打包**已提交的树**覆盖（别人的未跟踪 `securities-*.yaml`
   不碰），覆盖前留了备份 `/opt/fd-data-quality.backup-20261111-165536.tgz`；
   `.env` 与 `.state` 原样保留（600）。
2. 该机 `.env` 缺 `FD_OPEN_DATA_MCP_DATABASE_URL`（医药基线要用它）。从
   `-n mcp secret fd-mcp-env` 取真值经文件追加（值不回显，append 前后校验长度 78）。
3. 重建 `fd-data-quality:local`（147MB），容器内实测：两条医药源 `gaps=0`，
   再跑 `--source all` **7 份基线全 success**，原有 5 个域数值与本地一致。
4. cron 未改（仍 5 条，`coverage_scan` 那条周一 06:40），下一次起自动包含医药域。

两条修正：

1. 基线原来写 `db_env: FD_CRAWL_DB_URL`（行业 runner 的变量名），而质量仓的生产
   `.env` 没有它。**`dsn()` 抛的是 `SystemExit`（BaseException），`scan_source`
   的 `except Exception` 拦不住**——真跑起来整轮 `--source all` 会在 pharma 处退出，
   排在后面的源一天都不再被扫。已改用仓库既有词汇 `FD_OPEN_DATA_MCP_DATABASE_URL`
   （与 `-n mcp secret fd-mcp-env` 同一个键，同一台库；角色 `fd` 对
   `crawl_runs`/`crawl_items` 有 SELECT，实测 4320 行可读）。
2. 排障顺序的教训：先 `for h in <tailnet 各机>` 找 `/etc/cron.d/fd-data-quality`，
   再谈"没在跑"。我第一次只摸了 `.6`/`.7`/`.11` 就下结论"周巡检未安装"，实际
   master 在 `.3`、cron 与日志都好好的（11-09 周一扫了 6 份基线）。

另：`fd-industry-dispatcher`（`*/5 * * * *`）原先仍用 `sha-750da3f`——本批已一并滚到
`sha-3a7c63c`，见下节。

## 6. dispatcher / observer 一起滚（同日 18:2x）

`750da3f` 不只留在 per-source CronJob 的旧值里，还钉在三个手工面。盘点后发现四处、
跨三个集群，且其中两处的 tag 连 `750da3f` 都不是：

| 面（release.yaml component） | 位置 | 原 tag | 备注 |
|---|---|---|---|
| runner | tencent `fd-industry/fd-industry-dispatcher` | `750da3f` | 每 5 分钟一跳 |
| observer | xinru `scraw/federation-observer` | `750da3f` | xinru-master |
| runner-xinru | xinru-server1 `scraw/fd-industry-dispatcher` | **`d84cc82`** | 10-14，比 750da3f 还旧 |
| observer | xinru-server1 `scraw/federation-observer` | **`ea21592`** | 10-13，更旧 |

**方向核对**（AGENTS.md 那条"别把别人的新片回退"的正向用法）：`d84cc82`/`ea21592`
都查了 GHA 构建时间，是 10-13/10-14，比 `750da3f`(11-06) 与 `sha-3a7c63c`(11-11) 都旧，
所以滚上去是前进。

做法与判据：

1. 只改 `k8s/release.yaml` 的四个 `tag:`，然后 `scripts/render_release.py` 渲染三个
   消费面。**直接渲染会被拒**：渲染器看到文件里是旧的**具体 tag** 就判 `DRIFT`
   （设计的双态语义，防止拿陈旧 manifest 覆盖），要先把具体 tag 还原成
   `__RELEASE_IMAGE:<component>__` 占位符再渲染。
2. apply 前逐台 `kubectl diff`：tencent 只有 image 变化；xinru 集群**本来没有
   dispatcher**（diff 全是 `+`），所以那里只 apply observer，不新建对象；
   xinru-server1 两个都在，只有 image 变化。
3. 读回四处 + initContainer 均 `sha-3a7c63c`；tencent dispatcher 从 CronJob 派生
   实跑 `Succeeded`（`inventory synced, 165 source(s)` / `0 run(s) handled`）。
4. `release.yaml` 里过期的"11-06 统一到 750da3f"注释一并更新，避免下一任读错。

**踩到的一个跨仓闸**：`fd-industry-data/tests/test_render_release.py` 有一条不变量
——`release.yaml` 的 `runner-tencent-cron.tag` 必须等于 AppSet 的 image 值，判据是
正则 `name:\s*image\s*\n\s*value:`。我把说明注释插在 `- name: image` 与 `value:`
之间，正则就匹配不到，测试红在"AppSet 里找不到 image 参数"。注释必须放在参数
**上方**。这条闸是"登记面 == 实际部署值"的唯一自动化判据，值得留着。

## 7. 中药饮片段接入（同日，change `add-nrdl-herbal-slice`）

目录族最后一段补上：**892 条饮片 + 32 行黑名单**，总行数由 3911 升到 **4835**。

这段与前三部分都不同构，实测后按三条不同规则处理：

| 特征 | 处理 |
|---|---|
| 左右双栏并列（每页两组 序号/名称/备注） | 按 x 切半：左 `x<300`、右 `x≥300`；名称/备注带各自实测，**未归类 token = 0** |
| 备注以 `□` 标记为主 | `□` → `payable_standalone=False`（凡例：单独使用不予支付，且全部由这些组成的处方也不予支付） |
| 2 条备注折行（红参 / 红参片） | 按**最近条目行**绑定 + 8pt 护栏（实测距本行 6.1/6.3pt、距相邻行 19pt） |
| `(二) 不得纳入基金支付范围` | 自由文本解析：行拼接修断行、括号感知切分保别名、末尾类目行单列 |

**两个必须记住的坑**

1. `□` 与条目**同行**。按「序号开头 → 其余全是名称」会把标记并进药名——实测 114 条标记
   全被吞，而名称唯一性检查照样通过（因为吞进去后仍唯一）。必须按 x 带切开。
2. 折行备注**少拼一行时不报错**：剩下的「重患者抢救」仍是非空文本，条目数与序号都没变。
   因此加了内容判据「饮片附加条件必须以『限』起」——注入删除一行后立刻红
   （`饮片备注不像限定条件（item_no=284）：重患者抢救`）。

**硬判据**：饮片唯一序号必须 = 892 且恰好覆盖 `1..892`（缺号/重号分列），名称不得重复；
黑名单药名数 = 31，且 `穿山甲→[醋山甲,炮山甲]`、`鹿茸→[鹿茸粉,鹿茸片]` 两条别名逐字相符。
注入删除第 196 页 → 报 `唯一 800 ≠ 892 缺号=[359,360,…]`。

**事实地址**：饮片按 `(section, item_no)`（名称换版会微调，序号才是版本内稳定对位）；
黑名单按 `(section, excluded, drug_name)`；类目按 `category`。

**上线实测**：run 5427 `rows_written=4835` = `crawl_items=4835`；section 分组
西药 1996 / 中成药 1443 / 谈判 472 / 中药饮片 924（= 892 + 31 + 1 类目）。

**顺带修掉一个假绿**：目录基线的 `row_count` 原本统计「所有成功 run 的并集」，
实测 6 个 run 累加 16272 行——一次被 writer cap 截到 1000 行的 run（5387）被历史
run 完全盖住，阈值抬多高都照样绿。改成只取最近一次成功 run 后，它立刻显形
（5398 → 3911 < 4800，报缺 889），代码上线后（5427 → 4835）缺口消失。

## 8. 观测层落账（change `add-pharma-observation-extraction`）

补上链路的最后一环：**概念观测**。此前医药域在观测层是零行——

```
select count(*) from semantic_observations
 where source_used in ('clinicaltrials-cn','nrdl-national-catalog');   -- 0
```

而同时 `crawl_items` 有 48xx 行、概念播了 4 条、`pharma_facts.py` 只有测试在 import。
结果是**产品面上这个域不存在**：MCP 按概念检索，没有观测就没有医药。

新增 `scripts/extract_pharma.py`，四个概念：

| 概念 | 粒度 | 实体 | 实测值 |
|---|---|---|---|
| `trial.new` | month | country/CN | 464 + 508 + 409 = 1381（= 唯一试验数） |
| `trial.enrollment.planned` | month | trial/实体 id | 1381 条（逐试验） |
| `catalog.drug.entries` | year | country/CN | 4803（可支付行，黑名单 32 不计） |
| `catalog.drug.class_a_share` | year | country/CN | 15.97%（甲类 767/4803） |

**三个必须记住的实现点**

1. `semantic_observations.entity_id` 是**整型外键指向 `entities.id`**，不是外部编号。
   直接写 `NCT...` 会 `invalid input syntax for type integer`（本批实测踩了一次）。
   逐实体观测要先幂等登记实体（`ON CONFLICT ON CONSTRAINT uq_entity_type_code`）再替换。
2. 试验源是**增量月窗**：只读最近一次 run，序列每期只剩一个月（那次 `--limit 1` 就只
   覆盖 1 个月）。必须跨全部成功 run 合并、按注册号取 `scraped_at` 最新。目录相反，
   它是年度全量快照，只读最近一次。
3. 写完必须**对账**：四个概念都要能被 `crawl_items` 复算。口径漂移不会让写入失败，
   只会让数字静默偏大（例如把黑名单 32 行也算进条目数）。

**聚合度量的实体粒度**：计数与占比是「范围内该期总量」，实体是范围（`country/CN`），
不是样本实体。挂到单个药品上会让同一概念在 4803 个实体上各有一条同名值。
`concepts.yaml` 里三条种子的 `entity_type` 已随之修正（`drug`/`trial` → `country`）；
逐实体的 `planned_enrollment` 保持 `trial`。

**播种脚本加了守卫**：五元组含 `entity_type`，改声明粒度会**静默新增同 code 的第二条
变量**。现在「同 code 不同五元组」直接失败并给出迁移提示，只有
`--migrate-entity-type` 且该概念零观测时才就地迁移（实测第二次运行 +0/+0）。

**巡检面**：新增基线 `pharma-observations.yaml`，用 `missing_entity` 以概念 code 为
"实体"检查四概念各至少一条观测。反向验证（把 where 收窄到只含试验源）报 2 条 critical
并点名两个目录概念。部署时又踩了一次「合并不等于生效」：**baselines 是烤进镜像的**，
只更新宿主树不重建镜像不生效——重建后八份基线全 success。

## 9. 抽取排期 + 存活判据（change `schedule-pharma-observation-extraction`）

前一段把观测层接通了，但抽取仍是**手工**跑的：爬虫按排期继续涨 `crawl_items`，而
`semantic_observations` 停在敲下 `--apply` 的那一刻；且写入是 `ON CONFLICT DO NOTHING`，
重跑插 0 行、`fetched_at` 不变，所以"抽取器挂没挂"在数据里看不出来。

**排期**：`k8s/extract-pharma.yaml` 的 CronJob `fd-pharma-observations`（`mcp` 命名空间，
每日 05:30 Asia/Shanghai，`Forbid` 并发，`activeDeadlineSeconds=900`）。派生实跑：pod
`Succeeded`，`1386 -> 1386 (inserted 0)`，台账 `executions` 多一行 `success`（2m22s，含
四概念对账）。

**脚本为什么走 ConfigMap 而不是 git checkout**：仓库私有（pod 里 clone 报
`could not read Username`——我本机第一次 `ls-remote` 成功是 macOS 钥匙串在替我认证），
而 `fd-industry-gitee-token` 在 `fd-industry` 命名空间，**K8s 不允许跨命名空间
secretKeyRef**。复制 token = 凭据多一个轮换漏点；塞进 runner 镜像 = 改一行脚本滚 126 个
CronJob 的镜像。改成 `scripts/render_extract_configmap.py` 渲染的 ConfigMap：pod 不碰 git、
不用凭据、不滚镜像。漂移由 `--check` + `tests/test_extract_configmap_gate.py`（含注入
漂移的反向用例）兜住。

**存活判据必须用 `freshness`，不能用 `staleness`**——这是本次最有价值的一条。初稿写
`staleness + allow_lag=36`；实测 `gaps_staleness` 的参照物是**同一结果集内各组的最大值**，
整表停摆时参照物和水位一起冻结，lag 恒 0。反向对照：同一份 `executions` 数据，`staleness`
即使把 `allow_lag` 压到 0.0001 也照样绿——死门。改用 `freshness`（`max(started_at)` 对
wall clock，`max_age_days=1.5`）后两条反向都成立：压窗到 0.0001 天报红、`where` 命中零行
报红。巡检机实跑 `gaps=0 status=success`，机上反向也复现了红。

**打包坑**：Mac 上 `tar -czf` 会把 AppleDouble 兄弟文件 `._*.yaml` 一起打进构建上下文，
镜像里 `baselines/._*.yaml` 让 `coverage_scan` 读基线时直接 `UnicodeDecodeError`（
`0xa3` 在 offset 45）。用 `COPYFILE_DISABLE=1 tar` 或 `git archive` 直接出包。
