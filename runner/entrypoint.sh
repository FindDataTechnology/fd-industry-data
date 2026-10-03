#!/usr/bin/env bash
# runner 容器入口：首次启动用一次性注册 token 注册；已有注册状态（.runner）则直接接活。
#
# 首次运行需环境变量：
#   RUNNER_URL    https://github.com/FindDataTechnology/fd-industry-data
#   RUNNER_TOKEN  注册 token（gh api .../registration-token 生成，1 小时有效）
# 可选：RUNNER_NAME（默认 fd-health-runner）、RUNNER_LABELS（默认 fd-health）
set -euo pipefail

cd /actions-runner

if [ ! -f .runner ]; then
  : "${RUNNER_URL:?RUNNER_URL required on first boot}"
  : "${RUNNER_TOKEN:?RUNNER_TOKEN required on first boot}"
  ./config.sh --unattended \
    --url "${RUNNER_URL}" \
    --token "${RUNNER_TOKEN}" \
    --name "${RUNNER_NAME:-fd-health-runner}" \
    --labels "${RUNNER_LABELS:-fd-health}" \
    --work /runner-data/_work
fi

exec ./run.sh