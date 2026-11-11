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

## 5. 尚未做的一件事（需要裁决）

两个 CronJob 目前仍用 `sha-e6f5f01`（不含 `pdftext.extract_words`）。新镜像
`sha-41af218` 已验证可用（pod 内 `extract_words 存在: True`、单元可导入、全量 3911 行
落库）。把 tag 滚上去有两条路：

1. 改 `fd-infra-deploy` 的 AppSet 声明 + `reconcile-industry-appset.sh tencent apply`——
   **风险**：仓内声明与集群实况还有三处漂移（`repoURL` 形态、仓内多出
   `scheduleTz/parked/enabled` 三参数、`image` 仓内写的是更旧的 `sha-750da3f`），
   整体 apply 会把全站镜像**回退**到 750da3f，并给 167 个 CronJob 一起加参数。
2. 只给这两个 CronJob `kubectl patch` 镜像（点对点，无副作用），但下次 AppSet 重渲染
   会被模板值覆盖回去。

**不做整体 apply 的理由**：线上 pod 实际镜像已经是混合的（86 个 e6f5f01、57 个 ea21592、
36 个 92ae150、28 个 01c5ffb…），说明有并发会话在手工滚动不同源；此时整体 replace 极易
把别人的新片回退成旧片。这条与 AGENTS.md「出片/GitOps 改动同一时刻只允许一个会话做」一致。
