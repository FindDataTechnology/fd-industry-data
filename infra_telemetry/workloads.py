"""② workloads_health — deployment/pod 逐工作负载健康（spec: infra-telemetry-surface R2 ②）。

语义：非就绪 deployment、CrashLoopBackOff、重启风暴、镜像拉取异常、容器状态未知，
逐 workload 标识。完全只读——经 kubectl ``get`` 子命令读取（见 _build_argv 白名单断言）。

数据来源（每 context 两条子命令，走 io_.run_cmd_json）：
- ``kubectl --context <ctx> get deployments -A -o json``
- ``kubectl --context <ctx> get pods -A -o json``  ← 全量拉取后本地筛

pods 二选一说明（spec 允许 field-selector 或本地筛）：本通道选**全量+本地筛**。
理由：``--field-selector=status.phase!=Running`` 会漏掉 phase 仍为 Running 但容器
处于 CrashLoopBackOff/ImagePullBackOff 的 pod（K8s 语义下这类 pod phase 常留在
Running），且完全看不到"已恢复就绪但重启数爆表"的 RestartStorm——逐 workload
的诚实口径要求全量视图。

配置（env，凭据只存在于部署环境，返回体零凭据零端点）：
- FD_TELEMETRY_KUBE_CONTEXTS          逗号分隔上下文列表；未设 → ChannelError(not_configured)
  （此失败没有上下文可点名，通道记录统一用配置通道名 ``k8s:config``）
- FD_TELEMETRY_KUBECTL                kubectl 二进制，默认 "kubectl"；找不到 →
  逐上下文 ChannelError(channel="k8s:<ctx>", "upstream_unreachable")
- FD_TELEMETRY_RESTART_STORM_THRESHOLD 重启风暴阈值，默认 10

通道诚实性：每 context 是一条独立通道记录（channel 名 ``k8s:<ctx>``），单 context
失败不污染其他 context 的读数；ok=False 时 items 仍带健康 context 的真实读数。
读不到的 context 一律**不**计入任何 workload——绝不把不可读算作 Ready。

items 只用逻辑名（context/namespace/workload——端点/IP/node 一律不出体）：
{namespace, workload, kind(deployment|pod), replicas_ready("2/3" 或 null=不可判),
 state(Ready|NotReady|CrashLoopBackOff|Pending|ImagePullBackOff|RestartStorm|Unknown),
 restart_count(int), context, detail(异常时的容器/条件消息，出体前过 envelope.scrub)}
detail 是逻辑名标识之外的唯一诊断字段，且只放 scrub 后的自由文本——异常类别要点名
原因才有操作性（spec R2 "点名该 workload、异常类别与最近重启计数"）。

state 优先级（pod）：CrashLoopBackOff > ImagePullBackOff(含 ErrImagePull) > Pending
(phase=Pending) > RestartStorm(restart_count>=阈值) > Ready(running 且全容器 ready)
> NotReady(running 未就绪 / phase=Failed) > Unknown（无 containerStatuses 等不可判）。
RestartStorm 对 CrashLoopBackOff 是叠加事实而非替代：state 取更具体的容器态，
restart_count>=阈值的 pod 一律计入 summary.restart_storm。
deployment：readyReplicas==spec.replicas → Ready，否则 NotReady；status 缺失 →
Unknown（replicas_ready=None，不冒充 0/N）。restart_count 按"pod 名前缀 =
deployment 名-' 的同 namespace 匹配"聚合（Deployment→RS→Pod 命名规约），是启发式，
仅用于逐 workload 展示；restart_storm 计数只认 kind=pod 条目，避免双重计数。
Succeeded pod（已完结 Job）跳过——非活跃 workload；Failed pod 计入 NotReady。

summary={workloads, not_ready, crash_loop, restart_storm, unknown}（正常 Ready 的
workload 也逐条出现并计入 workloads 总数）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Callable

from .envelope import ChannelError, channel_status, envelope, scrub, utc_now_iso
from .io_ import run_cmd_json

CONTEXTS_ENV = "FD_TELEMETRY_KUBE_CONTEXTS"
BINARY_ENV = "FD_TELEMETRY_KUBECTL"
STORM_ENV = "FD_TELEMETRY_RESTART_STORM_THRESHOLD"
DEFAULT_BINARY = "kubectl"
DEFAULT_STORM_THRESHOLD = 10
CONFIG_CHANNEL = "k8s:config"

STATES = frozenset(
    {
        "Ready",
        "NotReady",
        "CrashLoopBackOff",
        "Pending",
        "ImagePullBackOff",
        "RestartStorm",
        "Unknown",
    }
)

# 评审点：argv 严格白名单——本通道代码里只允许出现 get 子命令。
# 新增任何 kubectl 动词都必须先过这行断言（apply/delete/patch/scale/exec/logs/
# port-forward… 一律拒），写动作在工具面不存在（spec R1 完全只读）。
_READONLY_VERBS = frozenset({"get"})


def _build_argv(binary: str, ctx: str, args: list[str]) -> list[str]:
    """构造 kubectl argv。context 来自 env，防注入：不得以 '-' 开头冒充 flag。"""
    assert not ctx.startswith("-"), f"invalid kube context name: {ctx!r}"
    argv = [binary, "--context", ctx, *args]
    # 首个非 flag 位置参数即子命令动词——白名单外直接拒绝构造
    verb = next((tok for tok in args if not tok.startswith("-")), "")
    assert verb in _READONLY_VERBS, (
        f"workloads_health is read-only: only {sorted(_READONLY_VERBS)} "
        f"subcommands allowed, got verb={verb!r} argv={argv!r}"
    )
    return argv


def _deployments_argv(binary: str, ctx: str) -> list[str]:
    return _build_argv(binary, ctx, ["get", "deployments", "-A", "-o", "json"])


def _pods_argv(binary: str, ctx: str) -> list[str]:
    return _build_argv(binary, ctx, ["get", "pods", "-A", "-o", "json"])


def _pod_container_view(pod: dict) -> tuple[int, bool | None, str | None, list[str]]:
    """(max_restart, all_ready|None(无容器状态), waiting_reason, 异常消息列表)。"""
    status = pod.get("status") or {}
    statuses = status.get("containerStatuses") or []
    if not statuses:
        return 0, None, None, []
    restart = max(int(cs.get("restartCount") or 0) for cs in statuses)
    all_ready = all(bool(cs.get("ready")) for cs in statuses)
    waiting_reason: str | None = None
    messages: list[str] = []
    for cs in statuses:
        st = cs.get("state") or {}
        if "waiting" in st:
            reason = (st["waiting"] or {}).get("reason")
            msg = (st["waiting"] or {}).get("message")
            waiting_reason = waiting_reason or reason
            if msg:
                messages.append(msg)
        if not cs.get("ready"):
            last = st.get("terminated") or {}
            if last.get("message"):
                messages.append(last["message"])
    for cond in status.get("conditions") or []:
        if cond.get("type") == "Ready" and cond.get("status") != "True" and cond.get("message"):
            messages.append(cond["message"])
    return restart, all_ready, waiting_reason, messages


def _pod_state(phase: str | None, restart: int, all_ready: bool | None,
               waiting_reason: str | None, threshold: int) -> str:
    if waiting_reason == "CrashLoopBackOff":
        return "CrashLoopBackOff"
    if waiting_reason in ("ImagePullBackOff", "ErrImagePull"):
        return "ImagePullBackOff"
    if phase == "Pending":
        return "Pending"
    if all_ready is None:
        return "Unknown"
    if restart >= threshold:
        return "RestartStorm"
    if phase == "Running":
        return "Ready" if all_ready else "NotReady"
    if phase == "Failed":
        return "NotReady"
    return "Unknown"


def _extract_items(ctx: str, deploys_doc: Any, pods_doc: Any, threshold: int) -> list[dict]:
    pods = [p for p in (pods_doc.get("items") or []) if isinstance(p, dict)]
    items: list[dict] = []

    for pod in pods:
        meta = pod.get("metadata") or {}
        name, ns = meta.get("name"), meta.get("namespace")
        if not name or not ns:
            continue  # 连逻辑名都拼不出——不编造条目
        status = pod.get("status") or {}
        phase = status.get("phase")
        if phase == "Succeeded":
            continue  # 已完结，非活跃 workload
        restart, all_ready, waiting_reason, messages = _pod_container_view(pod)
        state = _pod_state(phase, restart, all_ready, waiting_reason, threshold)
        # pod 的 replicas_ready 语义映射为"就绪容器数/总容器数"；无容器状态 → None
        cs_list = status.get("containerStatuses") or []
        ready_repr = (
            f"{sum(1 for cs in cs_list if cs.get('ready'))}/{len(cs_list)}"
            if cs_list
            else None
        )
        detail = None
        if state != "Ready":
            detail = scrub("; ".join(messages)[:300]) if messages else None
        items.append(
            {
                "context": ctx,
                "namespace": ns,
                "workload": name,
                "kind": "pod",
                "replicas_ready": ready_repr,
                "state": state,
                "restart_count": restart,
                "detail": detail,
            }
        )

    deploys = [d for d in (deploys_doc.get("items") or []) if isinstance(d, dict)]
    for dep in deploys:
        meta = dep.get("metadata") or {}
        name, ns = meta.get("name"), meta.get("namespace")
        if not name or not ns:
            continue
        spec = dep.get("spec") or {}
        status = dep.get("status") or {}
        desired = spec.get("replicas")
        ready = status.get("readyReplicas")
        unavailable = status.get("unavailableReplicas")
        if not status or desired is None or ready is None:
            state = "Unknown"  # 读不到就绪度——不冒充 Ready，也不冒充 0/N
            ready_repr = None
        else:
            state = "Ready" if ready == desired and not unavailable else "NotReady"
            ready_repr = f"{ready}/{desired}"
        # 前缀启发式（Deployment→RS→Pod 命名规约）：聚合子 pod 重启数，仅展示用
        restart_sum = 0
        for p in pods:
            pm = p.get("metadata") or {}
            pname, pns = pm.get("name") or "", pm.get("namespace")
            if pns == ns and pname.startswith(name + "-"):
                pr, _, _, _ = _pod_container_view(p)
                restart_sum += pr
        items.append(
            {
                "context": ctx,
                "namespace": ns,
                "workload": name,
                "kind": "deployment",
                "replicas_ready": ready_repr,
                "state": state,
                "restart_count": restart_sum,
                "detail": None,
            }
        )

    items.sort(key=lambda i: (i["context"], i["namespace"], i["kind"], i["workload"]))
    for i in items:
        assert i["state"] in STATES, f"state out of closed set: {i['state']!r}"
    return items


def _summary(items: list[dict], threshold: int) -> dict:
    return {
        "workloads": len(items),
        "not_ready": sum(1 for i in items if i["state"] == "NotReady"),
        "crash_loop": sum(1 for i in items if i["state"] == "CrashLoopBackOff"),
        # 风暴判据：state 命中，或 pod 的 restart_count 达阈值（CrashLoopBackOff 叠加事实）
        "restart_storm": sum(
            1
            for i in items
            if i["state"] == "RestartStorm"
            or (i["kind"] == "pod" and i["restart_count"] >= threshold)
        ),
        "unknown": sum(1 for i in items if i["state"] == "Unknown"),
    }


def _storm_threshold(override: int | None) -> int:
    if override is not None:
        return int(override)
    raw = os.environ.get(STORM_ENV, "")
    try:
        return int(raw) if raw else DEFAULT_STORM_THRESHOLD
    except ValueError:
        return DEFAULT_STORM_THRESHOLD


def collect(
    *,
    timeout: int = 25,
    restart_storm_threshold: int | None = None,
    runner: Callable[..., dict] | None = None,
) -> dict:
    """纯函数核心：runner 可注入（测试 fake kubectl JSON），生产走 io_.run_cmd_json。

    每 context 独立探测（channel=k8s:<ctx>）；单 context 任一条子命令失败 → 该
    context 记 failed（部分成功的读数也不采信，避免半幅视图冒充健康），其余
    context 照常出真实 items。
    """
    as_of = utc_now_iso()
    executor = runner or run_cmd_json
    threshold = _storm_threshold(restart_storm_threshold)
    errors: dict[str, ChannelError] = {}
    items: list[dict] = []

    raw_ctxs = os.environ.get(CONTEXTS_ENV, "").strip()
    if not raw_ctxs:
        err = ChannelError(
            CONFIG_CHANNEL, "not_configured", f"env {CONTEXTS_ENV} is unset"
        )
        return envelope(
            "workloads_health",
            [],
            _summary([], threshold),
            channels=channel_status({err.channel: err}, [err.channel]),
            as_of=as_of,
        )

    contexts = [c.strip() for c in raw_ctxs.split(",") if c.strip()]
    binary = os.environ.get(BINARY_ENV) or DEFAULT_BINARY
    probed: list[str] = []
    for ctx in contexts:
        channel = f"k8s:{ctx}"
        probed.append(channel)
        try:
            deploys_doc = executor(
                _deployments_argv(binary, ctx), channel=channel, timeout=timeout
            )
            pods_doc = executor(
                _pods_argv(binary, ctx), channel=channel, timeout=timeout
            )
        except ChannelError as e:
            errors[channel] = e
            continue
        except subprocess.TimeoutExpired:
            # 防御性兜底：注入式 runner 直接抛 TimeoutExpired 时同样归因 timeout
            errors[channel] = ChannelError(
                channel, "timeout", f"kubectl get exceeded {timeout}s"
            )
            continue
        items.extend(_extract_items(ctx, deploys_doc, pods_doc, threshold))

    return envelope(
        "workloads_health",
        items,
        _summary(items, threshold),
        channels=channel_status(errors, probed),
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(
        name="workloads_health",
        description=(
            "逐 workload（deployment/pod）非就绪、CrashLoopBackOff、重启风暴、"
            "镜像拉取异常、状态未知，逐条点名（只读 kubectl get；仅逻辑名，"
            "零凭据零端点）"
        ),
    )
    def _tool(timeout: int = 25) -> dict:
        return collect(timeout=timeout)
