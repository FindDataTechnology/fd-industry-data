"""marketing-signals FastMCP server —— 营销信号只读面（spec marketing-signal-scout）。

工具面闭集声明处 = ``TOOL_NAMES``（与 registry 在册面 / pack allowTools 三方对账锚点，
沿用 edu_catalog/server.py 与 infra-telemetry 的装配纪律）。

**只读**：两个工具都不写库、不发对外请求之外的副作用。它们调既有 spider 取数
（`collect()` 只归一，取数仍归 spider），然后返回归一后的 Signal。

Entry: ``python -m fd_industry_data.marketing_signal_server``（stdio）

硬边界（spec marketing-signal-scout「信号只喂选题与人工判断，绝不自动触达」）：
本面**没有**任何发送/触达工具，且 MUST NOT 增加。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from fastmcp import FastMCP

from .marketing_signal import USABLE_SOURCES, Signal, by_entity, collect

#: 工具面闭集（registry 在册面与 pack allowTools 必须与之逐名一致）
TOOL_NAMES: tuple[str, ...] = ("signals_recent", "signals_by_entity")


def _load_spider(name: str, rel: str) -> Any:
    """按路径加载 spider 模块（不改 spider、不 import 它的包）。"""
    root = Path(__file__).resolve().parents[1]
    path = root / rel
    spec = importlib.util.spec_from_file_location(f"_sig_spider_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spider {rel}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _default_fetchers() -> dict[str, Callable[[int], list[dict]]]:
    """生产取数通道：三个已验证源各调自己的既有 spider。

    做成可注入的，是因为测试不该触网；生产装配才真正加载 spider。
    """
    fetchers: dict[str, Callable[[int], list[dict]]] = {}

    def _ted(limit: int) -> list[dict]:
        return _load_spider("ted", "spiders/ted-notices/spider.py").run_ted_notices(limit=limit)

    def _wb(limit: int) -> list[dict]:
        return _load_spider("wb", "spiders/worldbank-procnotices/spider.py").run_worldbank_procnotices(
            limit=limit
        )

    def _nfra(limit: int) -> list[dict]:
        return _load_spider("nfra", "spiders/nfra-penalty/spider.py").run_nfra_penalty(limit=limit)

    fetchers["ted-notices"] = _ted
    fetchers["worldbank-procnotices"] = _wb
    fetchers["nfra-penalty"] = _nfra
    return fetchers


def _serialize(s: Signal) -> dict:
    return {
        "source": s.source,
        "entity": s.entity,
        "event": s.event,
        "date": s.date,
        "line": s.line,
        "capability": s.capability,
        "signal": s.compact(),
        "detail": s.detail,
    }


def build_server(
    fetchers: dict[str, Callable[[int], list[dict]]] | None = None,
    error_hook: Callable[[], dict | None] | None = None,
) -> FastMCP:
    mcp = FastMCP(
        name="marketing-signals",
        instructions=(
            "营销信号只读面：signals_recent 按统一时间窗取最近信号，"
            "signals_by_entity 按实体聚合。每个信号带四要素"
            "（实体 / 事件 / 时点 / 产品面）与紧凑串「类型|日期|来源」。"
            "只陈述可查公开事实，不作推断性评价。"
            "本面没有任何对外触达工具——信号只喂选题与人工判断。"
        ),
    )
    get_fetchers = fetchers if fetchers is not None else _default_fetchers

    #: 每个源的取数总预算（秒）。spider 自己的单请求超时是 45s，但一个源可能
    #: 发多页/多分片，串起来能把一次 tools/call 拖到三分钟以上（实测：墙内取
    #: TED + 世行两个境外源时，MCP 调用 180s 未返回）。预算到了就放弃该源并
    #: 如实记 failure —— 这仍是「通道故障 ≠ 没有信号」的口径，只是给了上限。
    per_source_budget = float(os.getenv("MARKETING_SIGNALS_SOURCE_BUDGET_SEC", "45"))

    def _gather(limit_per_source: int, sources: list[str] | None) -> tuple[list[Signal], list[dict]]:
        f = get_fetchers() if callable(get_fetchers) else get_fetchers
        wanted = sources or list(USABLE_SOURCES)
        raw: dict[str, list[dict]] = {}
        failed: list[dict] = []
        for name in wanted:
            if name not in USABLE_SOURCES:
                # 未验证的源如实拒，不静默当空集（与 collect() 的忽略规则一致）
                failed.append({"source": name, "reason": "source not in verified set"})
                continue
            fn = f.get(name)
            if fn is None:
                failed.append({"source": name, "reason": "fetcher not configured"})
                continue
            # 单源预算：在独立线程里跑，超时即放弃（线程无法强杀，但它持有的
            # 只是 urllib 连接，进程退出前会随 GC 释放；本服务是无状态的）。
            box: dict[str, object] = {}

            def _run(_fn=fn, _box=box, _n=limit_per_source):
                try:
                    _box["rows"] = _fn(_n)
                except Exception as e:
                    _box["err"] = f"{type(e).__name__}: {e}"

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(per_source_budget)
            if t.is_alive():
                failed.append({
                    "source": name,
                    "reason": f"budget exceeded ({per_source_budget:.0f}s) — channel may be unreachable",
                })
                continue
            if "err" in box:
                failed.append({"source": name, "reason": str(box["err"])})
                continue
            raw[name] = box.get("rows") or []
        return collect(raw), failed

    @mcp.tool()
    def signals_recent(limit_per_source: int = 10, sources: list[str] | None = None) -> dict:
        """取最近的可用营销信号（默认三个已验证源各 10 条）。"""
        if error_hook and (e := error_hook()):
            return {"status": "channel_failure", **e}
        sigs, failed = _gather(max(1, min(int(limit_per_source), 50)), sources)
        return {
            "status": "ok",
            "count": len(sigs),
            "signals": [_serialize(s) for s in sigs],
            # 空结果与通道故障分开报：failed 非空 ≠ 没有信号
            "source_failures": failed,
            "note": "只陈述可查公开事实；无对外触达能力。",
        }

    @mcp.tool()
    def signals_by_entity(limit_per_source: int = 10, sources: list[str] | None = None) -> dict:
        """按实体聚合最近信号（每个实体按日期倒序）。"""
        if error_hook and (e := error_hook()):
            return {"status": "channel_failure", **e}
        sigs, failed = _gather(max(1, min(int(limit_per_source), 50)), sources)
        grouped = by_entity(sigs)
        return {
            "status": "ok",
            "entity_count": len(grouped),
            "entities": [
                {"entity": k, "signals": [_serialize(s) for s in v]}
                for k, v in grouped.items()
            ],
            "source_failures": failed,
            "note": "只陈述可查公开事实；无对外触达能力。",
        }

    return mcp


def main() -> None:
    """stdio 缺省；``MARKETING_SIGNALS_TRANSPORT=http`` 走 streamable-http
    （与 edu_catalog/server.py 同形状，pod 形态用这个）。"""
    import os

    transport = os.getenv("MARKETING_SIGNALS_TRANSPORT", "stdio")
    if transport == "stdio":
        build_server().run()
        return
    build_server().run(
        transport="http",
        host=os.environ.get("MARKETING_SIGNALS_HOST", "0.0.0.0"),
        port=int(os.environ.get("MARKETING_SIGNALS_PORT", "8412")),
    )


if __name__ == "__main__":
    main()


# ── 自检：工具面闭集与硬边界 ────────────────────────────────────────────────


def _selftest() -> int:
    import asyncio
    import json

    fails: list[str] = []

    fake = {
        "ted-notices": lambda n: [{"buyer": "HSE", "date": "2026-11-18", "title": "Arch"}],
        "nfra-penalty": lambda n: [
            {"party": "兴业银行股份有限公司", "violation": "数据安全", "date": "2026-11-18"},
            {"party": "某银行员工张三", "violation": "受贿", "date": "2026-11-18"},
        ],
    }
    mcp = build_server(fetchers=fake)

    def _payload(r):
        c = r.content if hasattr(r, "content") else r
        return json.loads(c[0].text if isinstance(c, list) else str(c))

    async def run():
        tools = await mcp.list_tools()
        names = sorted(t.name for t in tools)
        if names != sorted(TOOL_NAMES):
            fails.append(f"工具面与 TOOL_NAMES 不一致：{names}")
        # 硬边界：MUST NOT 有触达类工具
        for t in names:
            if any(k in t.lower() for k in ("send", "publish", "reach", "outreach", "message")):
                fails.append(f"出现触达类工具：{t}")

        d = _payload(await mcp.call_tool("signals_recent", {}))
        ents = [s["entity"] for s in d["signals"]]
        if "某银行员工张三" in ents:
            fails.append("自然人未被过滤")
        if not any("兴业银行" in e for e in ents):
            fails.append("机构被误删")
        # 通道缺配与空结果必须分开报
        if not d["source_failures"]:
            fails.append("缺配源未被如实报为 failure")

        d3 = _payload(await mcp.call_tool("signals_recent", {"sources": ["samr-food-inspection"]}))
        if not d3["source_failures"] or "verified" not in str(d3["source_failures"]):
            fails.append("未验证源未被拒")

        # 单源预算：慢源必须被放弃并记 failure，不能拖死整个调用。
        # 这条钉住 in-pod 实测的形状（境外源 180s 未返回）。
        import os as _os
        _os.environ["MARKETING_SIGNALS_SOURCE_BUDGET_SEC"] = "1"
        try:
            slow = build_server(fetchers={
                "nfra-penalty": lambda n: __import__("time").sleep(10) or [],
            })
            t0 = __import__("time").time()
            d4 = _payload(await slow.call_tool("signals_recent", {"sources": ["nfra-penalty"]}))
            took = __import__("time").time() - t0
            if took > 3:
                fails.append(f"慢源未被预算拦住（耗时 {took:.1f}s）")
            if not any("budget" in str(f) for f in d4["source_failures"]):
                fails.append("超预算源未被如实记为 failure")
        finally:
            _os.environ.pop("MARKETING_SIGNALS_SOURCE_BUDGET_SEC", None)

    asyncio.run(run())

    if fails:
        print("SERVER SELFTEST FAILED")
        for f in fails:
            print("  -", f)
        return 1
    print("SERVER SELFTEST OK（工具面闭集 + 无触达工具 + 自然人过滤 + 缺配与空集分离 + 未验证源拒）")
    return 0
