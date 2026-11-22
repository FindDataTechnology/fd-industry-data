#!/usr/bin/env python3
"""把教育目录传导桥渲染成 ConfigMap（change automate-education-catalog-ingest）。

与 `render_extract_configmap.py`（pharma）同款理由：抽取器跑在 `mcp` 命名空间
（写 `education_catalog` 需 `fd` 角色，DSN 在 `mcp/fd-mcp-env`），而 `fd-industry-gitee-token`
在 fd-industry 命名空间，K8s 不允许跨命名空间 secretKeyRef。脚本从 ConfigMap 挂载执行，
无需 git、无需凭据、无需重建镜像；`--check` 把「仓内脚本 ↔ 集群 ConfigMap」漂移做成可检测。

与 pharma 的差别：抽取器 import 了 `edu_catalog/normalize.py`（共享归一，单一真源）与
`scripts/_semextract.py`（DSN 归一）。故本 ConfigMap 打包**三个文件**并保留相对目录结构，
pod 内 `PYTHONPATH=/src` 即可按原样 import。

用法::

    python3 scripts/render_extract_education_configmap.py            # 写文件
    python3 scripts/render_extract_education_configmap.py --check    # 只校验
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "k8s" / "extract-education-configmap.yaml"

# key → 仓内相对路径（pod 内挂到 /src 下，PYTHONPATH=/src）
FILES = {
    "extract_education.py": "scripts/extract_education.py",
    "_semextract.py": "scripts/_semextract.py",
    "normalize.py": "edu_catalog/normalize.py",
}
LOGICAL_NAME = "fd-education-ingest-src"

HEADER = """\
# GENERATED — 不要手改。由 `scripts/render_extract_education_configmap.py` 从仓内脚本渲染；
# `--check` 在校验不一致时退出非零。
#
# 部署（与 CronJob 一起 apply）：
#   scp k8s/extract-education-configmap.yaml k8s/extract-education.yaml fd-deploy:/tmp/
#   ssh fd-deploy "sudo k3s kubectl apply \\
#     -f /tmp/extract-education-configmap.yaml -f /tmp/extract-education.yaml"
#
# **改脚本后必须重跑渲染并 apply**，否则集群里跑的还是旧版本。
"""


def _indent(body: str) -> str:
    if not body.endswith("\n"):
        body += "\n"
    return "".join(("    " + ln if ln.strip() else "\n") for ln in body.splitlines(True))


def render() -> str:
    parts = [
        HEADER + "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
        f"  name: {LOGICAL_NAME}\n  namespace: mcp\n"
        "  labels:\n    fd-industry/component: education-catalog-ingest\n"
        "data:\n"
    ]
    for key, rel in FILES.items():
        body = (REPO / rel).read_text(encoding="utf-8")
        parts.append(f"  {key}: |\n" + _indent(body))
    return "".join(parts)


def check() -> str:
    if not TARGET.exists():
        return f"FATAL: {TARGET.relative_to(REPO)} 不存在，先跑一次渲染"
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover
        return "" if TARGET.read_text() == render() else "ConfigMap 与脚本文本不一致"
    got = (yaml.safe_load(TARGET.read_text(encoding="utf-8")) or {}).get("data", {})
    for key, rel in FILES.items():
        want = (REPO / rel).read_text(encoding="utf-8")
        if got.get(key) != want:
            return (f"ConfigMap 内 {key} 与仓库脚本 {rel} 不一致；"
                    f"configmap={len(got.get(key) or '')}B script={len(want)}B；重跑渲染")
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        problem = check()
        if problem:
            print(problem)
            return 1
        print(f"OK EXTRACT_EDU_CONFIGMAP_CONSISTENT {TARGET.relative_to(REPO)}")
        return 0
    TARGET.write_text(render(), encoding="utf-8")
    print(f"WROTE {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
