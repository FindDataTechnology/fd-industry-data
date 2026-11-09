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
