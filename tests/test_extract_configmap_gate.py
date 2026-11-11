"""守卫：ConfigMap 里跑的抽取器必须就是仓库里的那份脚本。

后台：`scripts/extract_pharma.py` 由 `scripts/render_extract_configmap.py` 渲染成
`k8s/extract-pharma-configmap.yaml`，集群里 pod 执行的是 ConfigMap 内容，不是仓库文件。
改了脚本忘渲染 ⇒ 集群继续跑旧逻辑而所有测试全绿（本仓已经吃过一次「合并不等于生效」）。

两条断言：
1. `--check` 退出零：ConfigMap 语义上等于脚本文件。
2. 反向：改一个字节的临时副本必须让 `--check` 报红——先证明这条门能红。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "render_extract_configmap.py"
CONFIGMAP = REPO / "k8s" / "extract-pharma-configmap.yaml"
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
    cj = yaml.safe_load((REPO / "k8s" / "extract-pharma.yaml").read_text(encoding="utf-8"))
    assert cm["metadata"]["namespace"] == cj["metadata"]["namespace"] == "mcp"
    pod = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    ref = pod["volumes"][0]["configMap"]["name"]
    assert ref == cm["metadata"]["name"]
    assert pod["volumes"][0]["name"] in {m["name"] for m in pod["containers"][0]["volumeMounts"]}
