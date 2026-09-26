"""Landing channel four-path tests (harness-source-landing tasks 1.1/1.2).

Each test lands into a throwaway depth-1 clone of the real repo — full
isolation, no mutation of the working checkout, safe next to concurrent
sessions.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import land_source  # noqa: E402

SRC = "zz-land-test"


@pytest.fixture
def sandbox(tmp_path):
    clone = tmp_path / "repo"
    subprocess.run(["git", "clone", "--depth", "1", "--quiet", str(REPO), str(clone)],
                   check=True)
    for k, v in (("user.name", "land-test"), ("user.email", "land@test.local")):
        subprocess.run(["git", "-C", str(clone), "config", k, v], check=True)
    return clone


def _inputs(tmp_path, *, name=SRC, entry=None, extra_manifest="", spider_code=None):
    entry = entry or f"run_{name.replace('-', '_')}"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f'version: "1"\nname: {name}\nlabel: Land Test\nsource_url: https://example.com\n'
        f"scanner_mode: upstream-curated\nranking_seed: [0.5, 0.5]\nfunctions: []\n"
        f"concepts: []\nentities: []\n{extra_manifest}")
    spider = tmp_path / "spider.py"
    spider.write_text(spider_code or (
        f"def {entry}(limit: int = 100) -> list[dict]:\n"
        f"    return [{{'ok': 1}}][:limit]\n"))
    return manifest, spider


def _land(sandbox, manifest, spider):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "land_source.py"),
         str(manifest), str(spider), "--repo-root", str(sandbox)],
        capture_output=True, text=True)


def test_green_lands_and_commits(tmp_path, sandbox):
    manifest, spider = _inputs(tmp_path)
    proc = _land(sandbox, manifest, spider)
    assert proc.returncode == 0, proc.stderr
    assert (sandbox / "spiders" / SRC / "manifest.yaml").exists()
    committed = subprocess.run(
        ["git", "-C", str(sandbox), "log", "-1", "--format=%s"],
        capture_output=True, text=True).stdout
    assert SRC in committed and "land harness-generated source" in committed
    # v2 aligned, no schedule
    import yaml as _yaml

    landed = _yaml.safe_load((sandbox / "spiders" / SRC / "manifest.yaml").read_text())
    assert str(landed["version"]) == "2" and "schedule" not in landed


def test_gate_red_rejects_atomically(tmp_path, sandbox):
    # unregistered site fails validate_manifests -> nothing committed
    manifest, spider = _inputs(tmp_path, extra_manifest="site: cheap\n")
    proc = _land(sandbox, manifest, spider)
    assert proc.returncode == 1
    assert "REJECTED" in proc.stderr and "site" in proc.stderr
    assert not (sandbox / "spiders" / SRC).exists()
    status = subprocess.run(["git", "-C", str(sandbox), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_missing_entry_rejected(tmp_path, sandbox):
    manifest, spider = _inputs(tmp_path, entry="run_something_else")
    proc = _land(sandbox, manifest, spider)
    assert proc.returncode == 1
    assert f"run_zz_land_test" in proc.stderr
    assert not (sandbox / "spiders" / SRC).exists()


def test_schedule_is_stripped_not_landed(tmp_path, sandbox):
    manifest, spider = _inputs(tmp_path, extra_manifest='schedule: "23 3 * * *"\n')
    proc = _land(sandbox, manifest, spider)
    assert proc.returncode == 0, proc.stderr
    landed = (sandbox / "spiders" / SRC / "manifest.yaml").read_text()
    assert "schedule" not in landed and "stripped schedule" in proc.stdout
