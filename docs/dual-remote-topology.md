# 双远端拓扑：哪个是真源、谁往哪推

> 2026-11-19 记。**起因是当天我（agent）连续两次判错真源**，先按本地 git 配置
> 断定「gitee 是日常开发地」，又按 CI 位置断定「github 才是真源」，各错一半——
> 实际是**双源分工**。这份文档把事实钉下来，免得下一个人再绕一遍。

## 一句话

**集群从 gitee 读，CI 从 github 跑。两边都不能删，两边之间原本没有同步。**

## 分工表

| 角色 | gitee | github |
|---|---|---|
| ArgoCD 读内容（`repoURL`） | ✅ **部署真源** | ❌ 不读 |
| GitHub Actions | ❌ 无 Actions | ✅ 13 个工作流 |
| 告警/巡检回写（`fd-health-bot`） | ❌ | ✅ `git push origin HEAD:main` |
| 镜像出片（`image.yml`） | ❌ | ✅ → `hkccr.ccs.tencentyun.com` |
| self-hosted runner | ❌ | ✅ `[self-hosted, fd-health]` |

### 证据（可复算）

```bash
# ① 集群只读 gitee
grep -n "repoURL" fd-infra-deploy/argocd/industry-appset/applicationset.yaml
#   → repoURL: git@gitee.com:FindDataTechnology/fd-industry-data.git   (两处)
grep -hn "repoURL" fd-infra-deploy/argocd/app-*.yaml | sort -u
#   → 全部 https://gitee.com/FindDataTechnology/fd-infra-deploy.git

# ② CI 只在 github，且往 github 写
ls .github/workflows/ | wc -l          # → 13
grep -rn "git push origin HEAD:main" .github/workflows/ | wc -l
grep -rhn "runs-on: \[self-hosted" .github/workflows/ | wc -l    # → 8

# ③ 两边各缺什么
git fetch --all
git rev-list --left-right --count gitee/main...origin/main
```

## 为什么两边都不能放弃

**gitee 不能放弃**：国内集群（chengsi / cheap 系）读它，这是硬约束。ArgoCD 的
`repoURL` 写死在 `fd-infra-deploy/argocd/*.yaml` 里，改成 github 意味着集群要能
稳定访问 github —— 这在当前网络条件下不成立。

**github 不能放弃**：`runs-on: [self-hosted, fd-health]` 的 runner 注册在
github，8 个工作流依赖它；告警/巡检的状态回写也走 github。

## 分叉是怎么长出来的（2026-11-19 实测）

没有任何同步机制。所以：

* 人工往 gitee 推代码 → github 不知道
* CI 每 5 分钟往 github 推状态文件 → gitee 不知道

实测当天两侧各差 **36 / 33 个提交**：

- github 独有 36 个（11-16~11-17）：水位冻结判决、宽窗水位历史测推进率、
  SUBMIT 错误回执上限（`fix-turn-fail-starvation`）、308 跳转判词分档。
  **不在任何 gitee 分支上。**
- gitee 独有 33 个：波抽取常驻排期（DCE 断流修复）+ hotel 文档 + CaaS 告警契约，
  以及**同一批 health 工作的较低版本**。

「同一批工作的较低版本」这个判断的证据：`triage.py` / `watermark.py` /
`config.py` 三文件**逐字节相同**，而 `inspector.py` github 侧多出
`_SUBMIT_TURN_FAIL_MAX` / `_unpack_reply` / `_status_failure_kind`，
`_default_poster` 返回 3 元组（带 trace dict）而 gitee 侧是 2 元组。

## 处置：单向镜像 github → gitee

`.github/workflows/mirror-to-gitee.yml`（2026-11-19 立）。方向单向的理由：

* 双向同步在两边同时有提交时**必然冲突**，而冲突发生在 CI 里没人看着，
  症状是「某一侧的提交静默消失」；
* 单向 + 单一写入口不会。**人工改代码请推 github**（`git push origin main`），
  镜像会在几分钟内带到 gitee。

三条硬约束（都写在 workflow 注释里）：

1. **凭据缺失即失败**，不静默跳过 —— 「配了但没生效」的镜像闸比没有更糟。
2. **只 fast-forward，绝不 `--force`** —— gitee 有 github 没有的提交时中止并逐条
   列出，因为 gitee 是部署真源，那些提交可能已在生产生效。
3. **推送前先探 SSH 认证** —— 否则真实原因被 `git push` 的 exit 128 吞掉。

### 启用状态（2026-11-19，已上线）

secret `GITEE_TOKEN` 已配。实测三轮：

1. 推 `c3401f0` → 镜像 success（18s），gitee 跟上，三处 0/0；
2. 再推一个空提交 → 镜像再次 success，gitee 自动跟上（**证明是自动的**）；
3. 我 force push 抹掉那个空提交后 → **镜像 failure 且拒绝覆盖**，报错逐条列出
   gitee 独有的提交。这是第三条硬约束（只 fast-forward）在起作用，行为正确。

### 为什么凭据是 PAT 而不是 SSH key

原本写的是 SSH 部署公钥，实测推翻了：**gitee 的部署公钥只能 clone/pull，
不能 push**（仓库设置页原文「部署公钥配置后的机器，只支持 clone 与 pull
等只读操作」）。而唯一能推的 SSH key 是组织账号 key —— 交给 Actions 等于对
全组织仓开放写权限。

改用 **gitee 仓库级私人令牌**：只对 `fd-industry-data` 生效、权限只勾 `projects`。
实测该令牌 clone 与 push 都通（探针分支建了又删）。

### 一个已知边界

**force push 后镜像会失败**（gitee 有 github 没有的提交）。这不是缺陷——镜像
不能替你决定谁对。处置：本地 `git merge gitee/main` 把那些提交接回来，再推 github。
2026-11-19 我为清理一个探针空提交试过 force push，镜像当场拒绝，就是这么解的。

## 其它仓

| 仓 | 拓扑 |
|---|---|
| `fd-infra-deploy` | 双远端但 **0/0 无分叉**，且**没有 CI**（无自动回写路径）⇒ 人工双推即可，不需要镜像 |
| `dashboards` | **只有 gitee 远端**，消费者只有本机 launchd（集群不读它）⇒ 单源，无问题 |

## 判「哪个远端是真源」的方法

不要看本地的 `branch.main.remote` —— 那是 **clone 时的配置残留**，可能是任何一边
（本仓这份长期是 `gitee`，而当时的实际写入口在 github）。看三样：

1. `git config --get remote.origin.url` 指向谁
2. **CI 在哪里跑、往哪推**（`.github/workflows/` 有无、`runs-on` 是什么）
3. **哪边有对方没有的提交**，以及那些提交是不是同一批工作的不同版本
   （逐文件 `diff` 比标题可靠）

本仓的答案：① origin 指向 github；② CI 在 github 且往 github 写；
③ 两边各有独有提交，但 health 那条线 github 更全。**集群读的是 gitee** —— 这一条
第 3 步看不出来，必须去读 ArgoCD 的 `repoURL`。
