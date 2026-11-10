"""nasa-firms 快照可用性判据（2026-11-10）。

为什么单独钉这几条：上游的失败形态**不止「没数据」一种**。实测 2026-11-10：

* S-NPP 24h 文件 → HTTP 200、只有表头（空）；
* 同产品 7d 文件 → 35,261 行，但 ``acq_date`` **全部是 2026-11-02**，而文件本身
  今天 04:53 还在重新生成。**「200 + 有行」照样可以是陈的**——只判「有没有行」
  会把 8 天前的火点当今日快照静默灌进库里。

所以判据是三条：(1) 有行；(2) 最新 acq_date 不超 ``MAX_SNAPSHOT_AGE_DAYS``；
(3) 逐个平台降级，全废才红，且错误里逐条写清原因。
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
          "confidence,version,bright_ti5,frp,daynight\n")


def _load():
    spec = importlib.util.spec_from_file_location("nasa_firms", REPO / "spiders" / "nasa-firms" / "spider.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nf = _load()
NOW = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)


def _csv(*dates, satellite="N20"):
    body = HEADER
    for i, date in enumerate(dates):
        body += (f"10.5,20.5,330.1,0.4,0.4,{date},0041,{satellite},n,2.0NRT,"
                 "290.1,1.2,D\n")
    return body


def _fake(fetch_map, monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url in fetch_map:
            value = fetch_map[url]
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(nf, "_fetch_csv", fake_fetch)
    return calls


def test_empty_first_platform_falls_through(monkeypatch):
    """首选平台只有表头 → 换下一个，而不是整条源红。"""
    first, second = nf.PLATFORMS[0], nf.PLATFORMS[1]
    _fake({first[1]: HEADER, second[1]: _csv("2026-11-10")}, monkeypatch)
    url, text = nf._pick_snapshot(now=NOW)
    assert url == second[1]
    assert "2026-11-10" in text


def test_stale_snapshot_is_rejected_even_though_it_has_rows(monkeypatch):
    """这条是本次事故的正身：有 35,261 行、HTTP 200，但日期停在 8 天前。"""
    first, second = nf.PLATFORMS[0], nf.PLATFORMS[1]
    stale = _csv("2026-11-02", "2026-11-02", "2026-11-02")
    _fake({first[1]: stale, second[1]: _csv("2026-11-10")}, monkeypatch)
    url, _ = nf._pick_snapshot(now=NOW)
    assert url == second[1], "陈旧快照必须被跳过"


def test_fresh_first_platform_wins(monkeypatch):
    """首选平台新鲜时不要无谓降级（只看一个 URL 说明没多打请求）。"""
    first = nf.PLATFORMS[0]
    calls = _fake({first[1]: _csv("2026-11-09", "2026-11-10")}, monkeypatch)
    url, _ = nf._pick_snapshot(now=NOW)
    assert url == first[1]
    assert calls == [first[1]]


def test_age_boundary_is_inclusive(monkeypatch):
    """恰好 MAX_SNAPSHOT_AGE_DAYS 天仍算新鲜（24h 窗口跨 UTC 日界，留一格余量）。"""
    limit_date = (NOW - timedelta(days=nf.MAX_SNAPSHOT_AGE_DAYS)).strftime("%Y-%m-%d")
    first = nf.PLATFORMS[0]
    _fake({first[1]: _csv(limit_date)}, monkeypatch)
    assert nf._pick_snapshot(now=NOW)[0] == first[1]


def test_one_day_over_the_boundary_is_rejected(monkeypatch):
    too_old = (NOW - timedelta(days=nf.MAX_SNAPSHOT_AGE_DAYS + 1)).strftime("%Y-%m-%d")
    first, second = nf.PLATFORMS[0], nf.PLATFORMS[1]
    _fake({first[1]: _csv(too_old), second[1]: _csv("2026-11-10")}, monkeypatch)
    assert nf._pick_snapshot(now=NOW)[0] == second[1]


def test_all_platforms_dead_raises_with_every_reason(monkeypatch):
    """全废要红，且错误里逐个平台写清原因 —— 否则运维只知道『挂了』。"""
    _fake({code_url: HEADER for _, code_url in nf.PLATFORMS}, monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        nf._pick_snapshot(now=NOW)
    message = str(exc.value)
    for code, _ in nf.PLATFORMS:
        assert code in message
    assert message.count("快照无数据行") == len(nf.PLATFORMS)


def test_fetch_error_on_one_platform_does_not_abort(monkeypatch):
    first, second = nf.PLATFORMS[0], nf.PLATFORMS[1]
    _fake({first[1]: OSError("connection reset"), second[1]: _csv("2026-11-10")}, monkeypatch)
    assert nf._pick_snapshot(now=NOW)[0] == second[1]


def test_rows_carry_the_platform_that_served_them(monkeypatch):
    """url 列必须写实际服务的平台，否则事后无法解释 satellite 为什么不是 N。"""
    first, second = nf.PLATFORMS[0], nf.PLATFORMS[1]
    _fake({first[1]: HEADER, second[1]: _csv("2026-11-10", satellite="N21")}, monkeypatch)
    rows = nf.run_nasa_firms(limit=1)
    assert rows[0]["url"] == second[1]
    assert rows[0]["satellite"] == "N21"


def test_zero_limit_returns_empty_without_fetching(monkeypatch):
    _fake({}, monkeypatch)   # 任何 URL 都算 unexpected
    assert nf.run_nasa_firms(limit=0) == []
