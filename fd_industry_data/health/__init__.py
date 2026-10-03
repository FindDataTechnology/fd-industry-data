"""health — 自愈闭环工具包（巡检/分诊/口径守卫）。

模块：
- ticket:    工单 schema、读写与生命周期事件
- triage:    遥测 → 分诊（确定性规则，五类枚举）
- inspector: 巡检器（遥测快照/中央库 → 工单）
- guard:     golden 重放与序列一致性（口径守卫）
- config:    阈值/限流/总闸配置（默认值 + 覆盖）

契约（与萬星 spider-heal Agent Service 对齐）：工单 category 枚举
network/structure/contract/source-dead/fallback；工单含 verify 验证链命令
声明与 golden 路径字段；终态枚举见 ticket.TERMINALS。
"""