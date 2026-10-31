"""infra-telemetry FastMCP server — 五类聚合只读工具面（闭集，spec R2）。

工具面 = {gitops_sync, workloads_health, services_liveness, crawl_summary, fleet_cost}。
完全只读：SHALL NOT 出现任何 mutate 工具（spec R1「写动作不存在」场景的静态保证
由本装配文件把守——新增通道须过 spec 场景评审后才能出现在此，工具面与
registry 在册面按 PATCH /api/servers/<path> 全量换 tool_list 同步）。

Entry: ``python -m infra_telemetry.server``（stdio）
       ``FD_TELEMETRY_TRANSPORT=http FD_TELEMETRY_PORT=8410 python -m infra_telemetry.server``
       （streamable-http；MCP_BEARER_TOKEN 在场则强制校验——fd-open-data-mcp 同款）
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from infra_telemetry import cost, crawl_summary, gitops, liveness, workloads

mcp = FastMCP(
    name="infra-telemetry",
    instructions=(
        "基础设施只读遥测：五个聚合工具，每回合取最少工具。"
        "gitops_sync=GitOps/ArgoCD 逐 app 漂移；workloads_health=deployment/pod 异常；"
        "services_liveness=PORTS 台账探活；crawl_summary=爬取源健康（口径=crawl-run-telemetry）；"
        "fleet_cost=逐岗位 token/成本。"
        "所有返回带 as_of 与逐通道状态；通道故障是显式 reason，不是全绿。"
    ),
)

gitops.register(mcp)
workloads.register(mcp)
liveness.register(mcp)
crawl_summary.register(mcp)
cost.register(mcp)

# 工具面闭集单一声明处：与 registry 在册面/ pack allowTools 三方对账的锚点
TOOL_NAMES = frozenset(
    {"gitops_sync", "workloads_health", "services_liveness", "crawl_summary", "fleet_cost"}
)


def main() -> None:
    transport = os.environ.get("FD_TELEMETRY_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
        return
    import uvicorn

    asgi = mcp.http_app(transport="streamable-http")
    token = os.environ.get("MCP_BEARER_TOKEN", "")

    async def app(scope, receive, send):  # noqa: ANN001 - ASGI shim
        # lifespan 必须直通：FastMCP 的会话管理器在 lifespan 里启动，
        # 拦掉即 initialize 500（2026-10-31 实探）。
        if scope.get("type") == "lifespan":
            await asgi(scope, receive, send)
            return
        if token:
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await _send_401(receive, send)
                return
        await asgi(scope, receive, send)

    async def _send_401(receive, send):  # noqa: ANN001
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"unauthorized"})

    uvicorn.run(
        app,
        host=os.environ.get("FD_TELEMETRY_HOST", "0.0.0.0"),
        port=int(os.environ.get("FD_TELEMETRY_PORT", "8410")),
    )


if __name__ == "__main__":
    main()
