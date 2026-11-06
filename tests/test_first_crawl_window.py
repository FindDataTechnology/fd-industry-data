"""首爬滚动 12 个月回补窗口（industry-crawl-gitops，harden-crawl-gitops-foundation 4.1/4.2/4.3）。

锁定四段契约：
1. 区间计算（整月截断：月初/月末/年末/闰日点亮，终点=点亮日）；
2. 首爬判据（无成功 crawl_runs 记录 → 带窗首爬；有成功记录 → 零参数原路径，
   存量源零行为变化；判据读失败降级为原路径不炸 tick）；
3. 参数传递链（pending_runs.params → dispatcher 专用 flag → runner 注入
   spider 上下文；窗口不进 --params 声明通道——平台属有参数，同 limit）；
4. 可见性（crawl_runs.metrics 携带生效窗口；坏窗口响亮失败而非静默忽略）。

示范源 abs-sdmx 的窗口消费用桩替身（不触网）：窗口过滤先于 limit 截断，
不带窗口的运行（增量、golden 重放）行为逐字节不变。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import dispatch  # noqa: E402
from fd_industry_data.dispatch import first_crawl_window, successful_run_sources  # noqa: E402
from fd_industry_data.dispatcher_cli import runner_command  # noqa: E402
from fd_industry_data.runner_cli import _parse_window, _window_kwargs  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 区间计算：整月截断边界
# ---------------------------------------------------------------------------


class TestFirstCrawlWindow:
    def test_mid_month_lit_day(self):
        """点亮 2026-11-06：12 个整月 Nov-2025..Oct-2026，起点=2025-11-01。"""
        assert first_crawl_window(date(2026, 11, 6)) == {
            "window_start": "2025-11-01", "window_end": "2026-11-06"}

    def test_month_start_lit_day(self):
        assert first_crawl_window(date(2026, 11, 1)) == {
            "window_start": "2025-11-01", "window_end": "2026-11-01"}

    def test_month_end_lit_day(self):
        assert first_crawl_window(date(2026, 11, 30)) == {
            "window_start": "2025-11-01", "window_end": "2026-11-30"}

    def test_year_boundary_lit_day(self):
        assert first_crawl_window(date(2026, 1, 31)) == {
            "window_start": "2025-01-01", "window_end": "2026-01-31"}

    def test_exactly_twelve_whole_months(self):
        assert first_crawl_window(date(2025, 3, 15)) == {
            "window_start": "2024-03-01", "window_end": "2025-03-15"}

    def test_leap_day_lit_day_never_invents_a_date(self):
        """2028-02-29 回溯 12 个月没有 2027-02-29——整月截断天然规避。"""
        assert first_crawl_window(date(2028, 2, 29)) == {
            "window_start": "2027-02-01", "window_end": "2028-02-29"}

    def test_months_override(self):
        assert first_crawl_window(date(2026, 11, 6), months=1) == {
            "window_start": "2026-10-01", "window_end": "2026-11-06"}


# ---------------------------------------------------------------------------
# 2. 首爬判据：无成功 crawl_runs 记录
# ---------------------------------------------------------------------------


class _QueuedConn:
    """psycopg2 stand-in: records executes, answers fetchall from a queue."""

    def __init__(self, fetches=None, fail_on=None):
        self.executed = []  # (single-spaced sql, params)
        self.fetches = list(fetches or [])
        self.fail_on = fail_on  # substring; matching execute raises

    def cursor(self):
        return _QueuedCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _QueuedCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if self._conn.fail_on and self._conn.fail_on in flat:
            raise RuntimeError("db unavailable")
        return None

    def fetchall(self):
        return self._conn.fetches.pop(0) if self._conn.fetches else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSuccessfulRunSources:
    def test_empty_input_touches_no_db(self):
        conn = _QueuedConn()
        assert successful_run_sources(conn, []) == set()
        assert conn.executed == []

    def test_returns_sources_with_success_rows(self):
        conn = _QueuedConn(fetches=[[("bls",), ("inmetals",)]])
        assert successful_run_sources(conn, ["bls", "inmetals", "fresh"]) == {
            "bls", "inmetals"}
        sql, params = conn.executed[0]
        assert "FROM crawl_runs" in sql and "status = 'success'" in sql
        assert params == (["bls", "fresh", "inmetals"],)  # sorted + deduped

    def test_db_failure_degrades_to_already_crawled(self):
        """判据读失败不炸 tick：全部视为已产 → 走原路径（宁缺窗口，不误判存量）。"""
        conn = _QueuedConn(fail_on="crawl_runs")
        assert successful_run_sources(conn, ["bls"]) == {"bls"}


# ---------------------------------------------------------------------------
# enqueue_due 集成：首爬行带窗口参数，存量行零参数
# ---------------------------------------------------------------------------


def _enqueue_due_with_fixed_now(monkeypatch, conn, *, now=None, site="zihan"):
    """Run enqueue_due with a fixed clock; return queue_run captures."""
    captured = []
    fixed = now or datetime(2026, 11, 6, 3, 10, tzinfo=timezone.utc)

    def fake_now(tz=None):
        return fixed

    orig_dt = dispatch.datetime
    orig_queue = dispatch.queue_run
    try:
        dispatch.datetime = type("D", (), {
            "now": staticmethod(fake_now),
            "timedelta": __import__("datetime").timedelta,
            "timezone": timezone})()
        dispatch.queue_run = lambda conn, src, **kw: captured.append((src, kw))
        enqueued = dispatch.enqueue_due(conn, site)
    finally:
        dispatch.datetime = orig_dt
        dispatch.queue_run = orig_queue
    return enqueued, captured


WINDOW = {"window_start": "2025-11-01", "window_end": "2026-11-06"}


class TestEnqueueDueFirstCrawl:
    SCHED = "10 3 * * *"  # matches the fixed 03:10 tick

    def test_first_crawl_row_carries_window_params(self, monkeypatch):
        # candidates, open_rows, recent, success-lookup(空 → 首爬)
        conn = _QueuedConn(fetches=[
            [("fresh-src", self.SCHED, None)], [], [], []])
        enqueued, captured = _enqueue_due_with_fixed_now(monkeypatch, conn)
        assert enqueued == ["fresh-src"]
        assert captured == [("fresh-src",
                             {"site": "zihan", "params": WINDOW,
                              "requested_by": "schedule-tick",
                              "known_sources": None})]

    def test_established_source_keeps_legacy_no_param_path(self, monkeypatch):
        """有成功记录的源：零行为变化——params 显式为 None（写库 '{}'）。"""
        conn = _QueuedConn(fetches=[
            [("bls", self.SCHED, None)], [], [], [("bls",)]])
        _, captured = _enqueue_due_with_fixed_now(monkeypatch, conn)
        assert captured == [("bls",
                             {"site": "zihan", "params": None,
                              "requested_by": "schedule-tick",
                              "known_sources": None})]

    def test_mixed_fleet_only_first_crawl_gets_window(self, monkeypatch):
        conn = _QueuedConn(fetches=[
            [("fresh-src", self.SCHED, None), ("bls", self.SCHED, None)],
            [], [], [("bls",)]])
        _, captured = _enqueue_due_with_fixed_now(monkeypatch, conn)
        params_by_src = {src: kw["params"] for src, kw in captured}
        assert params_by_src == {"fresh-src": WINDOW, "bls": None}

    def test_quiet_tick_costs_no_success_query(self, monkeypatch):
        """无 due 源时不跑判据查询（静默 tick 零额外开销）。"""
        conn = _QueuedConn(fetches=[[], [], [], [("never-popped",)]])
        enqueued, captured = _enqueue_due_with_fixed_now(monkeypatch, conn)
        assert enqueued == [] and captured == []
        # 只有三条 tick 查询；判据查询未发生
        assert len(conn.executed) == 3
        assert not any("status = 'success'" in s for s, _ in conn.executed)

    def test_success_lookup_failure_degrades_to_legacy_path(self, monkeypatch):
        # fail_on 精确锚定判据查询（tick 自身的 recent 查询也含 crawl_runs）
        conn = _QueuedConn(fetches=[[("fresh-src", self.SCHED, None)], [], []],
                           fail_on="DISTINCT source")
        _, captured = _enqueue_due_with_fixed_now(monkeypatch, conn)
        # 判据不可得 → 不误发首爬窗口，走原路径
        assert captured == [("fresh-src",
                             {"site": "zihan", "params": None,
                              "requested_by": "schedule-tick",
                              "known_sources": None})]


# ---------------------------------------------------------------------------
# 3. 参数传递链：pending_runs.params → 专用 flag（不进 --params 声明通道）
# ---------------------------------------------------------------------------


class TestRunnerCommand:
    BASE = [sys.executable, "-m", "fd_industry_data.runner_cli"]

    def test_no_params_is_legacy_argv(self):
        assert runner_command("bls", 100, {}) == self.BASE + ["bls", "--limit", "100"]

    def test_window_rides_dedicated_flags_not_params(self):
        argv = runner_command("bls", 100, {"window_start": "2025-11-01",
                                           "window_end": "2026-11-06"})
        assert argv == self.BASE + ["bls", "--limit", "100",
                                    "--window-start", "2025-11-01",
                                    "--window-end", "2026-11-06"]

    def test_window_stripped_from_params_channel(self):
        """窗口键不落入 --params：未声明窗口的 spider 不该看到丢弃噪声。"""
        argv = runner_command("bls", 50, {"window_start": "2025-11-01",
                                          "window_end": "2026-11-06",
                                          "scope": "annual"})
        i = argv.index("--params")
        payload = json.loads(argv[i + 1])
        assert payload == {"scope": "annual"}
        assert "--window-start" in argv and "--window-end" in argv

    def test_limit_never_duplicated(self):
        argv = runner_command("bls", 100, {"limit": 7})
        assert argv == self.BASE + ["bls", "--limit", "100"]
        assert "--params" not in argv

    def test_empty_window_value_sends_no_flag(self):
        argv = runner_command("bls", 100, {"window_start": ""})
        assert "--window-start" not in argv


# ---------------------------------------------------------------------------
# 4a. runner 窗口解析与注入辅助
# ---------------------------------------------------------------------------


class TestParseWindow:
    def test_absent_is_none(self):
        assert _parse_window(None, None) is None

    def test_valid_pair_normalized(self):
        assert _parse_window("2025-11-01", "2026-11-06") == {
            "window_start": "2025-11-01", "window_end": "2026-11-06"}

    def test_half_window_is_fatal(self):
        with pytest.raises(ValueError, match="need both"):
            _parse_window("2025-11-01", None)
        with pytest.raises(ValueError, match="need both"):
            _parse_window(None, "2026-11-06")
        with pytest.raises(ValueError, match="need both"):
            _parse_window("", "2026-11-06")

    def test_loose_format_is_fatal(self):
        """YYYY-MM 之类不收——窗口按整月截断构造，宽松解析会静默扩窗。"""
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            _parse_window("2025-11", "2026-11-06")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            _parse_window("20251101", "2026-11-06")

    def test_reversed_range_is_fatal(self):
        with pytest.raises(ValueError, match="after window_end"):
            _parse_window("2026-11-06", "2025-11-01")


class TestWindowKwargs:
    def test_named_params_injected(self):
        def fn(limit: int = 10, window_start=None, window_end=None):
            return []

        win = {"window_start": "2025-11-01", "window_end": "2026-11-06"}
        assert _window_kwargs(fn, win) == win

    def test_partially_named_gets_only_named(self):
        def fn(limit: int = 10, window_start=None):
            return []

        win = {"window_start": "2025-11-01", "window_end": "2026-11-06"}
        assert _window_kwargs(fn, win) == {"window_start": "2025-11-01"}

    def test_unnamed_spider_gets_nothing(self):
        """不声明窗口的 spider 零改动——kwargs 不注入，仅 env 通道可见。"""
        def fn(limit: int = 10):
            return []

        assert _window_kwargs(fn, {"window_start": "2025-11-01",
                                   "window_end": "2026-11-06"}) == {}

    def test_var_keyword_does_not_count(self):
        def fn(limit: int = 10, **kw):
            return []

        assert _window_kwargs(fn, {"window_start": "a", "window_end": "b"}) == {}

    def test_uninspectable_gets_nothing(self):
        assert _window_kwargs(print, {"window_start": "a"}) == {}

    def test_absent_window_is_empty(self):
        def fn(limit: int = 10, window_start=None):
            return []

        assert _window_kwargs(fn, None) == {}


# ---------------------------------------------------------------------------
# 4b. runner 端到端（临时 spider + writer 桩；不连中央库）
# ---------------------------------------------------------------------------


def _spider_unit(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "spiders" / name
    d.mkdir(parents=True)
    (d / "spider.py").write_text(body, encoding="utf-8")
    return tmp_path


def _write_windowdemo(tmp_path: Path) -> Path:
    return _spider_unit(tmp_path, "windowdemo", """
import json, os

def run_windowdemo(limit: int = 100, window_start=None, window_end=None):
    rec = {"limit": limit, "window_start": window_start,
           "window_end": window_end,
           "env_start": os.environ.get("FD_WINDOW_START"),
           "env_end": os.environ.get("FD_WINDOW_END")}
    with open(os.environ["WINDOWDEMO_OUT"], "a") as f:
        f.write(json.dumps(rec) + "\\n")
    return [{"i": i} for i in range(min(int(limit), 3))]
""")


def _write_plaindemo(tmp_path: Path) -> Path:
    return _spider_unit(tmp_path, "plaindemo", """
import json, os

def run_plaindemo(limit: int = 100):
    with open(os.environ["WINDOWDEMO_OUT"], "a") as f:
        f.write(json.dumps({"limit": limit}) + "\\n")
    return []
""")


class TestRunnerEndToEnd:
    """窗口经 runner 注入 spider 调用上下文（kwargs + env）并入 metrics。"""

    @pytest.fixture()
    def runner_env(self, tmp_path, monkeypatch):
        out = tmp_path / "out.jsonl"
        monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
        monkeypatch.setenv("WINDOWDEMO_OUT", str(out))
        monkeypatch.chdir(tmp_path)  # _load_entry 会 chdir 进源目录
        # runner 直接写 os.environ（非 monkeypatch），前后各清一次防跨测试泄漏
        for key in ("FD_WINDOW_START", "FD_WINDOW_END"):
            import os
            os.environ.pop(key, None)

        reports = []

        def fake_start_run(**kw):
            return 123

        def fake_finish_run(run_id, **kw):
            reports.append(kw)
            return True

        monkeypatch.setattr("fd_industry_data.runner_cli.open_run_for",
                            lambda src: None)
        monkeypatch.setattr("fd_industry_data.runner_cli.start_run",
                            fake_start_run)
        monkeypatch.setattr("fd_industry_data.runner_cli.finish_run",
                            fake_finish_run)
        monkeypatch.setattr("fd_industry_data.runner_cli.cancel_requested",
                            lambda run_id: False)
        yield tmp_path, out, reports
        for key in ("FD_WINDOW_START", "FD_WINDOW_END"):
            import os
            os.environ.pop(key, None)

    def _read_records(self, out: Path) -> list[dict]:
        if not out.exists():
            return []
        return [json.loads(line) for line in out.read_text().splitlines()]

    def test_window_reaches_spider_kwargs_env_and_metrics(self, runner_env):
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        rc = runner_cli.main(
            ["windowdemo", "--limit", "5",
             "--window-start", "2025-11-01", "--window-end", "2026-11-06"])
        assert rc == 0
        recs = self._read_records(out)
        assert len(recs) == 1
        assert recs[0]["window_start"] == "2025-11-01"
        assert recs[0]["window_end"] == "2026-11-06"
        assert recs[0]["env_start"] == "2025-11-01"
        assert recs[0]["env_end"] == "2026-11-06"
        assert len(reports) == 1
        assert reports[0]["status"] == "success"
        assert reports[0]["metrics"] == {"window_start": "2025-11-01",
                                         "window_end": "2026-11-06"}

    def test_no_window_keeps_legacy_context(self, runner_env):
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        assert runner_cli.main(["windowdemo", "--limit", "2"]) == 0
        recs = self._read_records(out)
        assert recs[0]["window_start"] is None
        assert recs[0]["env_start"] is None
        assert reports[0]["metrics"] is None

    def test_platform_window_wins_over_params_channel(self, runner_env):
        """窗口若同时出现在 --params，平台 flag 优先且不炸重复关键字。"""
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        rc = runner_cli.main(
            ["windowdemo", "--limit", "3",
             "--params", json.dumps({"window_start": "2020-01-01"}),
             "--window-start", "2025-11-01", "--window-end", "2026-11-06"])
        assert rc == 0
        recs = self._read_records(out)
        assert recs[0]["window_start"] == "2025-11-01"
        assert reports[0]["metrics"]["window_start"] == "2025-11-01"

    def test_dropped_params_and_window_share_metrics(self, runner_env):
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        rc = runner_cli.main(
            ["windowdemo", "--limit", "3",
             "--params", json.dumps({"nope": 1}),
             "--window-start", "2025-11-01", "--window-end", "2026-11-06"])
        assert rc == 0
        assert reports[0]["metrics"] == {
            "dropped_params": ["nope"],
            "window_start": "2025-11-01", "window_end": "2026-11-06"}

    def test_plain_spider_ignores_window_without_typeerror(self, runner_env):
        """未声明窗口的 spider：零改动、零失败（kwargs 未注入）。"""
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_plaindemo(tmp_path)
        rc = runner_cli.main(
            ["plaindemo", "--limit", "4",
             "--window-start", "2025-11-01", "--window-end", "2026-11-06"])
        assert rc == 0
        assert reports[0]["status"] == "success"
        assert self._read_records(out) == [{"limit": 4}]

    def test_malformed_window_fails_loudly_and_records(self, runner_env):
        """坏窗口不静默：运行失败留痕（crawl_runs 记录原因），spider 未执行。"""
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        rc = runner_cli.main(
            ["windowdemo", "--limit", "3",
             "--window-start", "2025-11", "--window-end", "2026-11-06"])
        assert rc == runner_cli.EXIT_ADAPTER_ERROR
        assert len(reports) == 1
        assert reports[0]["status"] == "failed"
        assert "window params" in reports[0]["error_head"]
        assert self._read_records(out) == []

    def test_half_window_fails_loudly_and_records(self, runner_env):
        from fd_industry_data import runner_cli

        tmp_path, out, reports = runner_env
        _write_windowdemo(tmp_path)
        rc = runner_cli.main(
            ["windowdemo", "--limit", "3", "--window-start", "2025-11-01"])
        assert rc == runner_cli.EXIT_ADAPTER_ERROR
        assert reports[0]["status"] == "failed"
        assert "need both" in reports[0]["error_head"]


# ---------------------------------------------------------------------------
# 5. 示范源 abs-sdmx 真正消费窗口取数（桩替身，不触网）
# ---------------------------------------------------------------------------


def _abs_csv(rows: list[tuple[str, str]]) -> bytes:
    header = "DATAFLOW,MEASURE,REGION,UNIT_MEASURE,TIME_PERIOD,OBS_VALUE"
    lines = [header]
    for period, value in rows:
        lines.append(f"BA_GCCSA,1,1GSYD,NUM,{period},{value}")
    return "\n".join(lines).encode()


@pytest.fixture()
def abs_mod():
    from fd_industry_data.loader import load_spider_module

    return load_spider_module("abs-sdmx", "spiders")


@pytest.fixture()
def abs_payload(monkeypatch):
    """桩替身 urlopen：返回固定切片（2001-07 起 + 2025/2026 若干月）。"""
    payload = _abs_csv([
        ("2001-07", "2513"),   # golden 锚（历史首值，固定）
        ("2025-10", "9000"),
        ("2025-11", "9100"),
        ("2026-03", "9200"),
        ("2026-10", "9300"),
        ("2026-11", "9400"),
        ("2026-12", "9500"),
    ])

    class _FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp())
    return payload


class TestAbsSdmxWindow:
    def test_window_filters_to_inclusive_months(self, abs_mod, abs_payload):
        rows = abs_mod.run_abs_sdmx(limit=100, window_start="2025-11-01",
                                    window_end="2026-11-06")
        assert [r["period"] for r in rows] == [
            "2025-11", "2026-03", "2026-10", "2026-11"]

    def test_window_filter_runs_before_limit_cut(self, abs_mod, abs_payload):
        """切片自 2001-07 升序：先截断会把最老的行当产出——过滤必须在前。"""
        rows = abs_mod.run_abs_sdmx(limit=2, window_start="2025-11-01",
                                    window_end="2026-11-06")
        assert [r["period"] for r in rows] == ["2025-11", "2026-03"]

    def test_no_window_is_unchanged_golden_path(self, abs_mod, abs_payload):
        """不带窗口（增量/golden 重放）：行为与改动前逐行一致。"""
        rows = abs_mod.run_abs_sdmx(limit=3)
        assert [r["period"] for r in rows] == ["2001-07", "2025-10", "2025-11"]
        assert rows[0]["value"] == 2513.0  # golden 001 锚定值

    def test_malformed_window_is_fatal(self, abs_mod, abs_payload):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            abs_mod.run_abs_sdmx(limit=3, window_start="2025-11",
                                 window_end="2026-11-06")

    def test_reversed_window_is_fatal(self, abs_mod, abs_payload):
        with pytest.raises(ValueError, match="晚于"):
            abs_mod.run_abs_sdmx(limit=3, window_start="2026-11-06",
                                 window_end="2025-11-01")

    def test_half_window_is_fatal(self, abs_mod, abs_payload):
        with pytest.raises(ValueError, match="成对"):
            abs_mod.run_abs_sdmx(limit=3, window_start="2025-11-01")

    def test_window_outside_data_starts_is_loud_red(self, abs_mod, abs_payload):
        """窗口内零行 = 失败即红（本单元既有契约），不静默空跑。"""
        with pytest.raises(RuntimeError, match="0 行"):
            abs_mod.run_abs_sdmx(limit=3, window_start="1990-01-01",
                                 window_end="1990-12-31")
