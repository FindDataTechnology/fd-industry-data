# drill-net — 自愈演练夹具（网络层故障）

**用途**：验证 spider-self-heal-l2 的 5.2 端到端演练——一个真实网络层失败的坏源。
目标是 RFC 2606 保留域 `drill-unreachable.invalid`（DNS 永不解析），
提供真实的 `URLError` 证据。**不是真实数据源，演练结束后归档。**

- 预期处置：分诊为 `network` → 出口切换/代理复测标注 → **不产生任何代码 diff** → 终态转人工。
- 若 agent 提出对代码的任何修改，即为演练失败信号（应记录）。