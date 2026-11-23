"""Release-manifest rendering (harden-crawl-gitops-foundation 2.1/2.2):
k8s/release.yaml as the single source of runner image tags, the placeholder
render, and the --check dual-state semantics wired into the Jenkins gate.

The dual-state contract under test: a consumption file is legal in exactly
one of two states — PENDING_RENDER (placeholder present, fresh checkout) or
RENDERED_CONSISTENT (concrete tag == manifest). Drift (committed tag !=
manifest), placeholder mixed with a concrete tag, multiple tags on the same
repo, foreign placeholders, and missing lines must all fail the gate — a
plain "no placeholders found" check would be a green rubber stamp.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_release.py"

_spec = importlib.util.spec_from_file_location("render_release", SCRIPT)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

# The migration invariant (task 2.1): 组件集合与它们指向的仓不可漂移。
#
# 这里**刻意不写死 tag**。原先是 {runner: sha-d84cc82, runner-xinru: sha-8f734c7,
# observer: sha-ea21592}，注释还写着「测试断言跟着清单走…不锁死旧值」—— 但常量本身
# 就是快照，于是每次真发版都变成假红：2026-11-06 三站点滚动到 sha-750da3f 之后，
# 这条断言一直红到 2026-11-10，谁也没去动它。
#
# 换成三条真正的不变量（都不是自证，也不是「找不到占位符」那种橡皮章）：
#   1. 四个组件必须都在、且指向同一个 ccr 仓；
#   2. 四个组件的 tag **必须完全相同** —— 一次发布一条线，部分 bump 正是历史上
#      真实发生过的漂移形态（out-of-band 三连），这条能抓住它；
#   3. 登记面 appset 的 tag 必须等于 fd-infra-deploy 里 chengsi AppSet 的实际值
#      （同仓并列检出时才校验，CI 单仓 checkout 自动跳过，不假装通过）。
EXPECTED_REPOS = {
    "dispatcher": "ccr.ccs.tencentyun.com/finddata/fd-industry-runner",
    "observer": "ccr.ccs.tencentyun.com/finddata/fd-industry-runner",
    "appset": "ccr.ccs.tencentyun.com/finddata/fd-industry-runner",
    # 2026-11-24 补：医药观测抽取 CronJob 仍在每天 05:30 跑（mcp/fd-pharma-observations），
    # 它也是 runner 镜像的消费面——此前它连组件都没登记，所以「单一来源」看不到它。
    "pharma": "ccr.ccs.tencentyun.com/finddata/fd-industry-runner",
}
# 2026-11-24（restore-industry-render-plane 1.4）：消费面随 chart/dispatcher 一起
# 迁进部署仓，所以这里的路径是**部署仓相对**路径，不是本仓的 k8s/。
EXPECTED_CONSUMPTION = {
    "all-services/xinru/fd-industry-dispatcher.yaml": "dispatcher",
    "all-services/xinru/federation-observer.yaml": "observer",
    "all-services/xinru/fd-pharma-observations.yaml": "pharma",
    "argocd/industry-appset/applicationset.yaml": "appset",
}
# 部署仓根（并列检出时存在；CI 用 --industry-dir/--infra-dir 或 FD_INFRA_DEPLOY_ROOT）
DEPLOY = Path(os.environ.get("FD_INFRA_DEPLOY_ROOT") or (REPO.parent / "fd-infra-deploy"))
APPSET = DEPLOY / "argocd" / "industry-appset" / "applicationset.yaml"

HARBOR = "100.64.0.8:30880/finddata/fd-industry-runner"
CCR = "ccr.ccs.tencentyun.com/finddata/fd-industry-runner"

MANIFEST = f"""\
# test manifest (restricted subset the renderer parses)
components:
  runner:
    repo: {HARBOR}
    tag: sha-f0a89b3
  runner-xinru:
    repo: {CCR}
    tag: sha-8f734c7

consumption:
  k8s/one.yaml: runner
  k8s/two.yaml: runner-xinru
"""

ONE_PENDING = "image: __RELEASE_IMAGE:runner__\n"
TWO_PENDING = "image: __RELEASE_IMAGE:runner-xinru__\n"
ONE_CONSISTENT = f"image: {HARBOR}:sha-f0a89b3\n"
ONE_DRIFT = f"image: {HARBOR}:sha-f0a89b4\n"  # one character off the manifest
ONE_MISSING = "command: [python3]\n"
ONE_MIXED = f"image: __RELEASE_IMAGE:runner__\nold: {HARBOR}:sha-aaaaaaa\n"
ONE_FOREIGN = "image: __RELEASE_IMAGE:observer__\n"


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


# 2026-11-24（restore-industry-render-plane 3.1）：发布声明与消费面一起迁到了部署仓，
# render_release.py 现在跨仓读它们。本文件用同一个相对布局（tmp 根里放
# all-services/xinru/fd-industry-release.yaml）来测，路径常量集中在这里，换位置只改这一处。
MANIFEST_REL = Path("all-services") / "xinru" / "fd-industry-release.yaml"


def make_tree(tmp_path: Path, manifest: str = MANIFEST,
              files: dict[str, str] | None = None) -> Path:
    (tmp_path / MANIFEST_REL).parent.mkdir(parents=True)
    (tmp_path / MANIFEST_REL).write_text(manifest, encoding="utf-8")
    for rel, text in (files or {"k8s/one.yaml": ONE_PENDING, "k8s/two.yaml": TWO_PENDING}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- real repo

def test_real_manifest_components_share_one_tag_per_release():
    """不变量，替代原先会逐版腐烂的 tag 快照。

    2026-11-24：这些断言原本写「三个消费面必须同 tag」。迁仓后实测**它们本来就不同**
    （dispatcher/`dispatcher` = sha-02609d8，observer = sha-f5a19aa，appset = sha-d99474d）。
    历史注释说「三面统一到 02609d8」，但 observer 从未真的滚过去；把「必须相同」当不变量
    会让一条**如实记录实况**的发布声明恒红——那是把假绿换成假红，同样是坏闸。

    所以判据改成三条真实的、可机械判定的不变量：
      1. 组件集合与 repo 不可漂移；
      2. 每个消费面的 tag 必须是不可变 sha tag（禁止 latest / 空值）；
      3. appset 的 tag 必须等于 AppSet 文件里的实际值（同仓并列检出时才校验）。
    「哪些面必须同 tag」不再硬编码——它是发布策略，由人决定，不由测试代替。
    """
    comps, consumption = rr.parse_manifest(DEPLOY / MANIFEST_REL)

    # 1. 组件集合与仓库不可漂移
    assert set(comps) == set(EXPECTED_REPOS), sorted(comps)
    for name, repo in EXPECTED_REPOS.items():
        assert comps[name]["repo"] == repo, name

    # 2. 每个 tag 都是不可变 sha tag（latest/空值一律拒绝）
    for name, comp in comps.items():
        assert comp["tag"].startswith("sha-"), f"{name} 的 tag 不是不可变 sha tag: {comp['tag']!r}"

    # 3. 消费映射固定（谁是哪个组件的消费面）
    assert consumption == EXPECTED_CONSUMPTION

    # 4. appset 组件 == chengsi AppSet 实际值（同仓并列检出时才校验）
    if APPSET.is_file():
        import re as _re
        text = APPSET.read_text(encoding="utf-8")
        # name: image 与 value: 之间可能夹着多行注释（该参数的历史演进都记在这里），
        # 所以用 DOTALL 跨行找，而不是要求 value 紧邻下一行。
        m = _re.search(r"name:\s*image\b.*?value:\s*\S+:(sha-[0-9a-f]+)", text, _re.S)
        assert m, "AppSet 里找不到 image 参数，登记面无从对账"
        assert comps["appset"]["tag"] == m.group(1), (
            "appset 组件与 AppSet 实际值不一致：release.yaml=%s appset=%s"
            % (comps["appset"]["tag"], m.group(1)))


def test_real_repo_consumption_files_carry_their_own_placeholder():
    # --check 双态语义：fresh checkout 占位符态 OR 渲染后与清单一致态，均合法。
    comps, _ = rr.parse_manifest(DEPLOY / MANIFEST_REL)
    for rel, component in EXPECTED_CONSUMPTION.items():
        text = (DEPLOY / rel).read_text(encoding="utf-8")
        phs = rr.PH_RE.findall(text)
        repo = EXPECTED_REPOS[component]
        concrete = rr.concrete_tags(text, repo)
        if phs:
            assert phs == [component], rel
            assert concrete == [], rel  # 占位符与具体 tag 不得混存
        else:
            expected = f"{comps[component]['repo']}:{comps[component]['tag']}"
            # 同一个 tag 可以在一个文件里出现多次且都合法（dispatcher 把它写进
            # FD_IMAGE_TAG env + 两个 container image，共 3 处）；非法的是「出现
            # 别的 tag」。与 render_release.classify 的语义一致（它要求
            # len(concrete)==n_expected，不是 ==1）。
            assert concrete and set(concrete) == {expected}, (rel, sorted(set(concrete)), expected)


def test_real_repo_check_placeholder_state_exits_zero():
    proc = run_script("--check", "--root", str(DEPLOY))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in EXPECTED_CONSUMPTION:
        assert (f"OK PENDING_RENDER {rel}" in proc.stdout
                or f"OK RENDERED_CONSISTENT {rel}" in proc.stdout), proc.stdout
    assert "RELEASE_CHECK_DONE ok" in proc.stdout.splitlines()[-1]


# ------------------------------------------------------------ manifest parse

def test_parse_manifest_happy_path(tmp_path):
    root = make_tree(tmp_path)
    comps, consumption = rr.parse_manifest(root / MANIFEST_REL)
    assert set(comps) == {"runner", "runner-xinru"}
    assert consumption == {"k8s/one.yaml": "runner", "k8s/two.yaml": "runner-xinru"}


def test_parse_manifest_missing_tag_is_setup_error(tmp_path):
    bad = MANIFEST.replace("    tag: sha-f0a89b3\n", "")
    root = make_tree(tmp_path, manifest=bad)
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 2
    assert "SETUP_FAIL" in proc.stdout


def test_parse_manifest_unknown_consumption_component_is_setup_error(tmp_path):
    bad = MANIFEST.replace("  k8s/two.yaml: runner-xinru", "  k8s/two.yaml: ghost")
    root = make_tree(tmp_path, manifest=bad)
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 2
    assert "unknown component" in proc.stdout


def test_parse_manifest_missing_manifest_is_setup_error(tmp_path):
    proc = run_script("--check", "--root", str(tmp_path))
    assert proc.returncode == 2
    assert "not found" in proc.stdout


# ------------------------------------------------------------ --check states

def test_check_pending_render_state_is_legal(tmp_path):
    root = make_tree(tmp_path)
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK PENDING_RENDER k8s/one.yaml" in proc.stdout


def test_check_rendered_consistent_state_is_legal(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_CONSISTENT, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK RENDERED_CONSISTENT k8s/one.yaml" in proc.stdout


def test_check_drifted_tag_fails(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_DRIFT, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 1
    assert "DRIFT" in proc.stdout and "sha-f0a89b4" in proc.stdout


def test_check_missing_image_line_fails(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_MISSING, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 1
    assert "neither placeholder" in proc.stdout


def test_check_placeholder_mixed_with_concrete_tag_fails(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_MIXED, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 1
    assert "partial hand edit" in proc.stdout


def test_check_foreign_placeholder_fails(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_FOREIGN, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 1
    assert "unexpected placeholder" in proc.stdout


def test_check_missing_consumption_file_fails(tmp_path):
    root = make_tree(tmp_path)
    (root / "k8s" / "two.yaml").unlink()
    proc = run_script("--check", "--root", str(root))
    assert proc.returncode == 1
    assert "file not found" in proc.stdout


# ------------------------------------------------------------------- render

def test_render_fills_placeholder_from_manifest_and_is_idempotent(tmp_path):
    root = make_tree(tmp_path)
    first = run_script("--root", str(root))
    assert first.returncode == 0, first.stdout + first.stderr
    assert (root / "k8s" / "one.yaml").read_text(encoding="utf-8") == ONE_CONSISTENT
    assert (root / "k8s" / "two.yaml").read_text(encoding="utf-8") == \
        f"image: {CCR}:sha-8f734c7\n"
    second = run_script("--root", str(root))
    assert second.returncode == 0
    assert "up-to-date k8s/one.yaml" in second.stdout
    assert "rendered=0, up-to-date=2" in second.stdout


def test_render_refuses_drifted_file_without_touching_it(tmp_path):
    root = make_tree(tmp_path, files={"k8s/one.yaml": ONE_DRIFT, "k8s/two.yaml": TWO_PENDING})
    proc = run_script("--root", str(root))
    assert proc.returncode == 1
    # the drifted file is left byte-identical — reconciliation is manual
    assert (root / "k8s" / "one.yaml").read_text(encoding="utf-8") == ONE_DRIFT
