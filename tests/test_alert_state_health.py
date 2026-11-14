"""`state_health`：把「去重与回落通知到底生不生效」变成一条会自己说话的读数。

2026-11-14 查出：线上四条告警线的 `/reports` 是 `emptyDir`，每轮 `load_state()` 都拿到
`{}` ⇒ `state.py` 的去重分支与 RESOLVED 分支**永不生效**，一条持续越阈的规则每轮重发一封、
回落永不通知。当日已裁定**不挂 PVC**（理由见 `openspec/changes/ciyuan-consumer-surface`
任务 3.7：告警链路挂节点本地卷，那台机一挂这四条线自己就 Pending），留下的增量就是本读数。

这里钉三件事：
1. 没有历史状态时 `persisted` 为 False，且 note 说明后果（不是一句「正常」）；
2. **必须在 `save_state()` 之前取**——否则本轮刚写的那份会冒充「历史状态」，读数永远为 true，
   等于把这个坑从「看得见」变成「看不见」；
3. 目录不可写要单独一档：那是另一种故障（只读挂载），note 不能与「首轮」混为一谈。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.alerts_core import load_state, save_state, state_health  # noqa: E402


def test_no_history_reports_not_persisted_with_consequence(tmp_path):
    info = state_health(tmp_path / "state" / "alert-state.json")
    assert info["persisted"] is False
    assert info["rules"] == 0
    assert info["dir_writable"] is True
    assert "不生效" in info["note"], "不能只报 false 不说后果"
    assert "emptyDir" in info["note"], "要点名典型成因，否则读者还得去翻清单"


def test_second_round_sees_history_and_says_it_works(tmp_path):
    p = tmp_path / "state" / "alert-state.json"
    save_state(p, {"k8s-node-not-ready:cheap-5": {"state": "firing", "episode": 1}})
    info = state_health(p)
    assert info["persisted"] is True
    assert info["rules"] == 1
    assert "生效" in info["note"] and "不生效" not in info["note"]


def test_state_written_this_round_must_not_count_as_history(tmp_path):
    """调用方必须在 save 之前取读数；这条用例钉的是读数本身的语义边界。

    若在 `save_state()` 之后取，`persisted` 会是 True —— 那正是「自己刚写完就当成历史」，
    读数变成一句无论实际如何都为真的空话。
    """
    p = tmp_path / "state" / "alert-state.json"
    before = state_health(p)
    save_state(p, {"r": {"state": "firing"}})
    after = state_health(p)
    assert before["persisted"] is False
    assert after["persisted"] is True, "写完之后文件确实在了"
    assert before != after, "两处取数的结论必须不同，调用方才有理由在意顺序"


def test_unwritable_dir_is_its_own_verdict(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    p = d / "alert-state.json"
    os.chmod(d, 0o500)          # r-x：不可写
    try:
        info = state_health(p)
    finally:
        os.chmod(d, 0o700)      # 还原，否则 tmp_path 清理失败
    assert info["dir_writable"] is False
    assert info["persisted"] is False
    assert "不可写" in info["note"], "只读挂载要与「首轮」分开报——修法完全不同"


def test_corrupt_state_counts_as_no_history(tmp_path):
    p = tmp_path / "alert-state.json"
    p.write_text("{not json", encoding="utf-8")
    info = state_health(p)
    assert info["persisted"] is False
    assert "不生效" in info["note"]


@pytest.mark.parametrize("script", ["node_health_alerts", "ciyuan_line_alerts",
                                    "plaza_freshness_alerts"])
def test_scripts_take_the_reading_before_saving(script):
    """三处调用点都必须「先取读数、后 save_state」，顺序写反读数就永远是 true。"""
    src = (REPO / "scripts" / f"{script}.py").read_text(encoding="utf-8")
    assert "state_info = state_health(STATE)" in src, f"{script} 没接这条读数"
    assert '"state": state_info' in src, f"{script} 没把读数打进摘要"
    take = src.index("state_info = state_health(STATE)")
    # 该脚本里最后一次 save_state 必须在取读数之后
    assert src.rindex("save_state(STATE") > take, (
        f"{script}: 取读数早于 save_state 才行；现在顺序反了，读数会永远为 true"
    )


def test_reading_tracks_the_behaviour_it_describes(tmp_path):
    """读数的可信性证据：`persisted: false` 的那轮真的重发，`true` 的那轮真的被去重。

    只断言字符串等于没验——这条把「读数」与「它声称的那种行为」放在同一个实验里：
    同一个越阈节点连跑两轮，第一轮（新目录，等同 emptyDir）firing=1，
    第二轮（状态已在）firing=0。若哪天去重逻辑或读数任一坏了，这对照就会歪。
    """
    import json
    import subprocess

    nodes = {"items": [{"metadata": {"name": "cheap-5"},
                        "status": {"conditions": [{"type": "Ready", "status": "Unknown"}]}}]}
    nj = tmp_path / "nodes.json"
    nj.write_text(json.dumps(nodes), encoding="utf-8")
    env = {**os.environ, "FD_NODE_ALERT_REPORTS": str(tmp_path / "reports")}

    def run() -> dict:
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "node_health_alerts.py"),
             "--nodes-json", str(nj), "--run"],
            capture_output=True, text=True, env=env, check=False,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])

    first, second = run(), run()

    assert first["state"]["persisted"] is False and first["firing"] == 1
    assert second["state"]["persisted"] is True, "第二轮必须认出上一轮的状态"
    assert second["firing"] == 0 and second["messages"] == 0, (
        "读数说「生效」时行为必须真的去重；否则这条读数在替错实现背书"
    )
