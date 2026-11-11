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

另：`fd-industry-dispatcher`（`*/5 * * * *`）仍用 `sha-750da3f`，但它由
`k8s/dispatcher.yaml` 单独管理（标签 `fd-industry/component: dispatcher`，
不在本 AppSet 内），属另一个发布面，本批未动。
