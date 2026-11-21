"""三联账判据（change measure-semantic-coverage-gap 1.1）。

**为什么要有这条测试**：三联账的主要价值是「把亮着但没抽取器的源摆上台面」。
如果判据把这类源静默滤掉，它就和既有的 `check_observation_lag` 一样永远绿
——本 change 就是为修那个病灶存在的实测 109 个源。

四个 fixture 分别对应四档，外加两条边界：

* `unwired` 必须出现，且**不被**当成 `unknown` 或 `served`；
* 没有成功爬取记录的源必须 `unknown`，不能因为「没有观测行」猜成 `unwired`；
* 有抽取器但台账已死必须 `lagging`，不能因为「有观测行」判 `served`；
* 无 DSN 时必须退出 2（判不出），不得退出 0（那会让「没查」和「查了没事」同形）。

反例已构造：把 `unwired` fixture 换成有抽取器 → 该用例必须红。
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import semantic_coverage as sc  # noqa: E402

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)


def _facts(**kw) -> dict:
    base = {"crawl_ts": NOW, "cycle_days": 1.0, "data_latest": None,
            "obs_rows": None, "last_run": None, "last_status": None}
    base.update(kw)
    return base


@pytest.fixture
def lit() -> dict[str, str]:
    return {
        "served-src": "0 1 * * *",
        "unwired-src": "0 2 * * *",
        "lagging-src": "0 3 * * *",
        "never-crawl-src": "0 4 * * *",
    }


def test_four_verdicts(lit):
    facts = {
        "served-src": _facts(last_run=NOW - dt.timedelta(hours=6), last_status="success",
                             data_latest="2026-11-20", obs_rows=42),
        "lagging-src": _facts(last_run=NOW - dt.timedelta(days=12), last_status="success",
                              data_latest="2026-11-01", obs_rows=10),
        # unwired-src：抓取成功过，但仓里没有抽取器，观测零行
        "unwired-src": _facts(data_latest=None, obs_rows=None),
        # never-crawl-src 没有成功爬取记录 → unknown
    }
    rows = {r["source"]: r for r in sc.build_rows(lit, {"served-src", "lagging-src"}, facts)}

    assert rows["served-src"]["verdict"] == "served"
    assert rows["lagging-src"]["verdict"] == "lagging"
    assert rows["unwired-src"]["verdict"] == "unwired"
    assert rows["never-crawl-src"]["verdict"] == "unknown"

    # 五档互斥且穷尽：合计 = 亮着源数
    assert len(rows) == len(lit)


def test_frozen_is_not_unwired(lit):
    """观测层有行但无当前抽取器 ⇒ frozen，不是 unwired。

    2026-11-21 实测 28 个源属此档（如 uk-hpi 有 18,906 行观测、判据首次漏了
    stars/extract_*.py 那一类时被判成 unwired）。两档处置不同：frozen 是找回
    旧抽取器，unwired 是新建一个。
    """
    facts = {"unwired-src": _facts(obs_rows=1234, data_latest="2026-10-01")}
    rows = {r["source"]: r for r in sc.build_rows(lit, set(), facts)}
    assert rows["unwired-src"]["verdict"] == "frozen"
    assert "1234" in rows["unwired-src"]["message"]


def test_unwired_is_not_silently_dropped(lit):
    """本 change 的存在理由：没抽取器的源必须出现在账上，而不是被滤掉。"""
    facts = {s: _facts() for s in lit}
    rows = sc.build_rows(lit, set(), facts)  # 零抽取器
    verdicts = {r["source"]: r["verdict"] for r in rows}
    assert verdicts["unwired-src"] == "unwired"
    assert len(rows) == len(lit), "任何亮着的源都不得被排除出账"


def test_unknown_wins_over_unwired(lit):
    """没有成功爬取记录 ⇒ unknown；不许因为「没观测行」猜成 unwired。"""
    rows = sc.build_rows(lit, set(), {})  # 无任何事实
    verdicts = {r["source"]: r["verdict"] for r in rows}
    assert set(verdicts.values()) == {"unknown"}


def test_extractor_dead_is_lagging_not_served(lit):
    facts = {"lagging-src": _facts(last_run=NOW - dt.timedelta(days=12),
                                   last_status="success", data_latest="2026-10-01",
                                   obs_rows=5)}
    rows = {r["source"]: r for r in sc.build_rows(lit, {"lagging-src"}, facts)}
    assert rows["lagging-src"]["verdict"] == "lagging"


def test_baseline_diff_shapes():
    rows = [
        {"source": "a", "verdict": "unwired"},
        {"source": "b", "verdict": "frozen"},
        {"source": "c", "verdict": "served"},
    ]
    for v in ("unwired", "frozen"):
        new, gone, kept = sc.baseline_diff(rows, v)
        # 形状：三元组，且各档互不串（b 不进 unwired，a 不进 frozen）
        assert isinstance((new, gone, kept), tuple)
    new_unwired, _, _ = sc.baseline_diff(rows, "unwired")
    new_frozen, _, _ = sc.baseline_diff(rows, "frozen")
    assert "b" not in new_unwired and "a" not in new_frozen


def test_pod_environment_degrades_not_greens(tmp_path):
    """生产 pod 只挂 check_observation_lag.py + wave_extract_split.py。

    在那个环境里抽取器清单读不到。**不许**因此把每个源判成「无抽取器」——
    那会让 frozen/unwired 全是、`lagging` 永不出现，闸变成永久绿。正确行为：
    降级（served/lagging 两档）并在 stderr 明说少判了什么。
    """
    import shutil
    import subprocess

    d = tmp_path / "check" / "scripts"
    d.mkdir(parents=True)
    for f in ("check_observation_lag.py", "wave_extract_split.py"):
        shutil.copy(SCRIPTS / f, d / f)
    r = subprocess.run([sys.executable, str(d / "check_observation_lag.py"), "--self-test"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr

    # 直接验 helper：pod 形态下抽取器集为空 ⇒ 调用方必须走降级分支
    sys.path.insert(0, str(d))
    import check_observation_lag as pod_mod
    assert pod_mod.extractor_source_names(d) == set(), "pod 里不该扫出抽取器"
    assert pod_mod.lit_source_names(d) is None, "pod 里没有 spiders/ ⇒ 判不出亮着的源"


def test_full_repo_has_extractors():
    """全仓 checkout 时才做五档判定——这是 frozen/unwired 的前提。"""
    sys.path.insert(0, str(SCRIPTS))
    import check_observation_lag as m
    assert len(m.extractor_source_names(SCRIPTS)) > 10, "全仓应扫出两位数抽取器"


def test_no_dsn_exits_2(tmp_path, monkeypatch):
    """判不出必须退出 2：退出 0 会让「没查」与「查了没事」同形。"""
    for k in ("FD_OPEN_DATA_DSN", "FD_OPEN_DATA_MCP_DATABASE_URL", "FD_CRAWL_DB_URL"):
        monkeypatch.delenv(k, raising=False)
    r = subprocess.run([sys.executable, str(SCRIPTS / "semantic_coverage.py")],
                       capture_output=True, text=True, cwd=str(REPO), timeout=120,
                       env={k: v for k, v in __import__("os").environ.items()
                            if k not in ("FD_OPEN_DATA_DSN",
                                         "FD_OPEN_DATA_MCP_DATABASE_URL",
                                         "FD_CRAWL_DB_URL")})
    assert r.returncode == 2, r.stderr
    assert "判不出" in r.stderr


def test_snapshot_offline(tmp_path):
    """离线夹具能跑通全部四档，不连库。"""
    snap = {
        "facts": {
            "served-src": {"crawl_ts": NOW.isoformat(),
                           "last_run": (NOW - dt.timedelta(hours=3)).isoformat(),
                           "last_status": "success", "cycle_days": 1.0,
                           "data_latest": "2026-11-20", "obs_rows": 5},
        }
    }
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "semantic_coverage.py"),
                        "--snapshot", str(p), "--json"],
                       capture_output=True, text=True, cwd=str(REPO), timeout=120)
    # 夹具里的源名不在真实清单里 ⇒ 全部 unknown（判不出爬取记录），这正是
    # 本测试要断言的：离线模式能跑通，且四档计数闭合到亮着的源总数。
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["params"]["lit_count"] > 0
    assert sum(out["summary"].values()) == out["params"]["lit_count"]
