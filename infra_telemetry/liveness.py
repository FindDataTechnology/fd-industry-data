"""③ services_liveness — 按 PORTS 台账口径的服务 TCP 探活（spec: infra-telemetry-surface R2 第③类）。

三态读出口径：open / timeout / refused / unreachable —— 探活失败是**服务的读数**，
不是通道故障；通道故障只限台账文件（ports_file）与 probe_host 配置失败
（``not_configured``，禁默认猜内网地址）。

台账解析：只取 NodePort 表格行（第一格为可选粗体的纯数字 + 第二格 ns/name +
第三格容器端口），跳过说明文字行、表头、合并端口行（``30874 / 31408``）与
冻结端口等异型表；台账里的 host/IP（如 ``100.64.0.1``）绝不进返回体（R4 零端点）。

探针注入式：collect(probe) 生产走 socket.create_connection，测试传假表；
probe 抛出的网络异常（OSError 系）一律归 unreachable 态，通道仍 ok。

reason 分类约定与 io_ 一致（envelope.REASONS 单一事实源）。纯 stdlib。
"""
from __future__ import annotations

import os
import re
import socket
from typing import Callable

from .envelope import ChannelError, channel_status, envelope, scrub, utc_now_iso

CHANNEL_PORTS_FILE = "ports_file"
CHANNEL_PROBE_HOST = "probe_host"
PROBED = [CHANNEL_PORTS_FILE, CHANNEL_PROBE_HOST]

STATES = ("open", "timeout", "refused", "unreachable")
DEFAULT_TIMEOUT_S = 3

_ENV_PORTS_FILE = "FD_TELEMETRY_PORTS_FILE"
_ENV_PROBE_HOST = "FD_TELEMETRY_PROBE_HOST"
_ENV_TIMEOUT_S = "FD_TELEMETRY_PROBE_TIMEOUT_S"

# 第一格：可选粗体后的纯数字（**31852** / 31851）
_BOLDS = "*"
# 第二格：k8s 逻辑名 ns/name（ASCII，防把说明文字/主机名当服务名）
_NS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
# 第三格：容器端口（纯数字或 80→8000 改写形态；排除 "5000 / 8080" 合并行与说明列）
_CONTAINER_PORT = re.compile(r"\d+(?:→\d+)?")


def parse_ports(text: str) -> list[dict]:
    """从 PORTS.md 全文提取 NodePort 行 → [{service, node_port, container_port}]。

    逐行：仅 ``|`` 开头的表格行；第一格去粗体后须纯数字，第二格须 ns/name，
    第三格须容器端口形态，否则视为表头/分隔/说明/异型表跳过。按
    (service, node_port) 去重，保留首次出现。
    """
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        port_cell = cells[0].strip(_BOLDS)
        if not port_cell.isdigit():
            continue
        service = cells[1].strip(_BOLDS)
        if not _NS_NAME.fullmatch(service):
            continue
        container_port = cells[2].strip(_BOLDS)
        if not _CONTAINER_PORT.fullmatch(container_port):
            continue
        key = (service, int(port_cell))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {"service": service, "node_port": int(port_cell), "container_port": container_port}
        )
    return rows


def socket_probe_factory(timeout_s: int) -> Callable[[str, int], str]:
    """生产探针：TCP connect。返回三态；本函数自身不抛（异常已在内部分类）。"""

    def probe(host: str, port: int) -> str:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_s):
                return "open"
        except (TimeoutError, socket.timeout):
            return "timeout"
        except ConnectionRefusedError:
            return "refused"
        except OSError:
            return "unreachable"

    return probe


def _resolve_timeout_s(explicit: int | None, env: str) -> int:
    for candidate in (explicit, os.environ.get(env, "")):
        if candidate is None or candidate == "":
            continue
        try:
            v = int(candidate)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return DEFAULT_TIMEOUT_S


def collect(
    *,
    probe: Callable[[str, int], str] | None = None,
    timeout_s: int | None = None,
    ports_file_env: str = _ENV_PORTS_FILE,
    probe_host_env: str = _ENV_PROBE_HOST,
    timeout_env: str = _ENV_TIMEOUT_S,
) -> dict:
    """纯函数核心：probe 可注入（测试假表），生产走 socket_probe_factory。

    通道故障（ok=False）只来自 not_configured：台账未设/文件不存在、probe_host
    未设（绝不默认猜内网地址）。单服务探活失败（含 probe 抛网络异常）是读数，
    记 unreachable 态，通道仍 ok。
    """
    as_of = utc_now_iso()
    errors: dict[str, ChannelError] = {}

    path = os.environ.get(ports_file_env, "")
    if not path or not os.path.isfile(path):
        # 不入 detail 具体路径（可能含内网信息），只报 env 名与判定
        errors[CHANNEL_PORTS_FILE] = ChannelError(
            CHANNEL_PORTS_FILE, "not_configured", f"env {ports_file_env} unset or ledger file not found"
        )
    host = os.environ.get(probe_host_env, "")
    if not host:
        errors[CHANNEL_PROBE_HOST] = ChannelError(
            CHANNEL_PROBE_HOST, "not_configured", f"env {probe_host_env} is unset — refusing to guess a probe host"
        )

    if errors:
        summary = {
            "services": 0,
            "open": 0,
            "timeout": 0,
            "refused": 0,
            "unreachable": 0,
            "note": "channel failed — no data claimed",
        }
        return envelope(
            "services_liveness",
            [],
            summary,
            channels=channel_status(errors, PROBED),
            as_of=as_of,
        )

    resolved_timeout = _resolve_timeout_s(timeout_s, timeout_env)
    runner = probe or socket_probe_factory(resolved_timeout)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        errors[CHANNEL_PORTS_FILE] = ChannelError(
            CHANNEL_PORTS_FILE, "upstream_unreachable", f"ledger file unreadable ({type(e).__name__})"
        )
        return envelope(
            "services_liveness",
            [],
            {
                "services": 0,
                "open": 0,
                "timeout": 0,
                "refused": 0,
                "unreachable": 0,
                "note": "channel failed — no data claimed",
            },
            channels=channel_status(errors, PROBED),
            as_of=as_of,
        )

    items: list[dict] = []
    counts = {"open": 0, "timeout": 0, "refused": 0, "unreachable": 0}
    for row in parse_ports(text):
        try:
            state = runner(host, row["node_port"])
        except (OSError, TimeoutError):
            # 注入探针抛网络异常 = 该服务读数不可达，不是通道故障
            state = "unreachable"
        if state not in STATES:
            state = "unreachable"
        counts[state] += 1
        # items 只带逻辑名与端口：台账 host/IP、probe host 绝不出现在返回体
        items.append({"service": row["service"], "node_port": row["node_port"], "state": state})

    summary = {
        "services": len(items),
        "open": counts["open"],
        "timeout": counts["timeout"],
        "refused": counts["refused"],
        "unreachable": counts["unreachable"],
    }
    if not items:
        # 区分"台账没登记"与"登记了但全挂"：0 行时明确标注，不误报全挂
        summary["note"] = "ledger has 0 NodePort rows — nothing registered, not an outage"
    return envelope(
        "services_liveness",
        items,
        summary,
        channels=channel_status({}, PROBED),
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(
        name="services_liveness",
        description="按 PORTS 台账口径逐服务 TCP 探活（open/timeout/refused/unreachable；只读，零端点，台账 0 行如实标注）",
    )
    def _tool(timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
        # 参数缺省值 3 与 env 缺省一致：值为 3 时传 None，让 FD_TELEMETRY_PROBE_TIMEOUT_S 生效；
        # 显式传非 3 值即覆盖 env。
        return collect(timeout_s=None if timeout_s == DEFAULT_TIMEOUT_S else timeout_s)
