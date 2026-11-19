"""打包判据：告警运行期要读的数据文件必须真的进 wheel。

**这个测试存在的理由**（2026-11-19 实测）：

`caas.py` 用 `Path(__file__).parent / "caas-forms.json"` 读权威表，
而 `delivery.resolve_target()` 每次投递都要读它。但 `pyproject.toml` 的
`package-data` 只声明了 `sites.yaml` —— pip 安装那份包里没有这个 json，
于是 `resolve_target()` 抛 `FileNotFoundError`，**每条告警线的投递都变成
not_configured**。

为什么潜伏了很久：所有测试都从**源码树**取文件（`caas.__file__` 在工作树里
当然存在），所以它们测的是「副本与权威文件一致」，不是「文件会不会被装进包」。
两者是不同的命题，而只有后者在线上成立。

而线上又偏偏一直 `firing: 0`（静默期），投递分支根本不进 —— 空跑和成功长得
一模一样。只要真有一条告警要发，七条线会一起变哑。

所以这条测试真的去 **build 一个 wheel 并检查它的内容**，而不是查文件系统。
判据必须是「装出来的东西里有它」，不是「仓库里有它」。
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: 运行期会被 `Path(__file__).parent` 读到的数据文件（相对包根）。
#: 新增这类文件时必须在这里登记——否则线上 FileNotFoundError，而源码树的测试全绿。
REQUIRED_PACKAGE_DATA = [
    "fd_industry_data/alerts_core/caas-forms.json",
    "fd_industry_data/sites.yaml",
]


def _build_wheel(tmp_path: Path) -> Path:
    """在隔离目录里 build 一个 wheel。不装、不联网。"""
    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "-w", str(out), str(REPO)],
        capture_output=True, text=True, timeout=600,
    )
    wheels = sorted(out.glob("*.whl")) if out.exists() else []
    if not wheels:
        pytest.skip(
            "无法构建 wheel（缺 build 后端或环境不允许）；"
            f"pip 输出尾部：{(proc.stderr or proc.stdout)[-400:]}"
        )
    return wheels[-1]


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> set[str]:
    wheel = _build_wheel(tmp_path_factory.mktemp("wheel"))
    with zipfile.ZipFile(wheel) as zf:
        return set(zf.namelist())


@pytest.mark.parametrize("rel", REQUIRED_PACKAGE_DATA)
def test_runtime_data_file_is_inside_the_wheel(wheel_names, rel):
    assert rel in wheel_names, (
        f"{rel} 不在 wheel 里。\n"
        "它在源码树中存在，但没被写进 pyproject 的 [tool.setuptools.package-data]，\n"
        "所以 pip 安装出来的包缺这个文件 ⇒ 运行期 Path(__file__).parent 读不到 ⇒\n"
        "FileNotFoundError。2026-11-19 就是这样让七条告警线的投递一起变成\n"
        "not_configured，而所有源码树测试全绿（它们读的是工作树，不是装出来的包）。\n"
        "修法：把匹配模式加进 [tool.setuptools.package-data]。"
    )


def test_alerts_core_reads_only_files_that_ship():
    """反向检查：alerts_core 里 `Path(__file__).parent / "xxx"` 的每个字面量，
    都必须在 REQUIRED_PACKAGE_DATA 或同目录的 .py 里。

    这条防的是「下次有人再加一个运行期数据文件却忘了登记」——
    上面那条 parametrize 只覆盖已登记的，这条覆盖**未登记的**。
    """
    import re

    core = REPO / "fd_industry_data" / "alerts_core"
    registered = {Path(p).name for p in REQUIRED_PACKAGE_DATA}
    offenders = []
    for py in core.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        for m in re.finditer(r'with_name\(\s*["\']([^"\']+)["\']', src):
            name = m.group(1)
            if name in registered or name.endswith(".py"):
                continue
            offenders.append(f"{py.name} 读 {name}")
        for m in re.finditer(r'__file__\)?\s*/\s*["\']([^"\']+\.[a-z]+)["\']', src):
            name = m.group(1)
            if name in registered or name.endswith(".py"):
                continue
            offenders.append(f"{py.name} 读 {name}")
    assert not offenders, (
        "alerts_core 里有运行期读的数据文件没登记进 REQUIRED_PACKAGE_DATA：\n  "
        + "\n  ".join(offenders)
        + "\n登记后确认它也在 [tool.setuptools.package-data] 里，否则装出来的包缺文件。"
    )