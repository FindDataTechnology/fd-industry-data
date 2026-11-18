"""作业清单与判据的一致性（change schedule-wave-observation-extraction 3.2/3.3）。

**为什么需要这条测试**：CronJob 的 `--only` 名单、内联正文、`items[].path` 三样
都是从 `wave_extract_split.py` 的分类结果派生的。派生链路是「生成器读 git ref →
渲染清单」，所以它只在**有人重新跑生成器**时保持同步；有人直接改清单、或改了抽取器
形态却没重渲染，三样就会错位，而错位的症状是**静默少抽一个源**或者 pod 里
`ModuleNotFoundError`——都不是报错，是观测层悄悄冻住。

本测试读盘上的清单文件（不执行生成器），独立于生成器自己的 `--self-test`：
后者验的是「刚渲染出来的这份自洽」，这里验的是「盘上这份与判据一致」。

两条判据：
1. 清单里每条 `--only` 都等于 `wave_extract_split.scheduled_by_package()` 的结果
   （逐包逐个 SOURCE，顺序无关、集合相等）。
2. 清单里每条命令点到的脚本，都出现在 `test_extract_dsn_normalization.JOB_SCRIPTS`
   里——那一道守住「生产真跑的脚本必须归一化 DSN」。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
#: 清单在兄弟仓里（工作区不是 git 仓，两仓分开）
MANIFEST = REPO.parent / "fd-infra-deploy" / "all-services" / "prod" / "wave-extract-configmap.yaml"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "tests"))
import wave_extract_split as wes  # noqa: E402


pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(),
    reason=f"清单不在盘上（{MANIFEST}）——工作区单仓 checkout 时不判")


@pytest.fixture(scope="module")
def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def test_only_lists_match_the_classifier(manifest_text):
    """清单里的 --only 必须等于分类器算出的名单（逐包）。"""
    found: dict[str, str] = {}
    for m in re.finditer(r'python3 /src/scripts/(extract_\w+\.py)\b.*?--only "([^"]*)"',
                         manifest_text, re.S):
        driver, names = m.group(1), m.group(2)
        pkg = next(p for p, d in wes.DRIVERS.items() if d == driver)
        found[pkg] = names
    expected = {pkg: ",".join(v) for pkg, v in wes.scheduled_by_package(SCRIPTS).items()}
    assert set(found) == set(expected), (
        f"清单里缺这些包的 --only：{sorted(set(expected) - set(found))}")
    for pkg, want in expected.items():
        got = found[pkg]
        assert set(got.split(",")) == set(want.split(",")), (
            f"{pkg} 的 --only 与分类器不一致。\n  盘上：{sorted(got.split(','))}\n"
            f"  判据：{sorted(want.split(','))}\n"
            "改法是重跑生成器（python3 fd-infra-deploy/tools/gen_wave_extract_configmap.py），"
            "不是手改清单")


def test_manifest_commands_are_dsn_normalized(manifest_text):
    """清单点到的每个脚本都必须在 JOB_SCRIPTS 里（那道闸守 DSN 归一化）。"""
    from test_extract_dsn_normalization import JOB_SCRIPTS
    pointed = set(re.findall(r'python3 /src/scripts/(extract_\w+\.py)', manifest_text))
    missing = sorted(pointed - set(JOB_SCRIPTS))
    assert not missing, (
        f"这些脚本会被 wave 作业按排期调用，但没进 JOB_SCRIPTS：{missing}。"
        "加进去，否则 DSN 归一化的回归没有防线")


def test_manifest_ships_common_modules(manifest_text):
    """每个包的 `_common.py` 必须被打进 ConfigMap。

    分类器跳过下划线开头的模块（它们不产生写入形态），但 `_common` 是每个抽取器
    都 import 的公共件——漏了它 pod 里直接 ModuleNotFoundError（2026-11-19 首跑
    实测，整轮四包全炸）。
    """
    for pkg in wes.PACKAGES:
        # 不是每个包都有自己的 _common.py（flower 复用 restaurant 的），按实际文件判
        common = SCRIPTS / pkg / "_common.py"
        if not common.exists():
            continue
        # 清单的 data 键是压平的（长度前缀编码）：`<n>_scripts-<m>_<pkg>-<k>__common.py`
        # —— 段内长度含前导下划线，所以是 `10__common.py` 而不是 `3_common.py`。
        pattern = re.compile(
            r"^  \d+_scripts-\d+_" + re.escape(pkg) + r"-\d+__common\.py: \|$", re.M)
        assert pattern.search(manifest_text), (
            f"{pkg}/_common.py 没在清单里——包内模块会 import 失败")
