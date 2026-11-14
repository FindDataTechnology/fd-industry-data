"""平台能力端点告警的语义（2026-11-14）。

这组测钉四件会静默出错或误判绿的事，每条都对应一次真实误读：

1. **404 才叫「能力不在片里」**。401/403/405/400 都在证明路由存在（前三个是门控与方法，
   400 是坏请求——但路由接到了）。把「没权限」当成「不存在」，会让人去查错方向。
2. **503 是设计内的惰性**（runner-fleet 门未配令牌时按设计拒绝），不许判红；
   但它必须作为事实打印出来，不能静默吞掉。
3. **全部连不上 ≠ 健康**（insufficient_data，exit 4）；**部分连得上**照实判其余检查点。
4. **「消失」与「异常」分开报**：前者多半是滚错片，后者是服务侧故障，混成一条会指错方向。
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.platform_capability_alerts import (  # noqa: PLC2701 — 脚本与测同仓
    CAPABILITY_CHECKS, EXIT_BLIND, EXIT_FIRING, RULE_ERROR, RULE_GONE,
    assess, compose_messages, main,
)


def _checks(**status_by_name) -> list[dict]:
    """按 CAPABILITY_CHECKS 造一批带状态的检查点（默认全 401＝受门控但存在）。"""
    out = []
    for spec in CAPABILITY_CHECKS:
        short = spec["name"].split("（")[0]
        out.append({**spec, "status": status_by_name.get(short, 401), "error": ""})
    return out


def test_404_means_gone_and_401_does_not():
    v = assess(_checks(**{"会议录音按人派发": 404}))
    assert [g["name"] for g in v["gone"]] == ["会议录音按人派发（add-meeting-capture）"]
    # 401 的其余检查点不得被算成 gone 或 error
    assert v["error"] == []
    assert all(g["status"] == 404 for g in v["gone"])


def test_410_also_means_gone():
    v = assess(_checks(**{"会议发现面": 410}))
    assert [g["name"] for g in v["gone"]] == ["会议发现面（add-meeting-discovery）"]


def test_405_and_400_are_not_gone():
    # 405＝方法不对但路由在（/api/teams/directory 就是 GET-only，POST 回 405）；
    # 400＝坏请求但路由接到了。两者都证明路由存在。
    v = assess(_checks(**{"跨用户团队目录": 405, "语音采集入房": 400}))
    assert v["gone"] == []
    assert v["error"] == []


def test_503_on_runner_fleet_is_advisory_not_firing():
    v = assess(_checks(**{"runner 舰队桥门": 503}))
    assert v["gone"] == [] and v["error"] == []
    assert [a["status"] for a in v["advisory"]] == [503]


def test_5xx_elsewhere_is_error_not_gone():
    v = assess(_checks(**{"会议录音按人派发": 502}))
    assert v["gone"] == []
    assert [e["name"] for e in v["error"]] == ["会议录音按人派发（add-meeting-capture）"]


def test_all_unreachable_is_blind_but_partial_is_judged():
    all_zero = assess(_checks(**{s["name"].split("（")[0]: 0 for s in CAPABILITY_CHECKS}))
    assert all_zero["blind_all"] is True

    partial = assess(_checks(**{"会议录音按人派发": 0, "会议发现面": 404}))
    assert partial["blind_all"] is False, "还有一个能答上来，就不能整体记「判不出」"
    assert [g["name"] for g in partial["gone"]] == ["会议发现面（add-meeting-discovery）"]


def test_gone_message_names_the_capability_and_the_next_steps():
    v = assess(_checks(**{"会议录音按人派发": 404}))
    msgs = compose_messages([{"rule_id": RULE_GONE, "kind": "firing"}], v)
    assert len(msgs) == 1
    body = msgs[0]["body"]
    assert "add-meeting-capture" in body or "会议录音" in body
    # 排查动作要能被照做：BUILD-INFO → 祖先计数 → 闸 → 回滚
    for token in ("BUILD-INFO", "rev-list --count", "fd-image-coherence-gate", "image 行"):
        assert token in body, f"缺排查动作：{token}"
    assert "消失" in msgs[0]["subject"]


def test_error_message_is_separate_from_gone():
    v = assess(_checks(**{"会议录音按人派发": 502}))
    msgs = compose_messages([{"rule_id": RULE_ERROR, "kind": "firing"}], v)
    assert "异常" in msgs[0]["subject"]
    assert "消失" not in msgs[0]["subject"]


# ── main()：退出码就是这条线的可见性 ────────────────────────────────────────

def _cfg(tmp_path: Path) -> Path:
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "delivery": {"channel": "smtp", "to": ["a@example.com"]},
        "targets": {"base": "http://127.0.0.1:9"},
    }), encoding="utf-8")
    return p


def _snap(tmp_path: Path, **status_by_name) -> Path:
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({
        "checks": [{**spec, "status": status_by_name.get(spec["name"].split("（")[0], 401)}
                   for spec in CAPABILITY_CHECKS]
    }), encoding="utf-8")
    return p


def test_main_firing_when_a_capability_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_CAPABILITY_ALERT_REPORTS", str(tmp_path / "reports"))
    snap = _snap(tmp_path, **{"会议录音按人派发": 404})
    assert main(["--config", str(_cfg(tmp_path)), "--from-json", str(snap),
                 "--dry-run"]) == EXIT_FIRING


def test_main_green_when_everything_is_merely_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_CAPABILITY_ALERT_REPORTS", str(tmp_path / "reports"))
    snap = _snap(tmp_path)  # 全 401
    assert main(["--config", str(_cfg(tmp_path)), "--from-json", str(snap),
                 "--dry-run"]) == 0


def test_main_blind_when_nothing_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_CAPABILITY_ALERT_REPORTS", str(tmp_path / "reports"))
    snap = _snap(tmp_path, **{s["name"].split("（")[0]: 0 for s in CAPABILITY_CHECKS})
    assert main(["--config", str(_cfg(tmp_path)), "--from-json", str(snap),
                 "--dry-run"]) == EXIT_BLIND


def test_main_exits_3_when_delivery_declaration_missing(tmp_path):
    snap = _snap(tmp_path)
    assert main(["--config", str(tmp_path / "nope.json"), "--from-json", str(snap),
                 "--dry-run"]) == 3
