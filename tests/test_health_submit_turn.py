"""SUBMIT 的 turn 串行语义（2026-11-10 实测缺陷回归）。

实测：批量提交时第 2 条起全部 HTTP 409 `TURN_IN_FLIGHT`（每 caller+agent 同时只一个
turn），18 条里 17 条每天重试每天 409、工单永久卡死。这里钉两件事：
1. 被占时**有界等待**后重试同一张单，等到就正常受理；
2. 预算耗尽 → 记 `deferred` 并**本批停手**（继续捶剩余单只会各自 409）。
"""
from __future__ import annotations

import pathlib
import sys
import urllib.error

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


def test_dry_run_covers_status_readback(tmp_path):
    """`--dry-run` 必须连 STATUS 一起停手——回读也是一次真 turn。

    2026-11-14 发现：`--dry-run` 只传给了 `submit_new_tickets`，`sync_status` 照旧
    发真请求。工作流的 `dry_run` 输入写着「仅出单不 SUBMIT（校准期用）」，于是校准
    轮既花模型钱、又和 SUBMIT 抢同一个 (caller, agent) 槽位——当天线上 STATUS 回执
    里就有一批 409 `TURN_IN_FLIGHT`，正是这种自撞。

    判据：**fetcher 一次都不许被调用**，且工单文件字节不变（dry run 不改状态）。
    """
    d = _mk(tmp_path, slugs=("t-a", "t-b"))
    before = {p.name: p.read_bytes() for p in sorted(d.glob("*.yaml"))}

    def boom(*a, **k):  # 被调用即失败：dry run 不该发任何请求
        raise AssertionError("dry run 仍然发起了 STATUS 请求")

    res = ins.sync_status(d, "http://x", "k", fetcher=boom, max_reads=3, dry_run=True)

    assert len(res) == 2, f"应报出「本来会读这 2 张」，实际 {res}"
    assert all(r.get("dry_run") is True for r in res), "条目须自带 dry_run 标记，别让人误读成真读了"
    assert all(r.get("http_status") is None for r in res), "dry run 没有回执状态码"
    after = {p.name: p.read_bytes() for p in sorted(d.glob("*.yaml"))}
    assert before == after, "dry run 不得改动工单文件"


def test_dry_run_still_prioritises_oldest_unread(tmp_path):
    """dry run 报的应该是**真跑时会读的那几张**，而不是随便几张。

    否则这个校准输出会替一个错误的读取顺序背书（最久未读优先的排序在 pending 阶段，
    dry run 只是不发请求）。
    """
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    # t-c 已经有更晚的回读记录 ⇒ 它的 _last_read_at 最大，应排最后。
    p = d / "20261110-t-c.yaml"
    doc = tm.load_ticket(p)
    tm.append_event(doc, "status", http_status=200, state="queued", note="", pr_url=None)
    p.write_text(tm.dump_ticket(doc), encoding="utf-8")

    res = ins.sync_status(d, "http://x", "k", max_reads=2, dry_run=True)
    names = [r["ticket"] for r in res]
    assert "20261110-t-c.yaml" not in names, f"最久未读优先应排除刚读过的 t-c，实际 {names}"


def test_jsonrpc_error_receipts_cap_retirement(tmp_path, cfg, monkeypatch):
    """agent 真跑过回合并以 JSON-RPC 错误收场的投递计满上限 → 停手转人工。

    实测（2026-11-15）：20261107-fred-cpi-ba279d6a 被 -32032/-32002 反复收走，
    每轮占住唯一槽位跑满整个回合预算（末次 600s 超时），同轮 8 张全 409 deferred、
    accepted=0 —— 一张永远处理不完的单饿死整条线。错误类此前无上限（try20）。
    """
    monkeypatch.setattr(ins, "_SUBMIT_TURN_FAIL_MAX", 2)
    d = _mk(tmp_path, slugs=("t-cap", "t-other"))
    name = "20261110-t-cap.yaml"
    doc = tm.load_ticket(d / name)
    for code in (-32032, -32002):
        tm.append_event(doc, "submitted", http_status=200, ok=False, verdict="error",
                        response=f'{{"code": {code}, "message": "x"}}')
    (d / name).write_text(tm.dump_ticket(doc), encoding="utf-8")

    calls: list[str] = []

    def poster(url, key, idem, body):
        calls.append(idem)
        return 200, "已入队"

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    capped = [r for r in res if r.get("ticket") == name]
    assert capped and capped[0]["verdict"] == "exhausted", capped
    assert "上限 2" in capped[0]["verdict_reason"], capped[0]["verdict_reason"]
    assert calls == ["20261110-t-other-try1"], (
        f"耗尽的工单不得再打门面，其余工单照常投；实际 calls={calls}"
    )
    doc2 = tm.load_ticket(d / name)
    kinds = [e.get("event") for e in doc2["lifecycle"]]
    assert "submit-exhausted" in kinds, kinds
    assert kinds.count("submitted") == 2, "停手不得再追加 submitted 事件"


def test_deferred_and_transport_failures_do_not_count_toward_cap(tmp_path, cfg):
    """409 deferred / HTTP 402 / 空响应 这些「回合没开始」的形态不计入上限。

    方向二：把通道故障误记成工单罪过，会提前退役其实有救的单——上限只数
    JSON-RPC 错误回执（-32xxx），其余 submitted(ok=false) 不计数。
    """
    d = _mk(tmp_path, slugs=("t-only",))
    name = "20261110-t-only.yaml"
    doc = tm.load_ticket(d / name)
    shapes = [
        {"http_status": 200, "ok": False, "verdict": "error", "response": "PLAN_EXHAUSTED text"},
        {"http_status": 502, "ok": False, "verdict": "error", "response": "<html>502</html>"},
        {"http_status": 409, "ok": False, "verdict": "deferred", "response": "turn in flight"},
        {"http_status": 200, "ok": False, "verdict": "unknown", "response": '{"code": "X"}'},
    ]
    for e in shapes:
        tm.append_event(doc, "submitted", **e)
    (d / name).write_text(tm.dump_ticket(doc), encoding="utf-8")

    def poster(url, key, idem, body):
        return 200, "已入队"

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    assert res[0]["verdict"] in ("accepted", "duplicate"), res[0]
    assert res[0]["attempt"] == 5, "非 JSON-RPC 错误回执不计入上限，照常递增重投"


def test_relayed_upstream_errors_count_toward_cap(tmp_path, cfg, monkeypatch):
    """-32003 是「上游错误原文中继」（实测含 502 与 429 rate_limit_error）⇒ **计入**上限。

    这条边界刻意放在另一侧，也很容易读错：面层的 409/402/裸 502 不计（见上一条用例），
    但**包成 JSON-RPC 错误的同一件事会计**——那一次回合确实被受理过、由上游收场。
    选这一侧的理由：少计＝一张真卡的单把整条线饿死；多计＝被限流挡住的单在 6 轮后
    判 exhausted 转人工，而判词里写着次数与末次原文，人一眼能看出是限流。
    **要翻转这条，必须同时改 `inspector.py` 里那段注释**——别只把 `-32\\d{3}` 放宽成
    白名单，那会把 `-32032 fetch failed`（传输层）一起放走。
    """
    monkeypatch.setattr(ins, "_SUBMIT_TURN_FAIL_MAX", 2)
    d = _mk(tmp_path, slugs=("t-relay",))
    name = "20261110-t-relay.yaml"
    doc = tm.load_ticket(d / name)
    for msg in ('502: {"message":"Upstream service temporarily unavailable"}',
                "429 Upstream account queue full (rate_limit_error)"):
        tm.append_event(doc, "submitted", http_status=200, ok=False, verdict="error",
                        response='{"code": -32003, "message": "%s"}' % msg.replace('"', "'"))
    (d / name).write_text(tm.dump_ticket(doc), encoding="utf-8")

    calls: list[str] = []

    def poster(url, key, idem, body):
        calls.append(idem)
        return 200, "已入队"

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    capped = [r for r in res if r.get("ticket") == name]
    assert capped and capped[0]["verdict"] == "exhausted", capped
    assert calls == [], f"被判耗尽后不得再打门面；实际 calls={calls}"


def test_poster_follows_one_same_host_redirect(tmp_path, monkeypatch):
    """POST 撞上前置的 308 时自己跟一跳（CPython 不跟 POST 的 307/308）。

    实测（2026-11-14）：一轮 8 次 SUBMIT 有 3 次拿到 Tengine 的 308 HTML 页
    （19:15:51Z / 23:14:05Z / 23:22:05Z），urllib 对 POST+308 直接抛 HTTPError，
    于是那几次投递既没到 API、也没被记成任何一类失败（verdict=unknown，reason 空）。
    这里用假 urlopen 复刻四段：① 308+Location 同主机同协议 → 跟过去拿到 200；
    ② 308+跨主机 Location → **不跟**（不能把 Bearer 送给别的主机）；
    ③ 308 无 Location → 原样返回 308，不无限跳；
    ④ 同主机但 https→http → **不跟**（协议降级＝凭据明文出门）。
    """
    calls: list[str] = []

    class _Resp:
        status = 200

        def read(self, _n=None):
            return b'{"result": {"ok": true}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            hdrs = {"Location": "/api/wanxing/v1/a2a/other"}
            raise urllib.error.HTTPError(req.full_url, 308, "Permanent Redirect", hdrs, None)
        return _Resp()

    monkeypatch.setattr(ins.urllib.request, "urlopen", fake_urlopen)
    st, tx = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x", "k", "idem1", "body")
    assert st == 200, (st, tx)
    assert calls == ["https://wanxing.example/api/wanxing/v1/a2a/x",
                     "https://wanxing.example/api/wanxing/v1/a2a/other"], calls

    # ② 跨主机：不跟，凭据不出门
    calls.clear()

    def cross_host(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 308, "Permanent Redirect",
                                     {"Location": "https://evil.example/x"}, None)

    monkeypatch.setattr(ins.urllib.request, "urlopen", cross_host)
    st, _ = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x", "k", "idem2", "body")
    assert st == 308, st
    assert len(calls) == 1, f"跨主机不得跟随，实际打了 {calls}"

    # ③ 无 Location：原样返回
    calls.clear()

    def no_loc(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 308, "Permanent Redirect", {}, None)

    monkeypatch.setattr(ins.urllib.request, "urlopen", no_loc)
    st, _ = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x", "k", "idem3", "body")
    assert st == 308 and len(calls) == 1, (st, calls)

    # ④ 同主机但**降级协议**（https → http）：主机名相等也不许跟——跟过去等于把
    #    `Authorization: Bearer …` 明文发上线。这条只有断言协议才抓得到：只比
    #    hostname 的实现会在这里跟过去，而其它三条臂全绿。
    calls.clear()

    def downgrade(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 308, "Permanent Redirect",
                                     {"Location": "http://wanxing.example/api/wanxing/v1/a2a/x"}, None)

    monkeypatch.setattr(ins.urllib.request, "urlopen", downgrade)
    st, _ = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x", "k", "idem4", "body")
    assert st == 308, st
    assert len(calls) == 1, f"跨协议不得跟随（会把 Bearer 明文发出），实际打了 {calls}"


def test_redirect_verdict_names_the_hop_not_silence():
    """3xx 的裁决必须是 `unknown` + **写明跳转**，不能是空 reason。

    之前 `status < 400` 落到 else 分支、HTML 解析失败 ⇒ verdict=unknown、reason=""，
    摘要里读不出「投递根本没到 API」与「agent 回了看不懂的话」的区别。
    """
    html = ('<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">\r\n<html>\r\n'
            '<head><title>308 Permanent Redirect</title></head>\r\n<body>\r\n'
            '<center><h1>308 Permanent Redirect</h1></center>\r\n<hr><center>tengine</center>\r\n'
            '</body>\r\n</html>')
    v = ins.parse_submit_verdict(308, html, "")
    assert v["verdict"] == "unknown", v
    assert "308" in v["reason"] and "跳转" in v["reason"], v["reason"]


def test_poster_carries_waf_challenge_cookie_on_the_follow_hop(monkeypatch):
    """WAF 的 308 是「发 Set-Cookie、让你带 cookie 再来」的挑战。

    2026-11-15 验收轮真跑实证：跟跳带了 Location 仍拿回同一页 308（reason
    「已按同主机跟一跳仍如此」）——旧跟跳只带原 headers，挑战 cookie 被丢掉。
    本用例复刻雷池的形状：第一跳 308 + `Set-Cookie: sl-session=…`，第二跳
    **只有**带 `Cookie` 才放行（不带就还是 308）。cookie 只在本次调用内存活，
    不跨调用、不跨工单。
    """
    calls: list[tuple[str, str | None]] = []

    class _Resp:
        status = 200

        def read(self, _n=None):
            return b'{"result": {"ok": true}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.get_header("Cookie")))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 308, "Permanent Redirect",
                {"Location": "/api/wanxing/v1/a2a/other",
                 "Set-Cookie": "sl-session=abc123; Path=/; HttpOnly"}, None)
        if req.get_header("Cookie") != "sl-session=abc123":
            # 不带 cookie 就还是同一道门——这正是「跟一跳仍如此」的成因
            raise urllib.error.HTTPError(
                req.full_url, 308, "Permanent Redirect",
                {"Location": "/api/wanxing/v1/a2a/other"}, None)
        return _Resp()

    monkeypatch.setattr(ins.urllib.request, "urlopen", fake_urlopen)
    st, tx = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x",
                                 "k", "idem-cookie", "body")
    assert st == 200, (st, tx)
    assert calls[1][0] == "https://wanxing.example/api/wanxing/v1/a2a/other"
    assert calls[1][1] == "sl-session=abc123", calls[1]
    # 无 Set-Cookie 的普通 308（既有 ①③④ 臂）不受影响：不产生 Cookie 头
    calls.clear()

    def plain_308(req, timeout=None):
        calls.append((req.full_url, req.get_header("Cookie")))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 308, "Permanent Redirect",
                                         {"Location": "/api/wanxing/v1/a2a/other"}, None)
        return _Resp()

    monkeypatch.setattr(ins.urllib.request, "urlopen", plain_308)
    st, _ = ins._default_poster("https://wanxing.example/api/wanxing/v1/a2a/x",
                                "k", "idem-nocookie", "body")
    assert st == 200 and calls[1][1] is None, (st, calls)
