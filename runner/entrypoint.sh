#!/usr/bin/env bash
# runner 容器入口（k8s Deployment / docker 均可用）
#
# 状态目录（PVC/卷，默认 /data/runner）：镜像内的 runner 在首次启动时整体拷入，
# 注册状态（.runner/.credentials）与工作目录都留在卷里——此后 Pod 重建/重启
# 都不再需要 token。仅第一次注册需要：
#   RUNNER_URL    https://github.com/FindDataTechnology/fd-industry-data
#   RUNNER_TOKEN  一次性注册 token（gh api .../registration-token）
# 可选：RUNNER_NAME（默认 fd-health-runner）、RUNNER_LABELS（默认 fd-health）、
#       RUNNER_STATE_DIR（默认 /data/runner）
set -euo pipefail

STATE="${RUNNER_STATE_DIR:-/data/runner}"
mkdir -p "$STATE"

if [ ! -x "$STATE/bin/Runner.Listener" ]; then
  cp -a /actions-runner/. "$STATE"/
fi

cd "$STATE"

if [ ! -f .runner ]; then
  : "${RUNNER_URL:?RUNNER_URL required on first boot}"
  : "${RUNNER_TOKEN:?RUNNER_TOKEN required on first boot}"
  ./config.sh --unattended \
    --url "${RUNNER_URL}" \
    --token "${RUNNER_TOKEN}" \
    --name "${RUNNER_NAME:-fd-health-runner}" \
    --labels "${RUNNER_LABELS:-fd-health}" \
    --work "$STATE/_work"
fi

exec ./run.sh