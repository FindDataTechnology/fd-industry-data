"""把 spider 的 urllib 出口接到 proxy-service 的 forwarder（acquire/release 契约）。

**为什么要它**：`marketing-signals` 的三个源（TED / World Bank / NFRA）在集群内
直连是 `ENETUNREACH`——TED 走 CloudFront，本网络到不了。平台早就有出口层
（`fd-proxy-service`：forwarder 持有节点出口 IP，按 `(source, addr_id)` 记电路），
但本服务从来没接上，于是每个源都跑满 45s 预算后返回 `source_failures`。

**为什么不用 `fd_open_data_mcp.proxy.injection`**：那个包是本仓**未声明**的依赖
（`pyproject.toml` 里没有，镜像里也没装——`import fd_open_data_mcp` 直接
ModuleNotFoundError）。契约本身只有两个 POST，照文档实现比引入一条未声明依赖可控。
若将来把它声明成依赖，这个模块可以整体换掉。

契约（见 `fd-proxy-service/README.md`）：

    POST {FD_PROXY_FORWARDER}/acquire {source}        -> {upstream_url, addr_id, provider}
    POST {FD_PROXY_FORWARDER}/release {source, addr_id, provider, outcome}

`upstream_url` 形如 `http://user:pass@host:port`（HTTP CONNECT 转发代理）。
**ships-dark**：`FD_PROXY_FORWARDER` 未设（本地/测试）或 forwarder 不可用 ⇒
`acquire` 返回 `None`，一切按直连走，行为与接线前一致——不会把本地开发打挂。

注入方式是**给 urllib 打补丁**（不是 requests/httpx）：本仓 150+ 个 spider 都
直接调 `urllib.request.urlopen`，逐个改不现实；而补丁在**调用时**读 contextvar，
每个线程/任务各读各的，因此并发安全（与 `injection.py` 里 requests 补丁同构）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

#: 打补丁前的原版 urlopen。forwarder 自己的调用必须走它，否则会拿代理去连代理。
_ORIGINAL_URLOPEN = urllib.request.urlopen

#: 当前上下文使用的代理 URL；None = 直连。
_proxy_url: ContextVar[Optional[str]] = ContextVar("fd_egress_proxy_url", default=None)
_installed = False


def forwarder_base() -> str:
    return (os.environ.get("FD_PROXY_FORWARDER") or "").rstrip("/")


def current_proxy_url() -> Optional[str]:
    return _proxy_url.get()


def _post(path: str, payload: dict, timeout: float = 5.0) -> Optional[dict]:
    """打 forwarder。**永远用原版 urlopen**——它不该被自己注入的代理拦一道。"""
    base = forwarder_base()
    if not base:
        return None
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("PROXY_CONTROL_TOKEN")
    if token:
        headers["X-Management-Token"] = token
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with _ORIGINAL_URLOPEN(req, timeout=timeout) as resp:  # noqa: S310 - 显式配置的主机
            return json.loads(resp.read())
    except Exception:  # noqa: BLE001 - 取不到就直连，不在这里判死
        return None


def acquire(source: str) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """(upstream_url, addr_id, provider)。取不到 ⇒ (None, None, None)，即直连。"""
    if not forwarder_base():
        return None, None, None
    doc = _post("/acquire", {"source": source}) or {}
    url = doc.get("upstream_url")
    if not url:
        return None, None, None
    return url, doc.get("addr_id"), doc.get("provider")


def release(source: str, addr_id: Optional[int], provider: Optional[str],
            outcome: str) -> None:
    """回写电路结果。**尽力而为**：失败不影响本次取数。"""
    if addr_id is None:
        return
    _post("/release", {"source": source, "addr_id": addr_id,
                       "provider": provider, "outcome": outcome})


@contextmanager
def via_proxy(source: str):
    """块内的 urllib 调用走该 source 的出口；出了块恢复直连并回写电路结果。

    `outcome` 只区分成功/失败两类（forwarder 侧据此调电路健康度）：
    块内抛异常 ⇒ `fail`，否则 `ok`。
    """
    url, addr_id, provider = acquire(source)
    if url is None:
        yield None
        return
    token = _proxy_url.set(url)
    ok = False
    try:
        yield url
        ok = True
    finally:
        _proxy_url.reset(token)
        release(source, addr_id, provider, "ok" if ok else "fail")


def install() -> None:
    """给 `urllib.request.urlopen` 打补丁（幂等）。

    补丁行为：当前上下文有代理 ⇒ 用一个带 ProxyHandler 的 opener 发请求；
    没有 ⇒ 原样调用（**不改变直连语义**）。
    """
    global _installed
    if _installed:
        return
    if getattr(urllib.request.urlopen, "_fd_egress_patched", False):
        _installed = True
        return

    def patched(url, data=None, timeout=None, **kwargs):  # noqa: ANN001 - 与标准库同形
        px = _proxy_url.get()
        if px is None:
            if timeout is None:
                return _ORIGINAL_URLOPEN(url, data)
            return _ORIGINAL_URLOPEN(url, data, timeout=timeout, **kwargs)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px}))
        if timeout is None:
            return opener.open(url, data)
        return opener.open(url, data, timeout=timeout, **kwargs)

    patched._fd_egress_patched = True  # type: ignore[attr-defined]
    urllib.request.urlopen = patched  # type: ignore[assignment]
    _installed = True
