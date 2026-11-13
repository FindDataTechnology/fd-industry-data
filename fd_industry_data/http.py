"""http — 共享的请求节流（同源礼貌间隔）。

**为什么存在**：节流逻辑此前在 19 个单元里各写一份（``SAME_HOST_GAP`` /
``_throttle`` / 裸 ``time.sleep``），新单元只能照抄。本模块把它做成平台能力。

用法::

    from fd_industry_data.http import SAME_HOST_GAP_S, throttle

    gate = time.monotonic()
    gate = throttle(gate)          # 保证距上次请求 >= SAME_HOST_GAP_S
    resp = await session.get(url)

约定：``throttle`` 接收并返回 ``time.monotonic()`` 时间戳，调用方持有它——
不用全局状态，避免多协程/多单元互相干扰。

既有单元**不强制迁移**（见 tabular-extraction 规格）；新单元使用本入口。
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

__all__ = [
    "SAME_HOST_GAP_S",
    "throttle",
    "allowed_by_robots",
    "pinned_candidates",
    "fetch_bytes",
]

# 同源请求最小间隔（秒）。0.4s 是仓内既有约定（见 cninfo-operating）：
# 实测无间隔连发时部分站点返回 200 空壳或直接限流。
SAME_HOST_GAP_S = 0.4


def throttle(last_at: float, gap: float = SAME_HOST_GAP_S) -> float:
    """保证距 ``last_at`` 至少 ``gap`` 秒，返回新的时间戳。

    Args:
        last_at: 上次请求的 ``time.monotonic()`` 值。
        gap: 最小间隔（秒），缺省 ``SAME_HOST_GAP_S``。

    Returns:
        本次调用后的 ``time.monotonic()``，调用方应保存并在下次传入。
    """
    elapsed = time.monotonic() - last_at
    if 0 <= elapsed < gap:
        time.sleep(gap - elapsed)
    return time.monotonic()


#: 每个 host 的 RobotFileParser 缓存（进程内）。robots.txt 一天不会变几次，
#: 但一个源一次跑会请求同一 host 几十上百次——不缓存会把 robots.txt 也变成负担。
_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser | None] = {}

#: 我们对外声明的 UA。站点按 UA 分桶给 robots 规则，声明清楚才谈得上"遵守"。
ROBOTS_USER_AGENT = "FindDataIndustryBot/1.0 (+https://www.finddatatech.cloud)"


def allowed_by_robots(url: str, *, user_agent: str = ROBOTS_USER_AGENT,
                      timeout: float = 10.0) -> bool:
    """``url`` 是否被该 host 的 ``robots.txt`` 允许抓取（审计 05 §6）。

    **为什么要有这个**：README 写着「All spiders respect ``robots.txt``」，但此前
    共享取数路径（本模块的 ``throttle``、``runner_cli`` 的调度）**都不读 robots**，
    各单元直接打 ``Fetcher.get``。也就是说那句话是不成立的文档承诺——出合规问题时
    它是最不利的证据。这里补上能力，README 的表述同时改成如实描述。

    失败语义（很重要，别搞反）：
    * ``robots.txt`` **取不到**（404 / 网络错误 / 解析失败）→ **放行**。
      这是 RFC 9309 的口径：没有 robots.txt 就等于没有限制。抓不到不等于禁止。
    * ``robots.txt`` 明确 Disallow 该路径 → **拒绝**。
    * 只对 ``http(s)`` 生效；其它 scheme（file: 等）直接放行。

    用法::

        from fd_industry_data.http import allowed_by_robots
        if not allowed_by_robots(url):
            logger.warning("robots.txt disallows %s; skipping", url)
            continue

    按 host 缓存，同一 host 只取一次 robots.txt。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return True
    host_key = f"{parts.scheme}://{parts.netloc}"

    parser = _ROBOTS_CACHE.get(host_key)
    if host_key not in _ROBOTS_CACHE:
        robots_url = f"{host_key}/robots.txt"
        try:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            # urlopen 带 timeout：某些站点不响应 robots.txt，不能把整轮跑挂住
            with urllib.request.urlopen(robots_url, timeout=timeout) as resp:  # noqa: S310
                rp.parse(resp.read().decode("utf-8", "replace").splitlines())
            parser = rp
        except Exception:  # noqa: BLE001 — 取不到 = 没有限制，放行（见 docstring）
            parser = None
        _ROBOTS_CACHE[host_key] = parser

    if parser is None:
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001 — 解析器自身出错时放行，不因工具故障断抓取
        return True


# ── 解析不可信时的退路：候选 IP + **TLS 当判据** ──────────────────────────
#
# 2026-11-14 实测（tencent 集群）：
#   * ``openapi.twse.com.tw`` 系统解析每次返回不同的无关地址
#     （204.79.197.217 / 45.77.186.255 / 185.45.7.185 / 69.63.186.30 / 205.186.152.122），
#     连上去一律超时，作业以 ``URLError: timed out`` / ``Errno 101`` 收场；
#   * **连 DoH 也不可信**：``https://doh.pub/dns-query`` 同一个域名先给出真地址
#     203.74.220.1，十分钟后给出 162.125.32.5（Dropbox 的地址）。第一次是运气。
#   * 但四个**真地址从集群里全部可达**：TCP 通、TLS 握手成功、HTTP/1.1 200、65KB 真 JSON。
#
# 结论：缺的不是通路，是**可信的名字→地址映射**。所以这里不做解析，只做「候选 + 验证」：
# 拿一组候选 IP 逐个连，**由 TLS 证书校验决定那个 IP 是不是真身**（``server_hostname``
# 仍是原域名，证书链照常校验）。连错 IP 的代价只是握手失败，不会有数据风险。
#
# 候选从哪来：``FD_PIN_HOSTS`` 环境变量，形如 ``host=ip1,ip2;host2=ip3``。
# 故意**不内置任何 IP**：写死等于给自己埋一个到期日，且换个环境就是错的。
# 刷新方式写在 spider 的 docstring 里（用一台解析正常的机器 `dig +short A <host>`）。
def pinned_candidates(host: str, spec: str | None = None) -> list[str]:
    """从 ``FD_PIN_HOSTS``（或显式 ``spec``）里取该 host 的候选 IP 列表。"""
    raw = spec if spec is not None else os.environ.get("FD_PIN_HOSTS", "")
    for entry in str(raw).split(";"):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        name, _, ips = entry.partition("=")
        if name.strip().lower() != host.lower():
            continue
        return [ip.strip() for ip in ips.split(",") if ip.strip()]
    return []


class _PinnedHTTPSConnection(urllib.request.http.client.HTTPSConnection):  # type: ignore[attr-defined]
    """连到**指定 IP**、但用原域名做 SNI 与证书校验的 HTTPS 连接。

    这是「解析不可信」的唯一安全修法：既不放弃证书校验（那等于对中间人敞开），
    也不信任任何解析结果——证书说这个 IP 是真身，它才是。
    """

    def __init__(self, host: str, ip: str, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_ip = ip

    def connect(self):  # noqa: D102 — 语义见类文档
        import socket as _socket

        self.sock = _socket.create_connection((self._pinned_ip, self.port or 443),
                                              self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _pinned_opener(ip: str):
    import ssl as _ssl
    import urllib.request as _ur

    class _Handler(_ur.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(
                lambda host, **kw: _PinnedHTTPSConnection(host, ip, **kw),
                req,
                context=self._context or _ssl.create_default_context(),
            )

    return _ur.build_opener(_Handler())


def fetch_bytes(url: str, *, headers: dict | None = None, timeout: float = 30.0,
                pin_spec: str | None = None, candidates: list[str] | None = None) -> bytes:
    """取 ``url`` 的响应体；解析可疑时按候选 IP 逐个重试。

    与 ``urllib.request.urlopen`` 同语义：HTTP 错误以 ``urllib.error.HTTPError`` 抛出，
    **不被这里吞掉**——「上游 404」和「我们解析坏了」必须是两种不同的失败。

    重试只发生在连接层（``URLError`` / 超时 / 连接错误）且该 host 配了候选 IP 时。
    候选为空即退化成普通 ``urlopen``，不做任何额外动作。
    """
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as first:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not host:
            raise
        ips = candidates if candidates is not None else pinned_candidates(host, pin_spec)
        if not ips:
            raise
        last: Exception = first
        for ip in ips:
            try:
                with _pinned_opener(ip).open(req, timeout=timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError:
                raise
            except Exception as exc:  # noqa: BLE001 — 换下一个候选再试，最后抛最后一个
                last = exc
        raise last
