"""舰队基线：在产源名单解析、报告生成（假 runner 打桩）、dry-run。全离线。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import fleet_baseline  # noqa: E402
from fleet_baseline import (  # noqa: E402
    classify_exit,
    discover_scheduled_sources,
    main,
    parse_runner_output,
)


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    spec = {
        "alpha": "0 4 * * *",     # 在产
        "beta": None,             # 无 schedule → 不在产
        "gamma": "",              # 空 schedule → 不在产
        "delta": "30 5 * * 1",    # 在产
    }
    for slug, schedule in spec.items():
        d = repo / "spiders" / slug
        d.mkdir(parents=True)
        doc = {"version": "1", "name": slug,
               "functions": [{"command": f"run_{slug}", "frequency": "daily"}]}
        if schedule is not None:
            doc["schedule"] = schedule
        (d / "manifest.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return repo


# 报告文件名里的日期与实现同源（UTC 当天），避免跨午夜漂移
TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")


def _ok(source: str, rows: int = 5) -> dict:
    return {"source": source, "status": "success", "exit_code": 0,
            "rows": rows, "seconds": 1.5, "error": ""}


# ── 名单解析 ───────────────────────────────────────────────────────────────

def test_discover_scheduled_sources(tmp_path):
    repo = _mk_repo(tmp_path)
    assert discover_scheduled_sources(repo) == ["alpha", "delta"]  # 排序、剔除无/空 schedule


def test_discover_missing_spiders_dir(tmp_path):
    assert discover_scheduled_sources(tmp_path / "nope") == []


def test_only_filter(tmp_path, capsys):
    repo = _mk_repo(tmp_path)
    rc = main(["--repo", str(repo), "--dry-run", "--only", "delta,alpha"],
              run_source=lambda s, limit: _ok(s))
    out = capsys.readouterr().out
    assert rc == 0
    assert "scheduled sources (2)" in out
    assert "  alpha" in out and "  delta" in out
    assert "beta" not in out and "gamma" not in out


def test_only_unknown_source_errors(tmp_path, capsys):
    repo = _mk_repo(tmp_path)
    rc = main(["--repo", str(repo), "--dry-run", "--only", "alpha,ghost"],
              run_source=lambda s, limit: _ok(s))
    err = capsys.readouterr().err
    assert rc == 2
    assert "ghost" in err


# ── dry-run ────────────────────────────────────────────────────────────────

def test_dry_run_lists_without_report(tmp_path, capsys):
    repo = _mk_repo(tmp_path)
    called = []

    def fake_runner(src, limit):
        called.append(src)
        return _ok(src)

    rc = main(["--repo", str(repo), "--dry-run"], run_source=fake_runner)
    out = capsys.readouterr().out
    assert rc == 0
    assert called == []                                  # 不执行
    assert not (repo / "reports" / "fleet-baseline").exists()  # 不写报告
    assert "would run 2 source(s)" in out


# ── 报告生成（假 runner） ──────────────────────────────────────────────────

def test_report_generation_with_fake_runner(tmp_path, capsys):
    repo = _mk_repo(tmp_path)

    def fake_runner(src, limit):
        assert limit == 20                               # 默认 limit 传入 runner
        if src == "alpha":
            return _ok(src, rows=42)
        return {"source": src, "status": "failed", "exit_code": 3,
                "rows": 0, "seconds": 2.0, "error": "HTTPError 503"}

    rc = main(["--repo", str(repo), "--limit", "20"], run_source=fake_runner)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[1/2] alpha -> success rows=42" in out
    assert "[2/2] delta -> failed rows=0" in out

    jpath = repo / "reports" / "fleet-baseline" / f"{TODAY}-baseline.json"
    mpath = repo / "reports" / "fleet-baseline" / f"{TODAY}-baseline.md"
    assert jpath.is_file() and mpath.is_file()
    doc = json.loads(jpath.read_text(encoding="utf-8"))
    assert doc["date"] == TODAY
    assert doc["summary"]["total"] == 2
    assert doc["summary"]["success"] == 1
    assert doc["summary"]["failed"] == 1
    assert doc["summary"]["success_rate"] == 0.5
    assert doc["summary"]["limit"] == 20
    assert [s["source"] for s in doc["sources"]] == ["alpha", "delta"]
    assert doc["sources"][0]["rows"] == 42

    md = mpath.read_text(encoding="utf-8")
    assert f"# 舰队基线 {TODAY}" in md
    assert "成功率: 0.5" in md
    assert "| alpha | success | 42 |" in md
    assert "HTTPError 503" in md


def test_report_overwrites_same_day(tmp_path):
    repo = _mk_repo(tmp_path)
    main(["--repo", str(repo)], run_source=lambda s, limit: _ok(s))
    main(["--repo", str(repo)], run_source=lambda s, limit: _ok(s))
    files = sorted(p.name for p in
                   (repo / "reports" / "fleet-baseline").glob(f"{TODAY}-*"))
    assert files == [f"{TODAY}-baseline.json", f"{TODAY}-baseline.md"]


def test_bad_stub_result_recorded_as_failed(tmp_path, capsys):
    repo = _mk_repo(tmp_path)
    rc = main(["--repo", str(repo), "--only", "alpha"],
              run_source=lambda s, limit: {"nonsense": True})   # 非法桩返回
    assert rc == 0
    doc = json.loads((repo / "reports" / "fleet-baseline"
                      / f"{TODAY}-baseline.json").read_text(encoding="utf-8"))
    assert doc["sources"][0]["status"] == "failed"
    assert "invalid runner stub result" in doc["sources"][0]["error"]


# ── runner 输出解析与退出码映射 ────────────────────────────────────────────

def test_parse_runner_output_line():
    stdout = ("some noise\n"
              "fd-runner: metal-com -> success rows=123 in 4.5s\n")
    assert parse_runner_output(stdout) == ("success", 123, 4.5)


def test_parse_runner_output_garbage():
    assert parse_runner_output("nothing here") == (None, None, None)


def test_classify_exit_codes():
    assert classify_exit(2) == "missing"
    assert classify_exit(4) == "no-entry"
    assert classify_exit(3) is None
    assert classify_exit(0) is None
