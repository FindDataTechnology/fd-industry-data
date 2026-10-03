# 自愈修复执行体 = 萬星 Agent Service（壹座构建部署、账号调用）

spider-self-heal-l2 的修复执行体不做一次性脚本：按萬星 agents 产品线（pack + serving contract → 部署为 a2a Agent Service）构建「爬虫自愈修复 agent」，驻留在壹座账号内，以真实案例完善萬星产品本身（facet cutover 两处隐患即由此实跑发现修复，见 paas 转接文档）。

最终接口（2026-10-03 v2 实跑验收后回传固化，契约全文=paas `docs/spider-heal-pack.md`）：

- pack `KkCie7NlrHluo4LiKPnn0w` · agent `spider-heal` · fd-prod（runner cheap1）
- 调用：`POST https://platform.finddatatech.cloud/api/wanxing/v1/a2a/packs-kkcie7nlrhluo4likpnn0w-spider-heal`，Bearer 调用键（允许清单制）
- 协议：`SUBMIT <repo> <ticket>` / `STATUS <ticket>` / `QUEUE`；SUBMIT 秒级回执（入队/已见过/拒绝），inbox 去重+门面幂等键双层；回合预算 20 分钟硬停转人工
- 节奏：30 分钟自巡检队列 + 外部 SUBMIT 立即入队
- 工单契约：category 枚举 network/structure/contract/source-dead/fallback + verify 验证链命令声明 + golden 路径
- 凭据：git PAT 已绑定（secret `git_pat`，仅推分支；平台存 ws_ 引用，日志尾四位）
- 已验证：真回合 110s、重放幂等单笔计费、STATUS 跨回合读盘、GitHub 出海（cheap1）

## Considered Options

- Platform 目录 agent + cron 自轮询（v1，用户否决：不成产品）
- 自研 ~300 行 repair-agent 循环（拒：重复造运行时）
- ZCode CLI headless（拒：GHA 依赖链不可控）

## Consequences

- finddata 与萬星的耦合收敛为一张 HTTP 契约：调用键是唯一跨服务凭据（允许清单制、可与业务键独立轮换）。
- 限额/通知由平台能力承载：回合预算硬停、部署绑定通知通道（当前 test-channel 待换正式）；已知限制=平台滚动窗口 relay 503 丢通知（单次纪律），重要工单避开部署窗口。
- 限流/总闸若要生效于 agent，需中央库配置注册为 MCP server 并由运营重部署追加引用（契约待办③）。
