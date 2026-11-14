"""SUBMIT 的 turn 串行语义（2026-11-10 实测缺陷回归）。

实测：批量提交时第 2 条起全部 HTTP 409 `TURN_IN_FLIGHT`（每 caller+agent 同时只一个
turn），18 条里 17 条每天重试每天 409、工单永久卡死。这里钉两件事：
1. 被占时**有界等待**后重试同一张单，等到就正常受理；
2. 预算耗尽 → 记 `deferred` 并**本批停手**（继续捶剩余单只会各自 409）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import inspector as ins  # noqa: E402
from fd_industry_data.health import ticket as tm  # noqa: E402

EV = {"window": {"from": "2026-11-01", "to": "2026-11-10"}, "failing_runs": [],
      "last_success": None, "watermark": None}


def _mk(dirpath: pathlib.Path, slugs=("t-a", "t-b")) -> pathlib.Path:
    for slug in slugs:
        doc = tm.new_ticket(source=slug, unit=f"spiders/{slug}/", kind="generate",
                            brief={"source_urls": ["https://e.com"], "expectations": "x"},
                            evidence=EV, verify_commands=["true"])
        (dirpath / f"20261110-{slug}.yaml").write_text(tm.dump_ticket(doc), encoding="utf-8")
    return dirpath


@pytest.fixture
def cfg():
    return type("C", (), {"master_switch": True})()


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch):
    monkeypatch.setattr(ins, "_SUBMIT_TURN_POLL_S", 0.001)
    monkeypatch.setattr(ins, "_SUBMIT_TURN_BUDGET_S", 0.004)


def test_detects_only_409_turn_in_flight():
    assert ins._is_turn_in_flight(409, '{"code": "TURN_IN_FLIGHT"}') is True
    assert ins._is_turn_in_flight(409, '{"code": "CONFLICT"}') is False
    assert ins._is_turn_in_flight(500, "TURN_IN_FLIGHT") is False


def test_waits_then_accepts_same_ticket(tmp_path, cfg):
    d = _mk(tmp_path)
    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(idem)
        if len(calls) == 1:
            return 409, '{"code": "TURN_IN_FLIGHT"}'
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    assert [r["submitted"] for r in res] == [True, True]
    assert len(calls) == 3  # 第一张 409→重试→200；第二张直接 200


def test_budget_exhausted_defers_and_stops_batch(tmp_path, cfg):
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    calls: list[str] = []

    def poster(*args):
        calls.append(args[2])
        return 409, '{"code": "TURN_IN_FLIGHT"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    assert len(res) == 1, "预算耗尽后必须停手，不得继续捶剩余工单"
    assert res[0]["verdict"] == "deferred"
    assert res[0]["submitted"] is False
    assert "停手" in res[0]["verdict_reason"]
    # 延后不算受理：下一轮巡检还会重投这张单
    doc = tm.load_ticket(d / res[0]["ticket"])
    assert ins._submit_recorded(doc) is False


def test_batch_limits_bound_the_run(tmp_path, cfg, monkeypatch):
    """每轮限量 + 批量等待预算：不加界会撞 workflow 45 分钟超时（实测 49m30s 被掐）。"""
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 2)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 0.01)
    # 本条只验「张数」这条界；把时间余量清零，免得 330s 的预留在第一张之前就关掉整批
    monkeypatch.setattr(ins, "_SUBMIT_CALL_RESERVE_S", 0.0)
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c", "t-d"))
    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(idem)
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    real = [r for r in res if r["ticket"] != "__batch_limit__"]
    assert len(real) == 2, f"每轮最多 2 张，实际 {len(real)}"
    assert any(r["ticket"] == "__batch_limit__" for r in res), "超限需留一条说明条目"
    assert len(calls) == 2


def test_status_sync_is_not_bounded_by_submit_limits(tmp_path, monkeypatch):
    """批量上限只作用于 SUBMIT：STATUS 回读有自己的 max_reads，不受影响。"""
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 1)
    read: list[str] = []

    def fetcher(url, key, idem, text):
        read.append(text)
        return 200, '{"state": "working"}'

    res = ins.sync_status(d, "http://x", "k", fetcher=fetcher, max_reads=3)
    assert len(res) == 3, "STATUS 应读满自己的 max_reads，不被 SUBMIT 的 1 张上限约束"


def test_generation_tickets_submitted_before_repairs(tmp_path, cfg, monkeypatch):
    """turn 串行、每轮只能推进两三张：generate 单必须排在 repair 前面。

    不加这条，业务单永远排在每天自动重生成的 repair 单后面（实测 2026-11-10：
    18 张积压，每轮只推进 2 张，业务单排在队尾）。
    """
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 1)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 999)
    # 文件名顺序：repair 的单在字典序上更早（a-）但仍应排在 generate 之后
    for name, slug, kind in (
        ("20261101-a-repair.yaml", "aaa", "repair"),
        ("20261109-z-generate.yaml", "zzz", "generate"),
    ):
        doc = tm.new_ticket(source=slug, unit=f"spiders/{slug}/", kind=kind,
                            brief={"source_urls": ["https://e.com"], "expectations": "x"},
                            evidence=EV, verify_commands=["true"])
        (tmp_path / name).write_text(tm.dump_ticket(doc), encoding="utf-8")

    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(text)
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(tmp_path, "org/repo", "http://x", "k", cfg, poster=poster)
    real = [r for r in res if r["ticket"] != "__batch_limit__"]
    assert len(real) == 1
    assert real[0]["ticket"] == "20261109-z-generate.yaml", "generate 单必须优先"


def test_poster_retries_connection_errors(monkeypatch):
    """连接层抖动重试：实测一轮 10 次 SUBMIT 里 7 次 RemoteDisconnected/URLError。

    只重试连接层异常（URLError/ConnectionError/TimeoutError）——HTTPError 是有答复的，
    不该重试；重试沿用同一 idem，agent 侧 inbox 去重保证安全。
    """
    import urllib.error

    monkeypatch.setattr(ins, "_POSTER_RETRY_BACKOFF_S", 0)
    calls: list[int] = []
    real_urlopen = ins.urllib.request.urlopen

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"status":"queued"}'

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("connection reset")
        return _Resp()

    monkeypatch.setattr(ins.urllib.request, "urlopen", fake_urlopen)
    try:
        status, text = ins._default_poster("http://x", "k", "idem-1", "SUBMIT x y")
    finally:
        monkeypatch.setattr(ins.urllib.request, "urlopen", real_urlopen)
    assert status == 200
    assert len(calls) == 2, "连接层失败应重试一次后成功"


def test_poster_does_not_retry_http_error(monkeypatch):
    """HTTPError 不重试：4xx/5xx 是服务端的明确答复（含 409 TURN_IN_FLIGHT）。"""
    import io
    import urllib.error

    monkeypatch.setattr(ins, "_POSTER_RETRY_BACKOFF_S", 0)
    calls: list[int] = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError("http://x", 409, "Conflict", {},
                                     io.BytesIO(b'{"code":"TURN_IN_FLIGHT"}'))

    monkeypatch.setattr(ins.urllib.request, "urlopen", fake_urlopen)
    status, text = ins._default_poster("http://x", "k", "idem-2", "SUBMIT x y")
    assert status == 409
    assert "TURN_IN_FLIGHT" in text
    assert len(calls) == 1, "HTTPError 不得重试"


def test_submit_body_carries_inline_yaml(tmp_path):
    """SUBMIT 必须内联工单全文：现役 agent 工作区不含该仓（2026-11-10 回执原文）。"""
    doc = tm.new_ticket(source="inline", unit="spiders/inline/", kind="generate",
                        brief={"source_urls": ["https://e.com"], "expectations": "x"},
                        evidence=EV, verify_commands=["true"])
    f = tmp_path / "20261110-inline.yaml"
    f.write_text(tm.dump_ticket(doc), encoding="utf-8")

    body = ins._submit_body("org/repo", "reports/health-tickets/20261110-inline.yaml", f, True)
    assert body.startswith("SUBMIT org/repo reports/health-tickets/20261110-inline.yaml")
    assert "SUBMIT-INLINE" in body
    assert "```yaml" in body
    assert "kind: generate" in body, "工单全文必须在正文里"
    # 关掉内联时退回原协议（兼容有仓的部署）
    plain = ins._submit_body("org/repo", "x.yaml", f, False)
    assert plain == "SUBMIT org/repo x.yaml"


def test_hollow_receipt_is_not_accepted():
    """「读不到工单」的回执不得判成 accepted——它是 hollow-ok，会让工单永不重投。

    原文字（2026-11-10 实录）：「我理解了协议。…但当前工作区（/app）是平台工作区，
    不包含 fd-industry-data 仓库，因此无法直接读取该工单文件。」
    """
    hollow = (
        '{"result": {"parts": [{"kind": "text", "text": "我理解了协议。您提交了一张工单。'
        '但当前工作区（/app）是平台工作区，不包含 fd-industry-data 仓库，因此无法直接读取该工单文件。'
        '请问您能否通过 SUBMIT-INLINE 方式把工单 YAML 内容直接发给我？"}]}}'
    )
    verdict = ins.parse_submit_verdict(200, hollow, ins.extract_message_text(hollow))
    assert verdict["verdict"] != "accepted", (
        f"读不到工单的回执被判成 {verdict['verdict']}；这会让工单永不重投"
    )


class _Clock:
    """可手动推进的 monotonic，用于证明预算逻辑（不靠真 sleep）。"""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        # 假睡：只推进时钟，不真的等（否则 turn 重试用例要跑几十秒）
        self.now += s


def test_status_read_stops_at_total_budget(tmp_path, monkeypatch):
    """STATUS 回读必须有**总**预算：只有"每张 300s × 最多 8 张"时最坏 2400s。

    2026-11-12/13 连续两晚，deliver 单步跑 37 分钟撞穿 job 的 45 分钟墙、被砍在中途，
    而 `if: always()` 的回执步仍把半截状态提交进仓（半投递）。这里钉住"超预算就停手"。
    """
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c", "t-d"))
    clock = _Clock()
    monkeypatch.setattr(ins, "time", clock)
    calls: list[str] = []

    def fetcher(url, key, idem, text):
        calls.append(idem)
        clock.now += 60.0
        return 200, '{"state": "working"}'

    res = ins.sync_status(d, "http://x", "k", fetcher=fetcher, max_reads=4, budget_s=100.0)

    assert len(calls) == 2, f"预算 100s、每张 60s ⇒ 只该读 2 张，实读 {len(calls)} 张"
    assert res[-1]["ticket"] == "__status_budget__", "超预算必须留下显式标记，不能静默截断"
    assert "下一轮" in res[-1]["error"]


def test_status_read_timeout_shrinks_to_remaining_budget(tmp_path, monkeypatch):
    """每次回读的超时必须取「剩余预算」与 STATUS_READ_TIMEOUT 的较小值。

    固定 300s 会让 8 张单在预算之外继续排队等待——预算形同不存在。
    """
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    clock = _Clock()
    monkeypatch.setattr(ins, "time", clock)
    timeouts: list[float | None] = []

    def fake_poster(url, key, idem, body, timeout=None):
        timeouts.append(timeout)
        clock.now += 200.0
        return 200, '{"state": "working"}'

    monkeypatch.setattr(ins, "_default_poster", fake_poster)
    ins.sync_status(d, "http://x", "k", max_reads=3, budget_s=350.0)

    # 第 1 张：剩余 350 ⇒ 取 min(300, 350) = 300；推进到 200
    # 第 2 张：剩余 150 ⇒ 取 min(300, 150) = 150；推进到 400 ⇒ 第 3 张超预算停手
    assert timeouts == [300.0, 150.0], f"超时应随剩余预算收缩，实得 {timeouts}"


def test_batch_budget_counts_poster_wall_time(tmp_path, cfg, monkeypatch):
    """批量预算必须算**墙钟耗时**，不能只算"等 turn"的 sleep。

    实测（2026-11-13/14 工单生命周期时间戳）每次 SUBMIT 调用本身要 303–309s，
    而旧版 `batch_waited` 只在 409 重试循环里累加 sleep ⇒ `_SUBMIT_BATCH_BUDGET_S=1800`
    从未触发过：24 张单最坏 24×300s=2 小时，11-12/13/14 三夜都跑满 60 分钟墙被掐。
    """
    clock = _Clock()
    monkeypatch.setattr(ins, "time", clock)
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 24)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 900.0)
    monkeypatch.setattr(ins, "_SUBMIT_CALL_RESERVE_S", 330.0)
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c", "t-d", "t-e"))

    def poster(url, key, idem, text):
        clock.now += 300.0          # 一次真实回合
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    real = [r for r in res if r["ticket"] != "__batch_limit__"]

    # 900s 预算、每次 300s、开单前留 330s 余量 ⇒ 300→600 之后 600+330>900 停手
    assert len(real) == 2, f"900s 预算 / 每次 300s 只该投 2 张，实投 {len(real)} 张"
    assert any(r["ticket"] == "__batch_limit__" for r in res), "超预算必须留说明条目"
    assert "已耗时" in res[-1]["verdict_reason"]


def test_turn_retry_counts_round_trip_time(tmp_path, cfg, monkeypatch):
    """409 重试的**往返耗时**也要计入单张等待预算。

    旧版只累计 sleep，于是"每张最多等 90s"在真实 300s/次的回合下意味着无限次重试。
    """
    clock = _Clock()
    monkeypatch.setattr(ins, "time", clock)
    monkeypatch.setattr(ins, "_SUBMIT_TURN_POLL_S", 10.0)
    monkeypatch.setattr(ins, "_SUBMIT_TURN_BUDGET_S", 90.0)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 1800.0)
    monkeypatch.setattr(ins, "_SUBMIT_CALL_RESERVE_S", 330.0)
    d = _mk(tmp_path, slugs=("t-a",))
    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(idem)
        clock.now += 300.0
        return 409, '{"code": "TURN_IN_FLIGHT"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)

    # 首投 300s ⇒ 单张预算 90s 已被一次往返吃满 ⇒ 只该重试 1 次
    assert len(calls) == 2, f"单张预算 90s、一次往返 300s ⇒ 只能重试 1 次，实试 {len(calls)} 次"
    assert res[0]["verdict"] == "deferred"
    assert res[0]["waited_s"] >= 300, "回执里要记真实等待（含往返），不能只记 sleep"


def test_submit_timeout_shrinks_to_remaining_budget(tmp_path, cfg, monkeypatch):
    """每次 SUBMIT 的 timeout 必须按「剩余预算 / 重试次数」收缩。

    `_default_poster` 默认 timeout=900s 且连接层重试 3 次 ⇒ 固定超时下**一次调用**
    最坏 45 分钟，能独占整个 60 分钟作业墙——预算算得再准也拦不住它。
    """
    clock = _Clock()
    monkeypatch.setattr(ins, "time", clock)
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 24)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 900.0)
    monkeypatch.setattr(ins, "_SUBMIT_CALL_RESERVE_S", 330.0)
    monkeypatch.setattr(ins, "_SUBMIT_CALL_TIMEOUT_S", 900.0)
    monkeypatch.setattr(ins, "_POSTER_ATTEMPTS", 3)
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c", "t-d"))
    timeouts: list[float | None] = []

    def poster(url, key, idem, text, timeout=None):
        timeouts.append(timeout)
        clock.now += timeout or 0.0     # 最坏情况：每次调用都跑到超时
        return 200, '{"status": "queued"}'

    ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)

    # 900 预算 / 3 次重试：300 → 剩 600 → 200 → 剩 400 → 133.3；之后 633+330 > 900 停手
    assert timeouts == [300.0, 200.0, pytest.approx(133.33, abs=0.01)], \
        f"超时应随剩余预算收缩，实得 {timeouts}"
    assert sum(timeouts) <= 900.0, f"收缩后整批不得超预算，实耗 {sum(timeouts)}s"
