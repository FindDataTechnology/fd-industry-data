"""自愈演练夹具（spider-self-heal-l2 task 5.2）：网络层故障。

目标域使用 RFC 2606 保留 TLD `.invalid`（永不解析）——提供**真实**的 DNS/连接失败证据，
预期被自愈 agent 识别为 network 类：出口切换/代理复测标注、不产代码 diff、终态转人工。
演练完成后本夹具应归档。
"""
from __future__ import annotations

import urllib.request

URL = "https://drill-unreachable.invalid/"


def run_drill_net(limit: int = 1) -> list[dict]:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return [{"url": URL, "status": resp.status}][:limit]