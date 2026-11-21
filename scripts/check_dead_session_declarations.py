"""声明了却从不使用的 browser session —— 结构性闸（dead-stealth-sessions）。

**为什么立这条闸**（2026-11-22 实测）：164 个 spider 里 19 个声明了
`manager.add("stealth", AsyncStealthySession(...), lazy=True)`，而**没有一处**
传 `session="stealth"`。判断依据不是抽样：逐个文件核过，`cqvip`/`csia`/`fred-data`
里多出来的 `stealth` 字样是 `FetcherSession(stealthy_headers=True)`——
那是 HTTP 头伪装，不是浏览器。

后果：这 19 处声明把 `scrapling[fetchers]`（含 playwright）钉进了 runner 镜像，
而 Chromium 在镜像里占 700MB，为**零个实际用户**存在。

**这条闸测什么**：`manager.add("<name>", <XxxSession>(...))` 声明的每个 session 名，
在同一个文件里必须至少被引用一次（`session="<name>"` 或 `session='<name>'`）。
未被引用的声明即死代码——它要么是漏了接线，要么是早就该删。

**为什么不直接禁止 AsyncStealthySession**：真正需要渲染的 spider 有权声明并**使用**它。
闸禁的是「声明了不用」，不是「用了浏览器」。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


DECL_RE = re.compile(r"""manager\.add\(\s*["'](?P<name>\w+)["']\s*,""")

# 名为 `default` 的 session 是框架的默认取向（`manager.add("default", …)` 等价于
# 「没有指定 session 时用它」），不需要也不应该被显式引用。把它排除在判据外，
# 否则每个正确使用默认 session 的 spider 都会被误报。
FRAMEWORK_DEFAULT = {"default"}


def declared_sessions(src: str) -> list[tuple[int, str]]:
    """[(行号, session 名)] —— 每一处 manager.add("<name>", …) 声明。"""
    return [(i, m.group("name")) for i, line in enumerate(src.splitlines(), 1)
            for m in [DECL_RE.search(line)] if m]


def is_referenced(src: str, name: str) -> bool:
    """声明之外是否还有对它的引用（即真的被当作 session 用）。

    去掉声明行后统计出现次数：>0 说明别处引用了它。
    """
    body = "\n".join(l for l in src.splitlines() if DECL_RE.search(l) is None)
    return bool(re.search(rf"""["']{re.escape(name)}["']""", body))


def find_dead(sources: dict[str, str]) -> list[str]:
    """返回 ["<file>:<line> 声明了 <name> 但从未使用", …]。"""
    dead: list[str] = []
    for path, src in sources.items():
        for line, name in declared_sessions(src):
            if name in FRAMEWORK_DEFAULT:
                continue
            if not is_referenced(src, name):
                dead.append(f"{path}:{line} 声明了 session {name!r} 但从未使用")
    return dead


def spider_sources(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted((root / "spiders").glob("*/spider.py"))}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    srcs = spider_sources(root)
    dead = find_dead(srcs)
    if dead:
        print(f"✗ {len(dead)} 处死声明（声明了却从不使用）：")
        for d in dead:
            print("   ", d)
        print("\n修法：删掉该声明，或在代码里真的用它（`session=\"<name>\"`）。")
        return 1
    print(f"✓ 扫了 {len(srcs)} 个 spider，没有「声明了却不用」的 session")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
