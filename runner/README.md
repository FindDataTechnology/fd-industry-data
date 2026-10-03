# fd-health-runner 镜像（GHA self-hosted runner）

自愈闭环巡检 workflow（`health-inspect.yml`）的执行载体。走标准镜像通道：
**GitHub Actions 构建 → TCR（hkccr）→ 目标机 docker pull**，不手工装包、不传文件。

## 目标机部署（xinru-server1）

```bash
# 一次性：注册 token（本机 gh 有权即可，1 小时有效）
TOKEN=$(gh api -X POST repos/FindDataTechnology/fd-industry-data/actions/runners/registration-token --jq .token)

docker pull hkccr.ccs.tencentyun.com/finddata/fd-health-runner:main
docker rm -f fd-health-runner 2>/dev/null || true
docker run -d --name fd-health-runner --restart unless-stopped \
  -e RUNNER_URL=https://github.com/FindDataTechnology/fd-industry-data \
  -e RUNNER_TOKEN="$TOKEN" \
  -e RUNNER_NAME=xinru-server1-health \
  -e RUNNER_LABELS=fd-health \
  -v /opt/fd-health-runner:/runner-data \
  hkccr.ccs.tencentyun.com/finddata/fd-health-runner:main
```

- 注册状态在容器内（`/actions-runner/.runner`）：宿主机重启由 `--restart` 兜底；
  若 `docker rm` 重建容器，需要重新生成 token 再跑一次上面的两步。
- 升级 = 重推镜像（改 `runner/Dockerfile` 的 `RUNNER_VERSION` 或依赖）→ 目标机
  `docker pull && docker rm -f && docker run`（同配方）。
- 工作目录/产物在宿主 `/opt/fd-health-runner`（干净可删）。

## 本地构建（可选调试）

```bash
docker build -f runner/Dockerfile -t fd-health-runner:dev .
```