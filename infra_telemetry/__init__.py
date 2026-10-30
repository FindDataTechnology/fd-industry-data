"""infra-telemetry — 基础设施只读遥测 MCP server（add-infra-factory-fleet 组1）。

五类聚合的只读工具面（spec: infra-telemetry-surface）：
  ① gitops_sync      ArgoCD/In-cluster GitOps 的 out-of-sync/degraded 逐 app 状态
  ② workloads_health deployment/pod 非就绪、CrashLoopBackOff、重启计数（逻辑名标识）
  ③ services_liveness PORTS 台账口径的服务探活（存活/超时/不可达三态）
  ④ crawl_summary    中央库 crawl_runs 汇总（最近成功/连续失败/零产出，口径=C1）
  ⑤ fleet_cost      逐集群逐岗位 token 用量与配额（无归因落 other）

硬边界：完全只读；返回体零凭据零端点（全部过 envelope.scrub）；
每次返回带 as-of 与逐通道状态；上游故障显式 reason，禁伪装全绿/空集。

Entry: ``python -m infra_telemetry.server``（FastMCP，streamable-http 或 stdio）
"""
__version__ = "0.1.0"
