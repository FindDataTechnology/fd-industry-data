"""自愈演练夹具（spider-self-heal-l2 task 5.2）：结构层故障注入。

模拟「站点正常返回 200、解析面失配」：故意使用不存在的标记抽取 `<title>`，
导致 title 恒为空、title_brand 恒为空串。正确修复 = 恢复 `<title>` 提取
（title_brand 在标题包含 "Shanghai Metals Market (SMM)" 时输出 "SMM"）。
演练完成后本夹具应归档（或作为最小冒烟单元保留）。
"""
from __future__ import annotations

import re
import urllib.request

URL = "https://www.metal.com/"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

# 注入的故障：该标记在页面中不存在 → 匹配恒失败
_BROKEN_RE = re.compile(r"<h1x-nonexistent-marker>(.*?)</h1x-nonexistent-marker>", re.S)


def run_drill_heal(limit: int = 5) -> list[dict]:
    req = urllib.request.Request(URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read(300000).decode("utf-8", "replace")
    m = _BROKEN_RE.search(body)
    title = m.group(1).strip() if m else ""
    return [{
        "url": URL,
        "title": title,
        "title_brand": "SMM" if "Shanghai Metals Market (SMM)" in title else "",
        "source": "drill-heal",
    }][:limit]