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
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "scripts" / "extract_pharma.py"
# 2026-11-24（restore-industry-render-plane 5.2）：渲染产物随其余机队绑定清单迁进部署仓。
# 它此前在 k8s/，2026-11-23 按目录删除后**目标文件消失而闸仍在跑**——闸恒报
# 「不存在，先跑一次渲染」。而集群里那个 ConfigMap 还在每天 05:30 执行，
# 且**内容是旧版**（缺 _dsn 修复，实测 live 22540 字符 vs 仓库 23146）。
# 这就是「闸守着一个已经不存在的路径」的完整形态：真东西活着，守着它的闸死了。
DEPLOY = Path(
    os.environ.get("FD_INFRA_DEPLOY_ROOT") or (REPO.parent / "fd-infra-deploy")
)
TARGET = DEPLOY / "all-services" / "xinru" / "fd-pharma-extract-configmap.yaml"

HEADER = """\
# GENERATED — 不要手改。由 `scripts/render_extract_configmap.py` 从
# `scripts/extract_pharma.py` 渲染；`--check` 在校验不一致时退出非零。
#
# 部署（out-of-band apply，与 fd-industry-dispatcher / federation-observer 同目录）：
#   kubectl --context tencent apply -f all-services/xinru/fd-pharma-extract-configmap.yaml
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
        return f"FATAL: {TARGET} 不存在，先跑一次渲染"
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
        print(f"OK EXTRACT_CONFIGMAP_CONSISTENT {TARGET}")
        return 0
    TARGET.write_text(render(), encoding="utf-8")
    print(f"WROTE {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
