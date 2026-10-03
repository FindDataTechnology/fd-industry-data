<!-- 一屏证据摘要：自愈修复与人工变更共用 -->
## 摘要

<!-- 诊断类别（network/structure/contract/source-dead/fallback）/ diff 概要 -->

## 证据

- 工单：`reports/health-tickets/…`
- health_verify 报告：<!-- 贴 verdict 与各环 ok 摘要，密钥已脱敏 -->
- golden 重放 / 序列一致性：<!-- 样本 id 与结果 -->

## 复核要点

- [ ] 变更仅限工单目标单元（`spiders/<slug>/`）
- [ ] 未包含任何 merge / schedule 点亮动作
- [ ] 无密钥明文（工单/描述/日志至多尾四位）
- [ ] 口径未漂移（golden 不一致时已走「样本更新待人工」而非静默改样本）