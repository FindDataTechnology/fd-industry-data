"""波抽取器切分判据（change schedule-wave-observation-extraction 1.1）。

**为什么要有这条测试**：`wave-observation-extract` CronJob 用 `--only` 只跑那些
读 `crawl_items` 的抽取器，跳过在进程内直接打上游的那些（`live_items` /
`_load_unit`）。这份 SPLIT 如果是手写常量，它一定会 rot，而且**rot 的方向是静默的**：
把一个已部署的 `live_items` 单元改回正解 `unit_items` 后没人记得加进名单，
CronJob 会继续跳过它，观测层就那么悄悄冻住——正是本 change 要修的那个病灶。

所以判据从源码读（`scripts/wave_extract_split.py` 的 AST 分类），本测试守三件事：

1. **形态与名单必须一致**：`crawl_items` 型必然在 `scheduled_by_package()` 里，
   反之亦然。任何一个模块两种都不像（`unknown`）即红。
2. **被排除的模块必须有具名理由**：`LIVE_FETCH_REASONS` 的键集合必须等于实测的
   live-fetch 集合，且每条理由非空。「排除」不是一个可以默认的态度，得有话说。
3. **驱动确实认得名单里的每个名字**：用驱动自身的发现规则（`load_extractors()`）
   校验 `--only` 里的每个 SOURCE 都能被解析到一个模块——写错一个名字会让整轮
   静默少抽一个源。

反例已构造在 `test_split_catches_a_misclassified_module`：把 `dce` 的人工判据改成
`live-fetch`，第 1、2 条必然红。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))
import wave_extract_split as wes  # noqa: E402


@pytest.fixture(scope="module")
def report() -> dict[str, list[dict]]:
    return wes.scan_scripts_dir(SCRIPTS)


def test_every_module_is_classified(report):
    unknown = [m["key"] for mods in report.values() for m in mods
               if m["kind"] == wes.UNKNOWN]
    assert not unknown, (
        "这些抽取器既不读 crawl_items 也不打上游，判不出形态——"
        "请让 extract() 明确走 unit_items 或 live_items：\n  " + "\n  ".join(unknown))


def test_scheduled_and_live_are_complementary(report):
    """每个模块必须恰好落在两侧之一。"""
    live_measured = {m["key"] for mods in report.values() for m in mods
                     if m["kind"] == wes.LIVE}
    scheduled_modules = set()
    for pkg, mods in report.items():
        for m in mods:
            if m["kind"] == wes.SCHEDULED:
                scheduled_modules.add(m["key"])
    overlap = live_measured & scheduled_modules
    assert not overlap, f"同一模块被判成两种形态：{sorted(overlap)}"

    # 反向：scheduled_by_package 里的 SOURCE 必须真能回指到某个 scheduled 模块
    by_pkg = wes.scheduled_by_package(SCRIPTS)
    for pkg, names in by_pkg.items():
        known = {m["source"] for m in report[pkg] if m["kind"] == wes.SCHEDULED}
        stray = sorted(set(names) - known)
        assert not stray, f"{pkg} 的 --only 名单里有回指不到的 SOURCE：{stray}"


def test_excluded_modules_carry_a_reason(report):
    """排除名单必须等于实测集合，且每条都得有话说。"""
    measured = {m["key"] for mods in report.values() for m in mods
                if m["kind"] == wes.LIVE}
    declared = set(wes.LIVE_FETCH_REASONS)
    assert measured == declared, (
        "排除名单与实测不一致。\n"
        f"  实测多出（新出现的 live-fetch，请在 LIVE_FETCH_REASONS 里具名说明）："
        f"{sorted(measured - declared)}\n"
        f"  名单多出（已不再是 live-fetch，请划掉）：{sorted(declared - measured)}")
    empty = sorted(k for k, v in wes.LIVE_FETCH_REASONS.items() if not v.strip())
    assert not empty, f"这些条目的理由为空：{empty}"


def test_only_names_resolve_in_their_driver(report):
    """`--only` 里的每个 SOURCE 都必须能被驱动自己的发现规则解析到。"""
    for pkg, names in wes.scheduled_by_package(SCRIPTS).items():
        pkg_dir = SCRIPTS / pkg
        resolvable = set()
        for path in sorted(pkg_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            source, kind, _ = wes.classify_text(path.read_text(encoding="utf-8"))
            if kind != "not-an-extractor":
                resolvable.add(source)
        missing = sorted(set(names) - resolvable)
        assert not missing, (
            f"{pkg} 的 --only 里有驱动解析不到的 SOURCE：{missing}"
            f"（驱动 {wes.DRIVERS[pkg]} 按顶层 *.py 的 SOURCE 变量发现模块）")


def test_restaurant_covers_every_unit(report):
    """restaurant 包全量可定时——它是本 change 的主目标（DCE 在这）。"""
    names = wes.scheduled_by_package(SCRIPTS)["restaurant_extractors"]
    assert "dce" in names, "DCE 不在可定时名单里——本 change 的存在理由就是它"
    assert len(names) == len({m["source"] for m in report["restaurant_extractors"]}), (
        "restaurant 包里有模块被判成不可定时，与 design D3 的「全量」不符")


def test_split_catches_a_misclassified_module(monkeypatch):
    """反向用例：把 dce 判成 live-fetch，前两条测试的判据必须能红。"""
    tmp = REPO / ".pytest_split_probe"
    pkg = tmp / "restaurant_extractors"
    pkg.mkdir(parents=True, exist_ok=True)
    try:
        # 一个只打上游、不读 crawl_items 的假抽取器，冒充 dce
        (pkg / "dce.py").write_text(
            'SOURCE = "dce"\n'
            "def extract(cur, notes):\n"
            "    return live_items(SOURCE, 'run_dce', notes)\n",
            encoding="utf-8")
        mods = wes.scan(pkg)
        assert [m["kind"] for m in mods] == [wes.LIVE], mods
        # 实测集合里有它、名单里没有 ⇒ test_excluded_modules_carry_a_reason 红
        measured = {m["key"] for m in mods if m["kind"] == wes.LIVE}
        assert measured - set(wes.LIVE_FETCH_REASONS) == {"restaurant_extractors/dce"}
        # 而 scheduled_by_package 里不会有 dce
        assert "dce" not in wes.scheduled_by_package(tmp)["restaurant_extractors"]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
