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
   渲染为空、Argo 拒绝清空，属既存 OutOfSync（已挂起，无风险），
   正是下面"待办"里那三个参数要解决的问题。

### 待办（未做，需单独窗口）

仓内声明比线上多 `scheduleTz` / `parked` / `enabled` 三参数：

- `scheduleTz` 是**空操作**（全仓 0 个 manifest 声明 `schedule_tz`，chart 缺省 Asia/Shanghai）；
- `parked` / `enabled` 会让 **31 个 parked 源**从"仍在触发"变为 `suspend: true`。
  这是独立的调度行为变更（含 `bls`/`cncotton` 的 OutOfSync 也会一并消失），
  不做整体 apply 就是因为它会改 31 个源的行为，与本批镜像滚动无关。

另：`fd-industry-dispatcher`（`*/5 * * * *`）仍用 `sha-750da3f`，但它由
`k8s/dispatcher.yaml` 单独管理（标签 `fd-industry/component: dispatcher`，
不在本 AppSet 内），属另一个发布面，本批未动。
