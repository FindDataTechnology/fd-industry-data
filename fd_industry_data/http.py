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

import time
import urllib.parse
import urllib.request
import urllib.robotparser

__all__ = ["SAME_HOST_GAP_S", "throttle", "allowed_by_robots"]

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
