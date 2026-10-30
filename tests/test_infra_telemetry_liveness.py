"""services_liveness 单测（内联假 PORTS 台账 + 假 probe 注入，不触真网络）。

覆盖 spec infra-telemetry-surface R2 第③类场景：
- 台账解析：粗体行/普通行入选，表头/说明文字/合并端口行/异型表跳过，逐条状态正确
- 零端点（R4）：台账 host/IP 与 probe_host 绝不出现在返回体任何角落
- 拔通道：FD_TELEMETRY_PORTS_FILE 未设 / 文件不存在 / probe_host 未设 → not_configured，ok=False
- 探活失败是读数不是通道故障：probe 抛 socket 异常 → unreachable 态，仍 ok=True（语义分明）
- 空台账：0 行 → summary.note 标注"没登记"，不误报"全挂"
- 新鲜度：as_of 在场
"""
from __future__ import annotations

import json
import socket

from infra_telemetry import liveness

FAKE_PORTS = """\
# cheap 集群 NodePort 台账（fake）

说明行不算表格：31999 是规划值。

| NodePort | Service | 容器端口 | 用途 |
|---|---|---|---|
| **31852** | fd-prod/fd-cn-report | 8301 | 公网入口 |
| 31851 | fd-prod/fd-open-data-mcp | 8300 | hub 副本 |
| **31861** | fd-prod/fd-daas-mcp | 8311 | 网关上游 http://100.64.0.1:30899/mcp |
| 30874 / 31408 | harbor/harbor-registry | 5000 / 8080 | 合并端口行：第一格非纯数字，须跳过 |
| **30899** | **chengsi**（tencent 集群 `mcp/fd-open-data-mcp`） | 入口 | 冻结端口表：第二格非 ns/name，须跳过 |
| 31999 | 待定 | 8000 | 第二格非 ns/name，须跳过 |

> 「事实冻结」注：31852 已锚定，不是表格行。
"""


def make_ledger(tmp_path, text=FAKE_PORTS):
    f = tmp_path / "PORTS.md"
    f.write_text(text, encoding="utf-8")
    return f


def fake_probe(table):
    calls: list[tuple[str, int]] = []

    def probe(host, port):
        calls.append((host, port))
        return table.get(port, "unreachable")

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


def test_ledger_parse_and_states(tmp_path, monkeypatch):
    f = make_ledger(tmp_path)
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(f))
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")
    out = liveness.collect(
        probe=fake_probe({31852: "open", 31851: "timeout", 31861: "refused"})
    )
    assert out["tool"] == "services_liveness"
    assert out["ok"] is True
    by = {i["node_port"]: i for i in out["items"]}
    # 恰好三行 NodePort 入选：合并端口行/冻结表/说明行全被跳过
    assert set(by) == {31852, 31851, 31861}
    assert by[31852]["service"] == "fd-prod/fd-cn-report"
    assert by[31852]["state"] == "open"
    assert by[31851]["service"] == "fd-prod/fd-open-data-mcp"
    assert by[31851]["state"] == "timeout"
    assert by[31861]["service"] == "fd-prod/fd-daas-mcp"
    assert by[31861]["state"] == "refused"
    for i in out["items"]:
        assert set(i) == {"service", "node_port", "state"}
    assert out["summary"] == {
        "services": 3,
        "open": 1,
        "timeout": 1,
        "refused": 1,
        "unreachable": 0,
    }
    assert all(c["status"] == "ok" for c in out["channels"])
    # 零端点：probe_host 与台账内 IP 不得出现在返回体任何字段
    blob = json.dumps(out, ensure_ascii=False)
    assert "10.99.88.77" not in blob
    assert "100.64.0.1" not in blob
    assert "chengsi" not in blob


def test_ports_file_unset_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("FD_TELEMETRY_PORTS_FILE", raising=False)
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")
    out = liveness.collect(probe=lambda host, port: "open")
    assert out["ok"] is False
    ch = {c["channel"]: c for c in out["channels"]}
    assert ch["ports_file"]["status"] == "failed"
    assert ch["ports_file"]["reason"] == "not_configured"
    assert ch["probe_host"]["status"] == "ok"
    assert out["items"] == []
    assert out["summary"]["note"] == "channel failed — no data claimed"


def test_ports_file_missing_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(tmp_path / "nope" / "PORTS.md"))
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")
    out = liveness.collect(probe=lambda host, port: "open")
    assert out["ok"] is False
    assert out["channels"][0]["channel"] == "ports_file"
    assert out["channels"][0]["reason"] == "not_configured"


def test_probe_host_unset_not_configured(tmp_path, monkeypatch):
    f = make_ledger(tmp_path)
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(f))
    monkeypatch.delenv("FD_TELEMETRY_PROBE_HOST", raising=False)
    out = liveness.collect(probe=lambda host, port: "open")
    # 绝不默认猜内网地址：诚实失败，且台账通道本身是好的
    assert out["ok"] is False
    ch = {c["channel"]: c for c in out["channels"]}
    assert ch["probe_host"]["status"] == "failed"
    assert ch["probe_host"]["reason"] == "not_configured"
    assert ch["ports_file"]["status"] == "ok"
    assert out["items"] == []


def test_probe_socket_error_is_reading_not_channel_failure(tmp_path, monkeypatch):
    f = make_ledger(tmp_path)
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(f))
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")

    def angry_probe(host, port):
        if port == 31851:
            raise socket.gaierror("nodename nor servname provided")
        if port == 31861:
            raise OSError("no route to host")
        return "open"

    out = liveness.collect(probe=angry_probe)
    # 探活异常 = 服务读数 unreachable，通道仍 ok —— 与上面 not_configured（ok=False）语义分明
    assert out["ok"] is True
    assert all(c["status"] == "ok" for c in out["channels"])
    by = {i["node_port"]: i for i in out["items"]}
    assert by[31851]["state"] == "unreachable"
    assert by[31861]["state"] == "unreachable"
    assert by[31852]["state"] == "open"
    assert out["summary"]["unreachable"] == 2
    assert out["summary"]["services"] == 3


def test_as_of_freshness(tmp_path, monkeypatch):
    f = make_ledger(tmp_path)
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(f))
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")
    as_of = "2026-10-31T08:00:00+00:00"
    orig = liveness.utc_now_iso
    liveness.utc_now_iso = lambda: as_of
    try:
        out = liveness.collect(probe=lambda host, port: "open")
        live = liveness.collect(probe=lambda host, port: "open")
    finally:
        liveness.utc_now_iso = orig
    assert out["as_of"] == as_of
    assert live["as_of"]  # 默认走真实 UTC 时钟


def test_empty_ledger_note_not_total_outage(tmp_path, monkeypatch):
    header_only = "| NodePort | Service | 容器端口 | 用途 |\n|---|---|---|---|\n暂无登记。\n"
    f = make_ledger(tmp_path, header_only)
    monkeypatch.setenv("FD_TELEMETRY_PORTS_FILE", str(f))
    monkeypatch.setenv("FD_TELEMETRY_PROBE_HOST", "10.99.88.77")
    probe = fake_probe({})
    out = liveness.collect(probe=probe)
    assert out["ok"] is True
    assert out["items"] == []
    assert probe.calls == []  # 0 行不发起任何探测
    assert out["summary"]["services"] == 0
    assert out["summary"]["refused"] == 0  # 空台账 ≠ 全挂
    assert "0 NodePort rows" in out["summary"]["note"]


def test_parse_ports_edges():
    text = FAKE_PORTS + "| 31884 | fd-prod/fd-legal-search-mcp | 80→8000 | 箭头容器端口 |\n| 31852 | fd-prod/fd-cn-report | 8301 | 重复行 |\n"
    rows = liveness.parse_ports(text)
    assert [r["service"] for r in rows] == [
        "fd-prod/fd-cn-report",
        "fd-prod/fd-open-data-mcp",
        "fd-prod/fd-daas-mcp",
        "fd-prod/fd-legal-search-mcp",
    ]
    assert rows[3]["node_port"] == 31884
    assert rows[3]["container_port"] == "80→8000"
    # (service, port) 去重：31852 重复行只保留一条
    assert sum(1 for r in rows if r["node_port"] == 31852) == 1
