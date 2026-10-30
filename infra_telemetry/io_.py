"""通道 IO 助手：psql 子进程 + HTTP JSON + 命令超时归因（纯 stdlib）。

reason 分类约定（envelope.REASONS 单一事实源）：
- 配置缺失（env 未设/文件不存在）      -> not_configured
- 连不上（DNS/拒绝/二进制缺失/非 2xx） -> upstream_unreachable
- 认证失败（401/403/密码认证）          -> unauthorized
- 子进程/请求超时                       -> timeout
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from .envelope import ChannelError

PSQL_TIMEOUT = 30


def run_psql(dsn_env: str, sql: str, *, channel: str, timeout: int = PSQL_TIMEOUT) -> list[list[str]]:
    """psql 子进程 TSV 输出解析（-A -F '\\t'），与 health/inspector.py 同路径。"""
    dsn = os.environ.get(dsn_env, "")
    if not dsn:
        raise ChannelError(channel, "not_configured", f"env {dsn_env} is unset")
    binary = os.environ.get("FD_TELEMETRY_PSQL", "psql")
    if shutil.which(binary) is None:
        raise ChannelError(channel, "upstream_unreachable", f"{binary} not found")
    try:
        proc = subprocess.run(
            [binary, dsn, "-At", "-F", "\t", "-c", sql],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ChannelError(channel, "timeout", f"psql exceeded {timeout}s")
    if proc.returncode != 0:
        err = proc.stderr.strip()[:400]
        low = err.lower()
        if "password authentication failed" in low or "permission denied for" in low:
            reason = "unauthorized"
        elif "could not connect" in low or "connection refused" in low or "no such host" in low:
            reason = "upstream_unreachable"
        else:
            reason = "upstream_unreachable"
        raise ChannelError(channel, reason, err)
    rows = []
    for line in proc.stdout.splitlines():
        if line.strip():
            rows.append(line.split("\t"))
    return rows


def http_json(url: str, *, channel: str, headers: dict[str, str] | None = None, timeout: int = 20) -> dict | list:
    """urllib GET JSON；状态码/网络/超时按约定归因。禁打印 URL 之外的凭据。"""
    if not url:
        raise ChannelError(channel, "not_configured", "base url env unset")
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ChannelError(channel, "unauthorized", f"HTTP {e.code}")
        raise ChannelError(channel, "upstream_unreachable", f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if getattr(e, "reason", None) == "timed out" or "timed out" in str(e).lower():
            raise ChannelError(channel, "timeout", str(e)[:200])
        raise ChannelError(channel, "upstream_unreachable", str(e)[:200])
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ChannelError(channel, "upstream_unreachable", "non-JSON response body")


def run_cmd_json(
    argv: list[str], *, channel: str, timeout: int = 25
) -> dict | list:
    """kubectl 类只读子命令，stdout 为 JSON。argv 由调用方构造（仅允许 get/describe/version 类）。"""
    if shutil.which(argv[0]) is None:
        raise ChannelError(channel, "upstream_unreachable", f"binary {argv[0]} not found")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ChannelError(channel, "timeout", f"{' '.join(argv[:3])} exceeded {timeout}s")
    if proc.returncode != 0:
        err = proc.stderr.strip()[:400]
        low = err.lower()
        if "unauthorized" in low or "forbidden" in low or "authentication" in low:
            reason = "unauthorized"
        elif "connection refused" in low or "no such host" in low or "could not resolve" in low or "i/o timeout" in low or "tls handshake" in low:
            reason = "upstream_unreachable"
        else:
            reason = "upstream_unreachable"
        raise ChannelError(channel, reason, err)
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        raise ChannelError(channel, "upstream_unreachable", f"{argv[0]} returned non-JSON")
