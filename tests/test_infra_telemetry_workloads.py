"""workloads_health（②通道）单测：假 kubectl JSON 注入，不依赖真集群/真二进制。

覆盖 spec 场景（infra-telemetry-surface R2②/R3/R4）：
- 逐条目可见：deployment NotReady + pod CrashLoopBackOff(restart=15) + 全 Ready
  deployment → 三条 state 正确、RestartStorm 判据命中、summary 计数对、as_of 在场、
  无 node/IP/端点字段
- 拔通道三态（+unauthorized）：contexts env 未设→not_configured；kubectl 缺失→
  upstream_unreachable；TimeoutExpired→timeout；非零退出 stderr 含 forbidden→
  unauthorized——各断言 ok=False
- 混合态诚实：两 context 一好一坏 → ok=False、好 context items 仍在、坏通道带 reason
- 脱敏：pod 异常 message 含 kubeconfig: 长串 → 出体前 scrub 为 [REDACTED]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from infra_telemetry import io_ as io_mod
from infra_telemetry import workloads
from infra_telemetry.envelope import ChannelError

AS_OF = "2026-10-31T00:00:00+00:00"

# ── 假 kubectl 返回体 ──────────────────────────────────────────────────────
DEPLOYS = {
    "kind": "List",
    "items": [
        {
            "metadata": {"name": "web", "namespace": "app"},
            "spec": {"replicas": 3},
            "status": {"readyReplicas": 2, "replicas": 3, "unavailableReplicas": 1},
        },
        {
            "metadata": {"name": "api", "namespace": "core"},
            "spec": {"replicas": 2},
            "status": {"readyReplicas": 2, "replicas": 2},
        },
    ],
}
PODS = {
    "kind": "List",
    "items": [
        {
            "metadata": {"name": "web-7d9f-x1", "namespace": "app"},
            "status": {
                "phase": "Running",
                "conditions": [
                    {"type": "Ready", "status": "False", "message": "containers not ready"}
                ],
                "containerStatuses": [
                    {
                        "name": "web",
                        "ready": False,
                        "restartCount": 15,
                        "state": {
                            "waiting": {
                                "reason": "CrashLoopBackOff",
                                "message": "back-off 5m0s restarting failed container",
                            }
                        },
                    }
                ],
            },
        }
    ],
}


def fake_runner(deploys=DEPLOYS, pods=PODS):
    """按 argv 资源词分发；顺带钉住 get-only 白名单构造。"""

    def runner(argv, *, channel, timeout):
        assert argv[0] and argv[1] == "--context" and argv[2], f"bad argv: {argv}"
        assert argv[3] == "get", "workloads 只允许 get 子命令"
        assert argv[4] in ("deployments", "pods")
        return deploys if argv[4] == "deployments" else pods

    return runner


def poison_runner(argv, *, channel, timeout):
    raise AssertionError("runner must not be probed when config is missing")


# ── 1. 逐条目可见 ──────────────────────────────────────────────────────────
def test_items_visible_states_and_summary(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    monkeypatch.setattr(workloads, "utc_now_iso", lambda: AS_OF)

    out = workloads.collect(runner=fake_runner())

    assert out["ok"] is True
    assert out["as_of"] == AS_OF
    assert out["channels"] == [{"channel": "k8s:prod", "status": "ok"}]

    by = {(i["kind"], i["workload"]): i for i in out["items"]}
    assert len(out["items"]) == 3  # 2 deployment + 1 pod，全量逐条（含 Ready）

    web = by[("deployment", "web")]
    assert web["namespace"] == "app"
    assert web["state"] == "NotReady"
    assert web["replicas_ready"] == "2/3"
    assert web["restart_count"] == 15  # 前缀启发式聚合子 pod 重启数

    crash = by[("pod", "web-7d9f-x1")]
    assert crash["state"] == "CrashLoopBackOff"
    assert crash["restart_count"] == 15
    assert crash["replicas_ready"] == "0/1"

    api = by[("deployment", "api")]
    assert api["state"] == "Ready"  # 正常 workload 也逐条出现
    assert api["replicas_ready"] == "2/2"

    # RestartStorm 判据命中：restart_count=15 >= 默认阈值 10
    assert out["summary"] == {
        "workloads": 3,
        "not_ready": 1,
        "crash_loop": 1,
        "restart_storm": 1,
        "unknown": 0,
    }

    # 只读逻辑名：无 node/端点/IP，item 字段集封闭
    allowed = {
        "context",
        "namespace",
        "workload",
        "kind",
        "replicas_ready",
        "state",
        "restart_count",
        "detail",
    }
    assert all(set(i) == allowed for i in out["items"])
    blob = json.dumps(out, ensure_ascii=False)
    for banned in ("node", "nodeName", "hostIP", "podIP", "ip"):
        assert banned not in blob
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", blob)  # 无 IPv4 端点形态


def test_restart_storm_state_and_env_threshold(monkeypatch):
    """已恢复就绪但重启数爆表 → RestartStorm；env 阈值可改写判据。"""
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    storm_pods = {
        "kind": "List",
        "items": [
            {
                "metadata": {"name": "worker-1", "namespace": "batch"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "w", "ready": True, "restartCount": 12, "state": {"running": {}}}
                    ],
                },
            }
        ],
    }

    out = workloads.collect(runner=fake_runner(deploys={"items": []}, pods=storm_pods))
    assert out["items"][0]["state"] == "RestartStorm"
    assert out["summary"]["restart_storm"] == 1

    monkeypatch.setenv("FD_TELEMETRY_RESTART_STORM_THRESHOLD", "20")
    out = workloads.collect(runner=fake_runner(deploys={"items": []}, pods=storm_pods))
    assert out["items"][0]["state"] == "Ready"  # 12 < 20，不再是风暴
    assert out["summary"]["restart_storm"] == 0


# ── 2. 拔通道三态 ──────────────────────────────────────────────────────────
def test_not_configured_contexts_env_unset(monkeypatch):
    monkeypatch.delenv("FD_TELEMETRY_KUBE_CONTEXTS", raising=False)

    out = workloads.collect(runner=poison_runner)  # 探针不得被触碰

    assert out["ok"] is False
    assert out["items"] == []
    ch = out["channels"][0]
    assert ch["status"] == "failed" and ch["reason"] == "not_configured"
    assert ch["channel"] == workloads.CONFIG_CHANNEL


def test_binary_missing_upstream_unreachable(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    monkeypatch.setenv("FD_TELEMETRY_KUBECTL", "kubectl-does-not-exist-xyzzy")

    out = workloads.collect()  # 真 io_.run_cmd_json，which() 即失败，不落子进程

    assert out["ok"] is False
    assert out["channels"] == [
        {
            "channel": "k8s:prod",
            "status": "failed",
            "reason": "upstream_unreachable",
            "detail": "binary kubectl-does-not-exist-xyzzy not found",
        }
    ]


def test_timeout_maps_to_timeout(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    monkeypatch.setenv("FD_TELEMETRY_KUBECTL", sys.executable)  # 存在的二进制过 which

    def boom_run(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 25))

    monkeypatch.setattr(io_mod.subprocess, "run", boom_run)

    out = workloads.collect()

    assert out["ok"] is False
    assert out["channels"][0]["channel"] == "k8s:prod"
    assert out["channels"][0]["reason"] == "timeout"


def test_forbidden_stderr_maps_to_unauthorized(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    monkeypatch.setenv("FD_TELEMETRY_KUBECTL", sys.executable)

    def denied_run(argv, **kw):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=1,
            stdout="",
            stderr="Error from server (Forbidden): deployments is forbidden: User cannot list",
        )

    monkeypatch.setattr(io_mod.subprocess, "run", denied_run)

    out = workloads.collect()

    assert out["ok"] is False
    assert out["channels"][0]["reason"] == "unauthorized"


# ── 3. 混合态诚实：部分可读 ────────────────────────────────────────────────
def test_mixed_contexts_partial_readable(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "good,bad")

    def runner(argv, *, channel, timeout):
        ctx = argv[2]
        if ctx == "bad":
            raise ChannelError(channel, "upstream_unreachable", "no such host")
        assert argv[3] == "get"
        return DEPLOYS if argv[4] == "deployments" else PODS

    out = workloads.collect(runner=runner)

    assert out["ok"] is False  # 禁假绿：有 failed 通道
    byname = {c["channel"]: c for c in out["channels"]}
    assert byname["k8s:good"]["status"] == "ok"
    assert byname["k8s:bad"] == {
        "channel": "k8s:bad",
        "status": "failed",
        "reason": "upstream_unreachable",
        "detail": "no such host",
    }
    # 好 context 的真实读数仍在
    assert len(out["items"]) == 3
    assert all(i["context"] == "good" for i in out["items"])
    assert out["summary"]["workloads"] == 3  # 坏 context 不算 Ready、不编条目


# ── 4. 脱敏：pod condition message 不过信封 ────────────────────────────────
def test_message_with_kubeconfig_is_scrubbed(monkeypatch):
    monkeypatch.setenv("FD_TELEMETRY_KUBE_CONTEXTS", "prod")
    secret = "/home/ops/.kube/prod-secret-kubeconfig-value"
    leaky_pods = {
        "kind": "List",
        "items": [
            {
                "metadata": {"name": "web-abc-1", "namespace": "app"},
                "status": {
                    "phase": "Running",
                    "conditions": [
                        {"type": "Ready", "status": "False", "message": f"pull aborted; kubeconfig: {secret}"},
                    ],
                    "containerStatuses": [
                        {"name": "web", "ready": False, "restartCount": 0,
                         "state": {"waiting": {"reason": "ImagePullBackOff", "message": "rpc error: code = Unknown"}}}
                    ],
                },
            }
        ],
    }

    out = workloads.collect(runner=fake_runner(deploys={"items": []}, pods=leaky_pods))

    item = out["items"][0]
    assert item["state"] == "ImagePullBackOff"
    blob = json.dumps(out, ensure_ascii=False)
    assert secret not in blob
    assert "[REDACTED]" in item["detail"]
    assert "kubeconfig: [REDACTED]" in item["detail"]
