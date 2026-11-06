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
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_release.py"

_spec = importlib.util.spec_from_file_location("render_release", SCRIPT)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

# The migration invariant (task 2.1): the manifest's repo:tag values must stay
# byte-identical to the tags that were live in the clusters when release.yaml
# was introduced — a bump here means a real release (task 2.3 drills it).
# 2026-11-06 三站点演练实况对账后校正（out-of-band 漂移三连）：
# 建档时取的是仓内旧文件值，集群实况才是真值。测试断言跟着清单走，
# 但清单的 tag 是动态读取的（bump 即真实发布，不锁死旧值）。
EXPECTED_COMPONENTS = {
    "runner": ("ccr.ccs.tencentyun.com/finddata/fd-industry-runner", "sha-d84cc82"),
    "runner-xinru": ("ccr.ccs.tencentyun.com/finddata/fd-industry-runner", "sha-8f734c7"),
    "observer": ("ccr.ccs.tencentyun.com/finddata/fd-industry-runner", "sha-ea21592"),
}
EXPECTED_CONSUMPTION = {
    "k8s/dispatcher.yaml": "runner",
    "k8s/xinru-dispatcher.yaml": "runner-xinru",
    "k8s/federation-observer.yaml": "observer",
}

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


def make_tree(tmp_path: Path, manifest: str = MANIFEST,
              files: dict[str, str] | None = None) -> Path:
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "release.yaml").write_text(manifest, encoding="utf-8")
    for rel, text in (files or {"k8s/one.yaml": ONE_PENDING, "k8s/two.yaml": TWO_PENDING}).items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- real repo

def test_real_manifest_components_match_pre_change_tags():
    comps, consumption = rr.parse_manifest(REPO / "k8s" / "release.yaml")
    # runner-tencent-cron 是登记面（AppSet 在 fd-infra-deploy 仓，无消费文件），
    # 不参与 --check 消费比对，但 tag 必须如实登记
    assert comps["runner-tencent-cron"] == {
        "repo": "ccr.ccs.tencentyun.com/finddata/fd-industry-runner",
        "tag": "sha-ea21592",
    }
    consuming = {k: v for k, v in comps.items() if k in EXPECTED_COMPONENTS}
    assert consuming == {name: {"repo": repo, "tag": tag}
                         for name, (repo, tag) in EXPECTED_COMPONENTS.items()}
    assert consumption == EXPECTED_CONSUMPTION


def test_real_repo_consumption_files_carry_their_own_placeholder():
    # --check 双态语义：fresh checkout 占位符态 OR 渲染后与清单一致态，均合法。
    # 2.3 演练后仓库处于「渲染一致态」（tag 逐字等于清单），故不能只认占位符。
    comps, _ = rr.parse_manifest(REPO / "k8s" / "release.yaml")
    for rel, component in EXPECTED_CONSUMPTION.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        phs = rr.PH_RE.findall(text)
        repo = EXPECTED_COMPONENTS[component][0]
        concrete = rr.concrete_tags(text, repo)
        if phs:
            assert phs == [component], rel
            assert concrete == [], rel  # 占位符与具体 tag 不得混存
        else:
            expected = f"{comps[component]['repo']}:{comps[component]['tag']}"
            assert concrete == [expected], (rel, concrete, expected)


def test_real_repo_check_placeholder_state_exits_zero():
    proc = run_script("--check", "--root", str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in EXPECTED_CONSUMPTION:
        assert (f"OK PENDING_RENDER {rel}" in proc.stdout
                or f"OK RENDERED_CONSISTENT {rel}" in proc.stdout), proc.stdout
    assert "RELEASE_CHECK_DONE ok" in proc.stdout.splitlines()[-1]


# ------------------------------------------------------------ manifest parse

def test_parse_manifest_happy_path(tmp_path):
    root = make_tree(tmp_path)
    comps, consumption = rr.parse_manifest(root / "k8s" / "release.yaml")
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
