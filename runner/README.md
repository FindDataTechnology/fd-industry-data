# fd-health-runner 镜像（GHA self-hosted runner）

自愈闭环巡检 workflow（`health-inspect.yml`）的执行载体。构建走标准镜像通道
（**GitHub Actions → TCR（hkccr）**），部署走标准 GitOps 通道（**ArgoCD**）。

## 标准部署（k8s / ArgoCD — cheap 集群 fd-prod）

清单事实源：`fd-infra-deploy/all-services/prod/fd-health-runner.yaml`
（Deployment + PVC，automated.selfHeal 自动同步）。首次上线：

```bash
# 1) 一次性注册 token 入库（不进 git）
TOKEN=$(gh api -X POST repos/FindDataTechnology/fd-industry-data/actions/runners/registration-token --jq .token)
kubectl -n fd-prod create secret generic fd-health-runner-token \
  --from-literal=RUNNER_TOKEN="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
# 2) 提交清单（本仓 fd-infra-deploy）→ ArgoCD 一个轮询周期内拉起
```

- 注册状态与工作目录都在 PVC（`fd-health-runner-data`，`/data`）：Pod 重建免 token；
  彻底换注册 = 删 PVC + 更新 secret + rollout restart。
- 升级 runner 版本/依赖：改 `fd-industry-data/runner/Dockerfile` → 推 main 触发
  `health-runner-image` 构建 → 将清单里的 `sha-*` 标签改为新构建 sha（不可变标签纪律）。
- 镜像链路：GHA 推 **hkccr**（海外快推）→ cheap-3 `tcr-relay-sync.sh` 自动回灌 **ccr**（repos.conf 已登记本镜像）→
  集群清单用 `ccr.ccs.tencentyun.com/...` + `tcr-ccr` pull secret（hkccr 的香港 COS blob 国内节点拉不动）。
- 出网要求：GitHub（注册/接活）+ 中央库 PG 走 tailscale mesh（100.64.0.3:30432，只读角色）。

## 本地调试（不用于服务器部署）

```bash
docker build -f runner/Dockerfile -t fd-health-runner:dev .
docker run --rm -e RUNNER_URL=... -e RUNNER_TOKEN=... \
  -v /tmp/fd-health-state:/data fd-health-runner:dev
```