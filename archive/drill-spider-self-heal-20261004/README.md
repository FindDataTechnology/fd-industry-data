# Drill 归档 — spider-self-heal-l2 task 5.2 端到端真修复演练（2026-10-04）

两个演练夹具（**非真实数据源**）在演练完成后按 README 声明归档于此。

## 夹具

- `spiders/drill-heal` — 结构层故障注入：解析面失配（错误标记抽取 `<title>`），
  golden 断言 `title_brand=SMM`；可修复。
- `spiders/drill-net` — 网络层故障：目标为 RFC 2606 保留域 `drill-unreachable.invalid`，
  提供真实 `URLError: _ssl.c:1039 handshake timed out` 证据；设计上永久失败。

## 演练结果（全链通过）

| 判据 | 结果 |
|---|---|
| 结构层：修复 PR 开出 | ✅ PR #1 `heal/20261004-drill-heal`（+6/-6，仅目标单元；根因分诊=structure、非反爬） |
| 结构层：人审门 | ✅ 人审 diff+证据 → squash merge（2026-10-04T06:19:45Z） |
| 结构层：合并后终验 | ✅ `health_verify` verdict=ok（原红值 `title_brand '' != 'SMM'` 转绿） |
| 网络层：不产代码 diff | ✅ 回执「已入队 → 终态转人工，不产代码 diff、不开 PR」；open PR 空 |
| 网络层：终态 | ✅ `manual` 落库 |
| 生命周期留痕 | ✅ 工单 `reports/health-tickets/20261004-drill-{structure,net}-*.yaml`（含量 `human-review`/`human-merged` 事件） |

## 演练揪出的两个真 bug（均已修复 + 回归）

1. **终态工单被反复重投 + 失败判定误报**：agent 回执正文引述历史 `Invalid Request`
   被误判为失败 → 无限重投。修复：终态工单跳过提交；失败判定只看回执开头/JSON-RPC 错误信封。
2. **门面 Idempotency-Key 重放首答**：结构单首投撞上 agent 不可用、缓存 `-32032 fetch failed`，
   此后同键重投永远被重放旧错误 → 永久卡死。修复：幂等键按次递增（`<ticket>-tryN`），
   工单级去重由 agent inbox（按路径）兜底。

## 后续

- 终态枚举暂缺「closed」态（已合并工单以 `human-merged` 事件留痕）——拟后续小 change 补。
- 如需再做链路冒烟，从本归档或 git 历史恢复任一夹具即可。