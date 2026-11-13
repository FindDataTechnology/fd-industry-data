"""广场机器面新鲜度告警的语义（2026-11-14）。

这组测钉四件会静默出错或误判绿的事，每条都对应一个真实会发生的误读：

1. **美化 JSON 与压缩 JSON 都要认**。`catalog.json` 线上是 `"generatedAt": "…"`
   （冒号后有空格），而压缩副本是 `"generatedAt":"…"`。2026-11-14 主机自检那条
   正则只写死后一种，于是日志永远打印空值、声称的校验从未生效——同形错误在这里
   必须先被用例钉住。
2. **抽不到日期 ≠ 新鲜**。判不出就该是 insufficient_data（exit 4），
   绝不能因为"没读到"就顺手当成"今天刚更新过"。
3. **陈旧必须 firing、回落必须 resolved**（只报坏消息的线等于半条线）。
4. **站点面不可达与陈旧是两个故障**，混成一条会让排查方向指错。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts.plaza_freshness_alerts import (  # noqa: PLC2701 — 脚本与测同仓
    EXIT_BLIND, EXIT_FIRING, RULE_ROOT_DOWN, RULE_STALE, age_hours,
    assess, compose_messages, main, parse_generated_at,
)

NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
ROOTS_OK = {p: 200 for p in ("/llms.txt", "/llms-full.txt", "/catalog.json", "/robots.txt")}

PRETTY = '{\n  "schema": "x",\n  "generatedAt": "2026-11-14",\n  "count": 735\n}\n'
MINIFIED = '{"schema":"x","generatedAt":"2026-11-14","count":735}'


@pytest.mark.parametrize("text", [PRETTY, MINIFIED], ids=["pretty", "minified"])
def test_parse_generated_at_accepts_both_json_styles(text):
    # 同一条正则必须同时认「冒号后有空格」与「无空格」——2026-11-14 踩过一次。
    assert parse_generated_at(text) == date(2026, 11, 14)


@pytest.mark.parametrize("text", ["", "{}", '{"generatedAt": ""}', '{"generatedAt": "not-a-date"}',
                                  '{"generatedAt": "2026-13-45"}'])
def test_parse_generated_at_returns_none_instead_of_guessing(text):
    assert parse_generated_at(text) is None


def test_age_hours_counts_from_utc_midnight_of_the_snapshot_day():
    # generatedAt 是**导出那天的 UTC 日**，年龄从该日 00:00Z 起算，不是从"那一刻"。
    assert age_hours(date(2026, 11, 14), NOW) == pytest.approx(8.0)
    assert age_hours(date(2026, 11, 13), NOW) == pytest.approx(32.0)


def test_fresh_snapshot_is_not_stale():
    v = assess(catalog_status=200, catalog_text=MINIFIED, roots=ROOTS_OK,
               now=NOW, stale_hours=36)
    assert v["blind"] is False
    assert v["stale"] is False
    assert v["root_down"] == []
    assert v["age_hours"] == pytest.approx(8.0)


def test_old_snapshot_is_stale_at_the_threshold_boundary():
    # 36h 阈值：31h 不响、37h 响。边界写清楚，免得后人以为差一小时无所谓。
    old_31 = PRETTY.replace("2026-11-14", "2026-11-13")   # 32h
    old_37 = PRETTY.replace("2026-11-14", "2026-11-12")   # 56h
    assert assess(catalog_status=200, catalog_text=old_31, roots=ROOTS_OK,
                  now=NOW, stale_hours=36)["stale"] is False
    assert assess(catalog_status=200, catalog_text=old_37, roots=ROOTS_OK,
                  now=NOW, stale_hours=36)["stale"] is True


def test_unreachable_catalog_is_blind_not_fresh():
    v = assess(catalog_status=0, catalog_text="", roots={p: 0 for p in ROOTS_OK},
               now=NOW, stale_hours=36)
    assert v["blind"] is True
    assert v["stale"] is None, "判不出就不能给一个布尔值，否则会冒充『新鲜』"
    assert "取不到" in v["reason"]


def test_unparsable_generated_at_is_blind_not_fresh():
    v = assess(catalog_status=200, catalog_text='{"schema":"x"}', roots=ROOTS_OK,
               now=NOW, stale_hours=36)
    assert v["blind"] is True
    assert "读不出 generatedAt" in v["reason"]


def test_root_face_down_is_a_separate_verdict_from_staleness():
    roots = dict(ROOTS_OK)
    roots["/llms.txt"] = 503
    v = assess(catalog_status=200, catalog_text=MINIFIED, roots=roots,
               now=NOW, stale_hours=36)
    assert v["stale"] is False
    assert v["root_down"] == ["/llms.txt"]


def test_messages_name_the_next_step_for_a_stale_plaza():
    v = assess(catalog_status=200, catalog_text=PRETTY.replace("2026-11-14", "2026-11-12"),
               roots=ROOTS_OK, now=NOW, stale_hours=36)
    msgs = compose_messages([{"rule_id": RULE_STALE, "kind": "firing"}], v, 36)
    assert len(msgs) == 1
    body = msgs[0]["body"]
    assert "plaza-refresh.log" in body, "要给出第一个排查动作"
    assert "check_plaza_export_copy" in body, "主机副本漂移是这条线最常见的成因"
    assert "ssh root@cheap1" in msgs[0]["subject"] or "广场" in msgs[0]["subject"]


def test_messages_for_root_down_do_not_talk_about_staleness():
    v = assess(catalog_status=200, catalog_text=MINIFIED,
               roots={**ROOTS_OK, "/robots.txt": 404}, now=NOW, stale_hours=36)
    msgs = compose_messages([{"rule_id": RULE_ROOT_DOWN, "kind": "firing"}], v, 36)
    assert "不可达" in msgs[0]["subject"] or "不可达" in msgs[0]["body"]
    assert "/robots.txt" in msgs[0]["body"]


# ── main()：退出码就是这条线的可见性 ────────────────────────────────────────

def _cfg(tmp_path: Path, stale_hours: float = 36) -> Path:
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "delivery": {"channel": "smtp", "to": ["a@example.com"]},
        "targets": {"base": "http://127.0.0.1:9",
                    "root_faces": ["/llms.txt", "/llms-full.txt", "/catalog.json", "/robots.txt"]},
        "thresholds": {"stale_hours": stale_hours},
    }), encoding="utf-8")
    return p


def _snap(tmp_path: Path, *, catalog_text: str = MINIFIED, catalog_status: int = 200,
          roots: dict | None = None) -> Path:
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"catalog_status": catalog_status, "catalog_text": catalog_text,
                             "roots": roots or ROOTS_OK}), encoding="utf-8")
    return p


def test_main_firing_on_stale_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_PLAZA_ALERT_REPORTS", str(tmp_path / "reports"))
    cfg = _cfg(tmp_path)
    snap = _snap(tmp_path, catalog_text=PRETTY.replace("2026-11-14", "2026-11-10"))
    assert main(["--config", str(cfg), "--from-json", str(snap), "--dry-run"]) == EXIT_FIRING


def test_main_blind_when_catalog_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_PLAZA_ALERT_REPORTS", str(tmp_path / "reports"))
    cfg = _cfg(tmp_path)
    snap = _snap(tmp_path, catalog_status=0, catalog_text="",
                 roots={p: 0 for p in ROOTS_OK})
    assert main(["--config", str(cfg), "--from-json", str(snap), "--dry-run"]) == EXIT_BLIND


def test_main_green_on_fresh_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_PLAZA_ALERT_REPORTS", str(tmp_path / "reports"))
    cfg = _cfg(tmp_path)
    snap = _snap(tmp_path, catalog_text=MINIFIED)  # 2026-11-14，距 NOW=8h
    assert main(["--config", str(cfg), "--from-json", str(snap), "--dry-run"]) == 0


def test_main_exits_3_when_delivery_declaration_missing(tmp_path):
    missing = tmp_path / "nope.json"
    snap = _snap(tmp_path)
    assert main(["--config", str(missing), "--from-json", str(snap), "--dry-run"]) == 3
