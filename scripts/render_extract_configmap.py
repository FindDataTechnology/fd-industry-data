#!/usr/bin/env python3
"""把 `scripts/extract_pharma.py` 渲染成 ConfigMap（change schedule-pharma-observation-extraction）。

**为什么需要这一步**：抽取器跑在 `mcp` 命名空间（它要写 `concepts`/`entities`/
`semantic_observations`，只有 `fd` 角色看得见，而 `fd` 的 DSN 在 `mcp/fd-mcp-env`）。
但仓库是私有的、`fd-industry-gitee-token` 在 `fd-industry` 命名空间，K8s 不允许跨命名空间
`secretKeyRef`——所以「pod 启动时 git clone 代码」这条路要么复制一份 token（凭据多一个
消费者，轮换必漏），要么把脚本塞进 runner 镜像（一次小改就要滚 126 个 CronJob 的镜像）。

第三条：脚本内容由本仓渲染成 ConfigMap，pod 直接从挂载卷执行。无需 git、无需凭据、
无需重建镜像；代价是"仓库里的脚本"与"集群里的 ConfigMap"可能漂移——所以 `--check`
把这条漂移做成可检测的（与 `render_release.py` 对镜像 tag 的判据同一套纪律）。

用法::

    python3 scripts/render_extract_configmap.py            # 写文件
    python3 scripts/render_extract_configmap.py --check    # 只校验，不一致退出非零
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "scripts" / "extract_pharma.py"
TARGET = REPO / "k8s" / "extract-pharma-configmap.yaml"

HEADER = """\
# GENERATED — 不要手改。由 `scripts/render_extract_configmap.py` 从
# `scripts/extract_pharma.py` 渲染；`--check` 在校验不一致时退出非零。
#
# 部署（与 CronJob 一起 apply）：
#   scp k8s/extract-pharma-configmap.yaml k8s/extract-pharma.yaml fd-deploy:/tmp/
#   ssh fd-deploy "sudo k3s kubectl apply \\
#     -f /tmp/extract-pharma-configmap.yaml -f /tmp/extract-pharma.yaml"
#
# **改脚本后必须重跑渲染并 apply**，否则集群里跑的还是旧版本。
apiVersion: v1
kind: ConfigMap
metadata:
  name: fd-pharma-extract-src
  namespace: mcp
  labels:
    fd-industry/component: pharma-observations
data:
  extract_pharma.py: |
"""


def render() -> str:
    body = SOURCE.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    indented = "".join(("    " + line if line.strip() else "\n") for line in body.splitlines(True))
    return HEADER + indented


def check() -> str:
    """返回空串表示一致，否则返回人读的差异摘要。"""
    if not TARGET.exists():
        return f"FATAL: {TARGET.relative_to(REPO)} 不存在，先跑一次渲染"
    actual = TARGET.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - 渲染侧一定有 yaml
        return "" if actual == render() else "ConfigMap 与脚本文本不一致（无 yaml 可做语义比对）"
    got = (yaml.safe_load(actual) or {}).get("data", {}).get("extract_pharma.py")
    want = SOURCE.read_text(encoding="utf-8")
    if got == want:
        return ""
    return (
        "ConfigMap 内的 extract_pharma.py 与仓库脚本不一致："
        f"configmap={len(got or '')}B script={len(want)}B；重跑 render_extract_configmap.py"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        problem = check()
        if problem:
            print(problem)
            return 1
        print(f"OK EXTRACT_CONFIGMAP_CONSISTENT {TARGET.relative_to(REPO)}")
        return 0
    TARGET.write_text(render(), encoding="utf-8")
    print(f"WROTE {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
