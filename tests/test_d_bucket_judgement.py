"""D-bucket judgement tests (harden-crawl-delivery 3.6).

The D bucket exists because forcing a `min_rows_per_run` floor on a source with
too little history manufactures false positives. The replacement judgement must
put each source in exactly one of: stall / stable-unavailable /
history-insufficient (plus "judgeable-ok" for completeness).
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    path = REPO / "scripts" / "d_bucket_judgement.py"
    spec = importlib.util.spec_from_file_location("dbucket", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


d_bucket = _load()
TODAY = date(2026, 11, 10)


def _meta(period="month", field="period"):
    return {"data_period": period, "field": field, "schedule": "0 4 * * *"}


def test_too_little_history_is_not_a_defect():
    out = d_bucket.classify_source("alpha", _meta(), ok_runs=2, last_ok=None,
                                   items=[], today=TODAY)
    assert out["column"] == "history-insufficient"


def test_stalled_watermark_is_the_real_defect():
    items = [{"period": "2026-05"}, {"period": "2026-06"}]
    out = d_bucket.classify_source("alpha", _meta(), ok_runs=10,
                                   last_ok=datetime.now(timezone.utc),
                                   items=items, today=TODAY)
    assert out["column"] == "stall"
    assert out["watermark"] == "2026-06"
    assert out["periods_behind"] >= 2


def test_current_watermark_is_judgeable_ok():
    items = [{"period": "2026-11"}]
    out = d_bucket.classify_source("alpha", _meta(), ok_runs=10,
                                   last_ok=datetime.now(timezone.utc),
                                   items=items, today=TODAY)
    assert out["column"] == "judgeable-ok"


def test_no_field_but_steady_runs_is_stable_unavailable():
    out = d_bucket.classify_source("alpha", _meta(field=None), ok_runs=20,
                                   last_ok=datetime.now(timezone.utc),
                                   items=[{"x": 1}], today=TODAY)
    assert out["column"] == "stable-unavailable"


def test_no_field_and_stale_runs_is_not_called_stable():
    """没有水位 + 上次成功很久以前 = 可能是停摆，不能塞进「稳定但不可得」。"""
    stale = datetime.now(timezone.utc) - timedelta(days=20)
    out = d_bucket.classify_source("alpha", _meta(period="day", field=None), ok_runs=20,
                                   last_ok=stale, items=[], today=TODAY)
    assert out["column"] == "stale-unjudgeable"


def test_irregular_without_field_is_stable_unavailable_when_recent():
    out = d_bucket.classify_source("alpha", _meta(period="irregular", field=None),
                                   ok_runs=10, last_ok=datetime.now(timezone.utc),
                                   items=[], today=TODAY)
    assert out["column"] == "stable-unavailable"


def test_threshold_is_a_parameter_not_a_constant():
    out = d_bucket.classify_source("alpha", _meta(), ok_runs=8, last_ok=None,
                                   items=[], today=TODAY, min_ok_runs=10)
    assert out["column"] == "history-insufficient"
    out2 = d_bucket.classify_source("alpha", _meta(field=None), ok_runs=8,
                                    last_ok=datetime.now(timezone.utc),
                                    items=[], today=TODAY, min_ok_runs=5)
    assert out2["column"] == "stable-unavailable"


def test_every_source_lands_in_exactly_one_column():
    """判据是互斥的：一个源只能进一列，不能既停滞又历史不足。"""
    seen = set()
    cases = [
        # (ok_runs, last_ok, items, period, field)
        (1, None, [], "month", "period"),
        (10, datetime.now(timezone.utc), [{"period": "2020-01"}], "month", "period"),
        (10, datetime.now(timezone.utc), [{"period": "2026-11"}], "month", "period"),
        (10, datetime.now(timezone.utc), [], "month", None),
        # 日频 + 30 天没成功 = 陈旧（月频下 30 天仍在 2 个周期内，不属于此列）
        (10, datetime.now(timezone.utc) - timedelta(days=30), [], "day", None),
    ]
    for ok, last, items, period, field in cases:
        out = d_bucket.classify_source("alpha", _meta(period=period, field=field), ok_runs=ok,
                                       last_ok=last, items=items, today=TODAY)
        assert out["column"] in {"history-insufficient", "stall", "judgeable-ok",
                                 "stable-unavailable", "stale-unjudgeable"}
        seen.add(out["column"])
    assert len(seen) == 5  # 五种情况各落到不同列
