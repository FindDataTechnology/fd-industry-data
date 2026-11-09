# Gitee token 失效导致行业线全面停摆（2026-11-09）

> 事故记录。诊断命令与判据见 change `harden-crawl-delivery` 的 design D8。
> **未修**：截至最后一次观测仍在持续。

## 摘要

- 首次观测：2026-11-09T15:50Z（`fd-industry-lhb-daily-29903990`）
- 最后一次观测：2026-11-09T18:32Z
- 最近一次成功 pod：2026-11-09T15:30Z（此后 3 小时无任何源成功取数）

## 根因

`fd-industry` ns 的 secret `fd-industry-gitee-token`（created 2026-09-25，45 天未更新）
中的 token 已失效。所有 CronJob 的 `checkout` initContainer 依赖它做
`git clone --depth 1 --sparse`，于是全部失败：

```
Cloning into '/content'...
remote: [session-…] FindDataTechnology: Incorrect username or password (access token)
fatal: Authentication failed for 'https://gitee.com/FindDataTechnology/fd-industry-data.git/'
```

exit code 全部为 **128**（git 认证），无 OOM(137) 或脚本错误(1) 混杂 —— 范围是确定的。

## 影响

- **tencent 集群的 k8s 执行通道停摆**：所有到点的 CronJob 在 init 阶段即失败，runner 从未启动。
  这是主力通道 —— `crawl_sources` 里 120 条有排期的源几乎全在 tencent
- **tencent 的 dispatcher 同时失效**（同批失败 6 个）：该站点的例外触发、回填、
  Console trigger-now 不可用
- **`crawl_runs` 里没有任何记录** —— 这是本类故障最难发现之处：报到契约在 runner 内，
  而 runner 根本没起来
- 191 个 `Completed` pod 全部早于 15:30Z，不构成「仍在正常运行」的证据
- **docker 站点未受影响**（2026-11-09T18:40Z 核查）：`xinru-server1` 心跳 3 分钟前、
  `sync_sources` 18:40:04 成功（241 条源清单完好，128 条有排期），说明它自己的
  checkout 正常。另外两个 docker 站点（`xinru-server2`、`zihan`）心跳停在 15:30Z，
  与 tencent 同一时刻，但它们的源只有 2-2 个，不足以补位
  → **准确说法：停的是 tencent 的 k8s 执行通道，不是「全线」**。docker 站点合计仅
  8 个有排期的源，主力 120 源全在 tencent

受影响源（每次观测略有增加）：`eia-v2`、`dispatcher`、`nmc-weather`、`lhb-daily`、
`dounan-flower-price`、`education-podcast`、`nifdc-batchcert`、`portwatch-imf`、`zgdypf-boxoffice`

## 凭据为何只失效了一半

同一 gitee 账号有**两套消费方、两份 secret**：

| 消费方 | secret | created | 值 sha 前 8 位 |
|---|---|---|---|
| CronJob initContainer | `fd-industry/fd-industry-gitee-token` | 2026-09-25T14:00:32Z | `dedaf44e` |
| ArgoCD repo | `argocd/repo-*`（3 个） | 2026-11-09T17:22:16Z | `da39a3ee` |

`da39a3ee` 是空串的 sha1 —— 那三个 secret 的 `password` 键为空（凭据可能在
`sshPrivateKey`，或刚被某次 apply 清空）。两个 ns 的取值不同 → **换 token 只换了一边**，
于是出现「Argo 侧刚重建、CronJob 侧 45 天没动」的半瘫状态。

## 处置（需 gitee owner 权限）

1. 重铸 token（读权限即可）
2. 写入 `fd-industry/fd-industry-gitee-token`（key = `token`）
3. 同步更新 `argocd/repo-*` 中指向该仓的 secret
4. 清掉 in-flight 失败 pod，避免 backoff 继续重试失败副本
5. 实测：`kubectl create job --from=cronjob/fd-industry-<src> verify-gitee-<ts>`，
   确认 pod 不再 `Init:Error`

## 待补的能力（本 change 之外）

停摆近 3 小时而 `crawl_runs` 无任何记录，说明诊断面不覆盖「pod 起不来」这一类。
既有 spec「调度侧为未报到的失败运行合成记录」只覆盖了容器的非零退出，
**init 容器失败属同一形态却无人合成**。已在 change 中记为 tasks 8.2。

---

## 附录：为什么「修好 token」还不够（2026-11-09 19:00Z 追加）

排查过程中发现，恢复取数只解决一半问题 —— **即使 token 修好，生产跑的还是旧代码**。

### 发布链路实际是断的

```
git push → CI 构建镜像 ✓（自动）
        → 集群 appset 的 image 参数 → ✗ 手写死 tag，要人跑 fd-image-bump.sh --apply
        → 按源 CronJob 拉新镜像   → ✗ 依赖上一步
```

实测三方不一致：

| 位置 | 值 |
|---|---|
| 按源 CronJob（120 个）实际运行 | `sha-34b0330` |
| `k8s/release.yaml` 的 `runner-tencent-cron` | `sha-750da3f`（采用日 11-06 的快照） |
| `fd-infra-deploy` 里的 appset 文件 | `sha-750da3f` |
| 内容仓 HEAD | `34b0330` 之后再前进 **46 个提交** |

`fd-image-bump.sh` 自己的注释就写着这件事（2026-11-08）：*"appset 的 helm 参数 image 是手写死 tag。
于是管线全绿、镜像齐全，集群却一直跑几个月前的那一片（实测 79 个 CronJob 停在 sha-750da3f）"*。
该脚本 `--apply` **只改集群里的 appset，不改 git** —— 所以集群永远比 git 新一步，
git 里的 appset 只是历史快照。

### 后果

- 本仓库 11-09 15:06Z 之后的 46 个提交（含 `33e872b` 起的全部改动）**从未进入生产**
- 已核验：`crawl_runs` 里 `outcome`/`error_class`/`parse_error` 三列不存在、
  `metrics ? 'watermark'` = 0 行、`metrics` 里从未出现 `clone_seconds`/`peak_rss_mb`/`runner_seconds`、
  近 7 天 `status` 只有 `success/failed/running`（无 `blocked-config`）
- **`FD_SCHEMA_MANAGED=1` 让 `ensure_schema()` 直接 return** —— 即使换上含 SCHEMA_DDL 的新镜像，
  运行时也**不会**建那三列，必须由部署侧单独执行 DDL

### 正确的恢复顺序

| # | 动作 | 不做会怎样 |
|---|---|---|
| 1 | 换 gitee token（`fd-industry` 与 `argocd` 两处 secret 同值） | 取数不恢复 |
| 2 | 手工执行 `crawl_runs` 三列 DDL | 新镜像的 `outcome` 写入失败，`finish_run` 整条 UPDATE 报错 |
| 3 | `scripts/fd-image-bump.sh --apply` 推到含新改动的镜像 | 恢复的是 46 个提交之前的旧代码 |
| 4 | （立项）appset 纳管 GitOps | 每次发布都要手工 bump，这次是第 N 次静默落后 |

**只做第 1 步，等于把系统恢复到「48 小时前的旧代码 + 一堆没生效的改动」。**
