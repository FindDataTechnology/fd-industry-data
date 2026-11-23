"""守卫：ConfigMap 里跑的抽取器必须就是仓库里的那份脚本。

后台：`scripts/extract_pharma.py` 由 `scripts/render_extract_configmap.py` 渲染成
`fd-infra-deploy/all-services/xinru/fd-pharma-extract-configmap.yaml`（2026-11-24 迁仓，
restore-industry-render-plane 5.2），集群里 pod 执行的是 ConfigMap 内容，不是仓库文件。
改了脚本忘渲染 ⇒ 集群继续跑旧逻辑而所有测试全绿（本仓已经吃过一次「合并不等于生效」）。

**2026-11-24 实测这条门确实抓到过东西**：集群里的 ConfigMap 是旧版（22540 字符，缺
`_dsn` 修复），仓库脚本 23146 字符。而 11-23 按目录删 k8s/ 后本文件的目标路径消失，
这条门变成恒红——「守着一个已不存在路径的闸」与「集群在跑旧版本」同时成立，
正是它要防的形态。迁到部署仓后重新渲染并与集群对齐。

两条断言：
1. `--check` 退出零：ConfigMap 语义上等于脚本文件。
2. 反向：改一个字节的临时副本必须让 `--check` 报红——先证明这条门能红。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "render_extract_configmap.py"
DEPLOY = Path(os.environ.get("FD_INFRA_DEPLOY_ROOT") or (REPO.parent / "fd-infra-deploy"))
CONFIGMAP = DEPLOY / "all-services" / "xinru" / "fd-pharma-extract-configmap.yaml"
CRONJOB = DEPLOY / "all-services" / "xinru" / "fd-pharma-observations.yaml"
SOURCE = REPO / "scripts" / "extract_pharma.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO, capture_output=True, text=True,
    )


def test_configmap_matches_repo_script():
    proc = _run("--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK EXTRACT_CONFIGMAP_CONSISTENT" in proc.stdout


def test_check_goes_red_when_script_drifts(tmp_path):
    """把仓库脚本改一个字符，--check 必须报红（证明这条门不是恒绿）。"""
    backup = tmp_path / "extract_pharma.py"
    shutil.copy2(SOURCE, backup)
    try:
        SOURCE.write_text(
            SOURCE.read_text(encoding="utf-8") + "\n# drift-injection\n", encoding="utf-8"
        )
        proc = _run("--check")
        assert proc.returncode != 0, "脚本漂移后 --check 仍然绿：这条门是死代码"
        assert "不一致" in proc.stdout
    finally:
        shutil.copy2(backup, SOURCE)


def test_configmap_declares_namespace_matching_cronjob():
    """ConfigMap 与 CronJob 必须同命名空间，否则 pod 挂不到卷（跨命名空间不可引用）。"""
    import yaml

    cm = yaml.safe_load(CONFIGMAP.read_text(encoding="utf-8"))
    cj = yaml.safe_load(CRONJOB.read_text(encoding="utf-8"))
    assert cm["metadata"]["namespace"] == cj["metadata"]["namespace"] == "mcp"
    pod = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    ref = pod["volumes"][0]["configMap"]["name"]
    assert ref == cm["metadata"]["name"]
    assert pod["volumes"][0]["name"] in {m["name"] for m in pod["containers"][0]["volumeMounts"]}
