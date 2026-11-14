"""词元按线花费告警的语义（2026-11-13，ciyuan-consumer-surface 3.1/3.2/3.3）。

这组测钉五条会静默出错或误判绿的语义，每条都对应一个真实会发生的误读：

1. **「无上限」必须每次都说出来**：2026-11-13 实测 16 个分组、16 个三档额度全为
   NULL。只报花费不报这一条，读者会把"没有告警"当成"额度在管着"。
2. **超过阈值必须 firing，回落必须 resolved**：只报涨不报落，邮箱里就只有坏消息。
3. **没配阈值 ≠ 通过**：配置文件缺失/不可读时以 exit 3 暴露，不是安静地当成没事。
4. **读不到数据 ≠ 正常**：以 exit 4 暴露"监控失明"。
5. **线名拼错的阈值要点名**：拼错等于那条线永远用默认阈值，而没有任何东西会红。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ciyuan_line_alerts import (  # noqa: PLC2701 — 脚本与测同仓
    RULE_ID, compose_messages, evaluate, line_daily_totals, thresholds_of,
    unattributed_share,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _doc(*, lines=None, groups=None, window_days=7) -> dict:
    """一份最小可用数据；lines = [(line, day, requests, usd)]。"""
    rows = [
        {"line": line, "raw_prefix_if_unknown": "", "day": day,
         "requests": req, "usd": usd}
        for line, day, req, usd in (lines or [])
    ]
    return {
        "generated_at": "2026-11-13T12:00:00Z",
        "window_days": window_days,
        "per_line_day": rows,
        "groups": groups or [],
    }


def _group(name, *, unset=True, today=0.0):
    return {
        "group": name, "subscription_type": "standard",
        "daily_limit_usd": None, "weekly_limit_usd": None, "monthly_limit_usd": None,
        "limits_unset": unset, "today_usd": today,
    }


# ── 1. 无上限必须被说出来 ────────────────────────────────────────────────────

def test_unlimited_groups_are_reported_not_silently_passed():
    doc = _doc(lines=[("萬星 wanxing", "2026-11-13", 100, 1.5)],
               groups=[_group("core"), _group("image-video")])
    # evaluate 不吃 groups 的判定，但 compose_messages 必须把它们带进正文
    _, events, detail = evaluate(doc, {}, NOW, default_usd=50, per_line={})
    assert events == [], "阈值以下不产生事件"

    msgs = compose_messages(
        [{"rule_id": RULE_ID, "kind": "firing", "key": f"{RULE_ID}:萬星 wanxing",
          "at": "2026-11-13T12:00:00Z"}], detail, doc)
    body = json.loads(msgs[0]["body"])
    assert body["unlimited_groups"] == 2
    assert body["groups_total"] == 2
    assert "无上限" in body["no_limit_note"], "邮件正文必须写明这组没有额度拦着"


def test_group_with_real_limits_is_not_called_unlimited():
    limited = {"group": "core", "subscription_type": "subscription",
               "daily_limit_usd": 10.0, "weekly_limit_usd": None, "monthly_limit_usd": None,
               "limits_unset": False, "today_usd": 9.23}
    doc = _doc(lines=[("萬星 wanxing", "2026-11-13", 10, 1.0)], groups=[limited])
    msgs = compose_messages(
        [{"rule_id": RULE_ID, "kind": "firing", "key": f"{RULE_ID}:萬星 wanxing",
          "at": "2026-11-13T12:00:00Z"}],
        evaluate(doc, {}, NOW, default_usd=50, per_line={})[2], doc)
    body = json.loads(msgs[0]["body"])
    assert body["unlimited_groups"] == 0
    assert "无上限" not in body["no_limit_note"]


# ── 2. 超阈 firing / 回落 resolved ──────────────────────────────────────────

def test_over_threshold_fires_and_drop_resolves():
    over = _doc(lines=[("萬星 wanxing", "2026-11-13", 9000, 99.0)])
    states, events, _ = evaluate(over, {}, NOW, default_usd=50, per_line={})
    assert [e["kind"] for e in events] == ["firing"]
    assert states[f"{RULE_ID}:萬星 wanxing"].state == "firing"

    back = _doc(lines=[("萬星 wanxing", "2026-11-14", 100, 2.0)])
    _, events2, _ = evaluate(back, {k: v.to_dict() for k, v in states.items()}, NOW,
                             default_usd=50, per_line={})
    assert [e["kind"] for e in events2] == ["resolved"]
    assert "回落" in compose_messages(events2, [], back)[0]["subject"]


def test_per_line_threshold_overrides_default():
    doc = _doc(lines=[("识律 lex", "2026-11-13", 100, 60.0)])
    _, quiet, _ = evaluate(doc, {}, NOW, default_usd=50, per_line={"识律 lex": 80})
    assert quiet == [], "按线阈值更高时不该响"
    _, loud, _ = evaluate(doc, {}, NOW, default_usd=50, per_line={"识律 lex": 30})
    assert [e["kind"] for e in loud] == ["firing"]


def test_alert_uses_latest_day_not_window_total():
    """缓慢涨价时窗口合计几乎不动，看不见"今天突然翻倍"。判定必须看最近一天。"""
    doc = _doc(lines=[
        ("萬星 wanxing", "2026-11-12", 100, 1.0),
        ("萬星 wanxing", "2026-11-13", 100, 400.0),
    ])
    totals = line_daily_totals(doc)
    assert totals["萬星 wanxing"]["latest_day"] == "2026-11-13"
    assert totals["萬星 wanxing"]["today_usd"] == 400.0
    assert totals["萬星 wanxing"]["window_usd"] == pytest.approx(401.0)
    _, events, _ = evaluate(doc, {}, NOW, default_usd=50, per_line={})
    assert [e["kind"] for e in events] == ["firing"]


def test_small_today_with_large_window_stays_quiet():
    """判据是「当日」而不是「窗口合计」——这条用例专门用来区分两者。

    窗口里前一天烧了 400、今天只有 1：按当日判必须安静（line 已经回落），
    按窗口合计判会一直响到窗口滑出为止。**首版没有这条用例，于是把判定改成
    window_usd 后 10 个测试全绿**——注入才发现判据没被钉住。
    """
    doc = _doc(lines=[
        ("萬星 wanxing", "2026-11-12", 9000, 400.0),
        ("萬星 wanxing", "2026-11-13", 10, 1.0),
    ])
    totals = line_daily_totals(doc)
    assert totals["萬星 wanxing"]["window_usd"] == pytest.approx(401.0)
    assert totals["萬星 wanxing"]["today_usd"] == 1.0
    _, events, _ = evaluate(doc, {}, NOW, default_usd=50, per_line={})
    assert events == [], "今天已经回落，窗口里那 400 不该让它继续响"


# ── 3. 阈值配置的形状 ────────────────────────────────────────────────────────

def test_threshold_annotations_are_not_values():
    """``_why_lines`` 这类注记不能被当成数值解析（首版就栽在这）。"""
    cfg = {"thresholds": {"default_daily_usd": 42,
                          "lines": {"_why_lines": "写理由的地方", "识律 lex": 30}}}
    default, per_line = thresholds_of(cfg)
    assert default == 42.0
    assert per_line == {"识律 lex": 30.0}


def test_misspelled_line_threshold_is_surfaced():
    """拼错线名 = 那条线永远用默认阈值。这里验证差集能被算出来（summary 靠它点名）。"""
    cfg = {"thresholds": {"lines": {"wanxing": 5}}}
    _, per_line = thresholds_of(cfg)
    doc = _doc(lines=[("萬星 wanxing", "2026-11-13", 1, 1.0)])
    _, _, detail = evaluate(doc, {}, NOW, default_usd=50, per_line=per_line)
    seen = {d["line"] for d in detail if d.get("line")}
    assert set(per_line) - seen == {"wanxing"}, "拼错的键必须能被点名，不能静默失效"


# ── 4/5. 无数据与缺声明必须非零退出 ─────────────────────────────────────────

def test_missing_config_exits_not_configured(tmp_path):
    from scripts.ciyuan_line_alerts import EXIT_NOT_CONFIGURED, main  # noqa: PLC0415

    data = tmp_path / "quota.json"
    data.write_text(json.dumps(_doc(lines=[("萬星 wanxing", "2026-11-13", 1, 1.0)])),
                    encoding="utf-8")
    code = main(["--from-json", str(data), "--dry-run",
                 "--config", str(tmp_path / "nope.json")])
    assert code == EXIT_NOT_CONFIGURED, "缺投递声明必须以 exit 3 暴露，不能当成通过"


def test_blind_without_dsn_or_json(monkeypatch):
    from scripts.ciyuan_line_alerts import EXIT_BLIND, main  # noqa: PLC0415

    monkeypatch.delenv("CIYUAN_DSN", raising=False)
    monkeypatch.delenv("SUB2API_DSN", raising=False)
    assert main(["--dry-run"]) == EXIT_BLIND, "读不到数据必须 exit 4，不能落成'没有告警'"


def test_require_limits_turns_unlimited_into_failure(tmp_path):
    """``--require-limits`` 是给闸用的：让「无上限」能被证明会红。"""
    from scripts.ciyuan_line_alerts import EXIT_DEAD_LETTER, main  # noqa: PLC0415

    data = tmp_path / "quota.json"
    data.write_text(json.dumps(_doc(lines=[("萬星 wanxing", "2026-11-13", 1, 1.0)],
                                    groups=[_group("core")])), encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"delivery": {"channel": "smtp", "to": ["a@b.c"]}}),
                   encoding="utf-8")
    assert main(["--from-json", str(data), "--dry-run", "--config", str(cfg),
                 "--require-limits"]) == EXIT_DEAD_LETTER
    # 默认不开启：16/16 全无上限是配置现状，不该让这条线常年红
    assert main(["--from-json", str(data), "--dry-run", "--config", str(cfg)]) == 0


# ── 6. 超阈时消息真的交给投递面（3.3）────────────────────────────────────────

def test_firing_hands_one_message_to_delivery_and_records_receipt(tmp_path, monkeypatch):
    """投递面用桩替换（不真发信），但要钉住三件事：

    - 一轮只交一条消息（不刷屏）；
    - 收件人来自**版本化配置**，不是藏在一个 env 里；
    - 正文带上「无上限」事实——不然收到"超阈"的人会以为额度在管着。
    """
    import scripts.ciyuan_line_alerts as mod  # noqa: PLC0415

    data = tmp_path / "quota.json"
    data.write_text(json.dumps(_doc(
        lines=[("萬星 wanxing", "2026-11-13", 9000, 99.0)],
        groups=[_group("core"), _group("image-video")])), encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"delivery": {"channel": "smtp", "to": ["ops@example.com"]},
                               "thresholds": {"default_daily_usd": 50}}), encoding="utf-8")

    sent: list[dict] = []

    def fake_dispatch(messages, _cfg, **_kw):
        sent.extend(messages)
        return {"sent": len(messages), "failed": 0, "failures": [], "skipped": False}

    monkeypatch.setattr(mod, "dispatch", fake_dispatch)
    monkeypatch.setattr(mod, "smtp_from_env", lambda _env, recipients=None: ("stub", tuple(recipients or ())))
    monkeypatch.setattr(mod, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "EVENTS", tmp_path / "events.jsonl")

    #  отрицательная половина：凭据缺失时必须 exit 3，不许把「没发出去」当成功
    monkeypatch.delenv("FD_ALERT_SMTP_HOST", raising=False)
    monkeypatch.delenv("FD_ALERT_SMTP_FROM", raising=False)
    assert mod.main(["--from-json", str(data), "--run", "--config", str(cfg)]) == 3, \
        "缺 SMTP 凭据 ⇒ not_configured(3)，绝不能落成 0"
    assert sent == [], "没有通道就不该调用投递"
    assert not (tmp_path / "state.json").exists(), \
        "投递没发生就不许推进状态——否则凭据补好后这条告警会被当成『报过了』静默吞掉"

    #  正向：补齐凭据后真的交出去，并落状态/留痕
    monkeypatch.setenv("FD_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("FD_ALERT_SMTP_FROM", "alerts@example.com")
    code = mod.main(["--from-json", str(data), "--run", "--config", str(cfg)])

    assert code == 0, "应送达且送达成功 ⇒ exit 0"
    assert len(sent) == 1, "一条线超阈只发一条，不按天/按键刷屏"
    body = json.loads(sent[0]["body"])
    assert body["line"] == "萬星 wanxing"
    assert body["today_usd"] == 99.0
    assert body["unlimited_groups"] == 2
    assert "无上限" in body["no_limit_note"]
    assert (tmp_path / "state.json").exists(), "--run 必须落状态，否则下一轮会重复发"
    assert (tmp_path / "events.jsonl").exists(), "留痕文件必须写"


# ── 6. 「按线」的分母必须写出来（2026-11-14 新增）────────────────────────────
#
# 2026-11-14 实测：近 7 天 $250.08 里只有 10.7% 能归到某条线，89.3% 全在 unknown
# （两把自有账号的键）。而这条告警此前对这件事一个字都不说——`unknown` 只是明细
# 里的一行，读者看不出"其余九成根本没进任何一条线"。
# 这与「无上限」是同一族缺陷：一个只在有值时才有意义的判据，缺值时必须自己也说出来。


def _unknown_rows():
    """复刻 2026-11-14 的真实形状：自有账号两个中文/英文首段吃掉绝大部分。"""
    return [
        ("unknown", "2026-11-13", 42135, 130.72),
        ("unknown", "2026-11-13", 25342, 91.20),
        ("萬星 wanxing", "2026-11-13", 9063, 26.60),
        ("law-bench", "2026-11-13", 80, 0.15),
    ]


def test_unattributed_share_is_measured_over_the_window():
    doc = _doc(lines=_unknown_rows(), groups=[_group("core")])
    facts = unattributed_share(doc)
    assert facts["total_usd"] == pytest.approx(248.67, abs=0.01)
    assert facts["unattributed_usd"] == pytest.approx(221.92, abs=0.01)
    assert facts["share"] == pytest.approx(0.892, abs=0.005), \
        "这个数就是「按线读数覆盖了多少」的另一半，不许算成 0 或 1"
    assert "没有落在任何一条线上" in facts["note"]
    assert "89" in facts["note"], "正文里要直接出现百分比，不能只给字段"


def test_no_usage_is_its_own_state_not_zero_percent():
    """窗口内没数据 ⇒ share 必须是 None，note 明说「无从计算」。

    写成 0.0 会造出「未归因 0%」这个假事实——正是
    「『一条记录都没有』须自成一态」那个坑。
    """
    facts = unattributed_share(_doc(lines=[]))
    assert facts["share"] is None, "无数据不许冒充 0%"
    assert facts["total_usd"] == 0.0
    assert "无从计算" in facts["note"]
    assert "不等于 0%" in facts["note"]


def test_fully_attributed_says_so_instead_of_dividing():
    doc = _doc(lines=[("萬星 wanxing", "2026-11-13", 10, 5.0)])
    facts = unattributed_share(doc)
    assert facts["share"] == 0.0
    assert facts["unattributed_usd"] == 0.0
    assert "全部" in facts["note"] and "未归因 $0.00" in facts["note"]


def test_unknown_prefixes_are_named_in_the_note():
    """只说「有 89% 未归因」不够——必须点名首段，否则没人知道该给谁一条线。"""
    doc = _doc(lines=[("unknown", "2026-11-13", 1, 9.0),
                      ("萬星 wanxing", "2026-11-13", 1, 1.0)])
    doc["per_line_day"][0]["raw_prefix_if_unknown"] = "特价"
    facts = unattributed_share(doc)
    assert "特价" in facts["note"]
    assert facts["top_prefixes"] == [{"seg": "特价", "usd": 9.0}]


def test_top_prefixes_rank_by_spend_not_alphabetically():
    """**首版就错在这里，必须钉住。**

    首版直接取 ``line_daily_totals`` 的 `raw_prefixes`（那是 ``sorted(set())[:5]``，
    即字母序），于是实测数据里真正花掉 88% 的 `特价`/`openrouter` 被
    `413621549`/`acp`/`embedding`/`enforce`/`fd` 这几个金额近零、字母序在前的首段
    全部挤掉 —— 「主要首段」指错了对象。这里用 7 个首段（超过 top=3 的截断），
    且让金额最大的两个排在**字母序最后**，只有按金额排序才能通过。
    """
    doc = _doc(lines=[
        ("unknown", "2026-11-13", 1, 0.01),    # 413621549
        ("unknown", "2026-11-13", 1, 0.02),    # acp
        ("unknown", "2026-11-13", 1, 0.03),    # embedding
        ("unknown", "2026-11-13", 1, 0.04),    # enforce
        ("unknown", "2026-11-13", 1, 0.05),    # fd
        ("unknown", "2026-11-13", 1, 91.20),   # openrouter ← 字母序靠后
        ("unknown", "2026-11-13", 1, 130.72),  # 特价 ← 字母序最后
        ("萬星 wanxing", "2026-11-13", 1, 26.60),
    ])
    for row, seg in zip(doc["per_line_day"],
                        ["413621549", "acp", "embedding", "enforce", "fd",
                         "openrouter", "特价", ""]):
        row["raw_prefix_if_unknown"] = seg
    facts = unattributed_share(doc)
    segs = [p["seg"] for p in facts["top_prefixes"]]
    assert segs == ["特价", "openrouter", "fd"], f"必须按花费降序取前三，实得 {segs}"
    assert "特价" in facts["note"] and "130.72" in facts["note"]
    assert "acp" not in facts["note"], "金额近零的首段不该占掉名额"


def test_unattributed_note_rides_along_with_every_alert():
    """超阈告警的正文里必须带上「按线读数只覆盖多少」，否则照样读不出盲区。"""
    doc = _doc(lines=_unknown_rows(), groups=[_group("core"), _group("dev-sandbox")])
    _, events, detail = evaluate(doc, {}, NOW, default_usd=50, per_line={})
    msgs = compose_messages(events, detail, doc)
    assert msgs, "两条线超阈（unknown 221.92 / 萬星 26.60 vs 阈值 50）"
    for m in msgs:
        body = json.loads(m["body"])
        assert body["unattributed_share"] == pytest.approx(0.892, abs=0.005)
        assert body["window_total_usd"] == pytest.approx(248.67, abs=0.01)
        assert "没有落在任何一条线上" in body["unattributed_note"]
