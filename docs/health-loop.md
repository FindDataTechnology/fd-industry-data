# 自愈闭环运维手册（L2 · spider-self-heal-l2）

> 契约与决策：openspec change `spider-self-heal-l2`、ADR `docs/adr/0001`、
> 萬星侧契约 `paas/docs/spider-heal-pack.md`。

## 闭环全景

```
GHA health-inspect（xinru-server1，每日 03:17 CST；只读中央库 crawl_runs）
  → 分诊（fd_industry_data/health/triage.py，五类确定性规则）
  → 工单落仓 reports/health-tickets/<date>-<src>-<id>.yaml
  → SUBMIT（Bearer 调用键）→ 萬星 spider-heal Agent Service（30min 节奏，单回合 20min 预算）
  → agent 修复 → 验证链（scripts/health_verify.py）全绿 → 推分支开 PR
  → 人审 merge → 人补 schedule 点亮（自动路径永不 merge/点亮）
  → STATUS 回读 → 工单生命周期与终态同步（巡检尾巴自动做）
```

## 枚举（与萬星契约一致）

- 分诊类别 `category`：`network`（换出口/代理复测，不改代码）/ `structure`（定向修 parser）/
  `contract`（重侦察数据表面；`suspected_anti_bot` 仅此类的强制标记，禁止逆向）/
  `source-dead`（退役评估）/ `fallback`（整段重生成兜底）。证据不足 → `needs_human`（不猜类别）。
- 终态 `terminal`：`fixed-pending-human` / `sample-update-pending-human` / `manual` /
  `retire-suggested` / `queued`（未终态含 None）。

## 模块与 CLI

| 组件 | 位置 | 说明 |
|---|---|---|
| 工单 schema | `fd_industry_data/health/ticket.py` | 校验/读写/生命周期（只追加） |
| 分诊 | `fd_industry_data/health/triage.py` | 纯规则、可单测 |
| 巡检器 | `fd_industry_data/health/inspector.py` + `scripts/health_inspect.py` | 出单/去重/日限额/SUBMIT/STATUS |
| 口径守卫 | `fd_industry_data/health/guard.py`、`samples.py` | golden 重放 + 序列比对 + 失败分流 |
| 验证链入口 | `scripts/health_verify.py` | 工单 verify 字段指向的命令 |
| 配置 | `fd_industry_data/health/config.py` | 默认值 + `FD_HEALTH_CONFIG` JSON 覆盖 |

常用命令：

```bash
# 离线预演（不读库不提交）
python3 scripts/health_inspect.py --snapshot /tmp/runs.json --dry-run
# 验证链（agent 执行的原样命令）
python3 scripts/health_verify.py --ticket reports/health-tickets/<f>.yaml
# 离线子集（跳过网络与 gate，用于本地冒烟）
python3 scripts/health_verify.py --ticket <f> --skip-network --skip-gate
```

## 运维配置（一次性，已落地 2026-10-04）

1. **self-hosted runner（k8s/ArgoCD 标准通道）**：清单事实源
   `fd-infra-deploy/all-services/prod/fd-health-runner.yaml`（cheap 集群 fd-prod，
   ArgoCD 自动同步）→ Pod `fd-health-runner`（镜像 `ccr.ccs.tencentyun.com/finddata/fd-health-runner:sha-*`，
   构建=fd-industry-data 仓 `health-runner-image` workflow → hkccr → cheap-3 tcr-relay 回灌 ccr）。
   名称 `cheap-health-runner`，labels `fd-health`。注册状态在 PVC `fd-health-runner-data`（`/data`），
   重建免 token；换注册 = 删 PVC + 更新 `fd-health-runner-token` secret + rollout restart。
2. **secrets（GHA）**：`FD_CENTRAL_PG_DSN`（中央库**只读**账号 fd_health_ro，经 tailscale mesh
   100.64.0.3:30432）、`WANXING_SPIDER_HEAL_KEY`（finddata 调用键，已在萬星允许清单）。
3. **集群内 secret（不进 git）**：`tcr-ccr`（ccr 拉取凭据）、`fd-health-proxy`（mihomo 出口
   `100.64.0.7:30081`，cheap 节点无 GitHub 直连；NO_PROXY 含 mesh 与 finddatatech 域）、
   `fd-health-runner-token`（一次性注册 token）。
4. **通知通道（✅ 2026-10-04 完成，萬星侧）**：正式通道 `fd-ops` 已绑定并随 spider-heal v4
   重部署生效（NOTIFY_CHANNEL=fd-ops，runner drain 热切换）；验证消息 relay 实录 `fd-ops | sent`
   且微信对话可见。`test-channel` 保留为同会话备用绑名（要清可去函）。
   **命名坑（排障勿被带偏）**：微信里显示名「qinfa」的公众号 = 平台里名为 **test** 的 bot
   （appId wx655b…，实际在用）；平台里另有一只名为 qinfa 的 bot（wx2983…）从未使用。
5. **限流/总闸 MCP（✅ 2026-10-04 双向闭环）**：
   - **坐标**：中央库 `fd_open_data.public.health_config`（key/value jsonb 6 键 +
     updated_at；只读账号 `fd_health_ro`，mesh `100.64.0.3:30432`，DSN 经安全渠道、不进 git）。
   - **萬星侧落地**：只读 MCP shim（cheap1 容器 `fd-health-mcp`，tailnet `:8090`，
     registry 条目 `fd-health-config`，pack v3 `mcpServers: ["fd-health-config"]`；
     运维配方与三坑见 paas `servers/fd-health-mcp/README.md`）。
   - **技能行为**：spider-heal-notify 每巡检调 `health_config_get` 对比本地
     `gate-state.json`，键值变化发一条 `gate_change`（首次只落盘；MCP 不可达记一句不重试）。
   - **闸门执行语义**：总闸关闭时我方巡检器在源头停止 SUBMIT（出单照旧，天然排空队列）；
     agent 侧负责变更通知与展示。
   - **验证记录（2026-10-04）**：本侧经 mesh 直连 shim `health_config_get` 返回真库值（独立复现）；
     总闸 false→true **双向翻转演练**两次手动 run 均绿、`last-run.master_switch` 随库实时变化
     （agent 侧 gate_change 落 test-channel，换绑正式通道后可见）。
   - 过渡期等价手段 = `POST /api/packs/<pack>/deployments/spider-heal/pause|resume`。
   - 巡检器配置优先级：`--config`/`FD_HEALTH_CONFIG`（文件）> 中央库表 > 默认值
     （last-run.json `config_source` 可见来源：`file:` / `db:health_config` / `defaults`）。
6. **万星侧其余状态**（2026-10-04 交接）：runner 默认模型/计费已核验（deepseek-v4.1-flash、
   部署键 sub2api id 31 逐请求记账）；git PAT **已重签为窄权限并前置验证通过**（finddata 侧独立核验：目标仓 Contents+PR 双写实测 ✓；
   非目标仓（fd-cn-report/platform/fd-daas-mcp/fd-vertical-packs）写权 403 全拒 ✓；私有仓不可见 ✓；
   探针物已清理）——新 PAT 身份 = FindDataOfficial（id 295187081），轮换时建议同步 gh_actor 为
   `295187081+FindDataOfficial@users.noreply.github.com`；**轮换确认后请吊销旧 PAT**（旧 token 属 scs001、
   含 12 仓 admin 面：GitHub→Settings→Developer settings→Fine-grained tokens→Revoke）。
   轮换命令 = PACK_ID + SECRET_GIT_PAT + SECRET_GH_ACTOR（通道/计费省略 = 保留 fd-ops 与现绑）。
   **轮换已实证生效（2026-10-04）**：agent 侧自验——登录名 `FindDataOfficial`、`push=true`、
   全程未回显凭据 → **剩余 = 旧 PAT（scs001，12 仓 admin）吊销（用户）**。
   轮换核验同时带回两项萬星侧待办：① spider-heal 技能读 `.credentials.yaml` 需按 YAML 块标量
   解析 `git_pat: >-`（单行 sed 会读成 `>-` 误判「凭据未配置」→ 误转人工）；② 聊天类回合
   `$DSH_HOME/spider-heal/` 状态目录不可创建（票务回合可写）——请萬星确认 gate-state/inbox
   更新是否仅发生在票务回合。

## 演练配方（task 5.2，避开平台滚动窗口）

1. 构造两张工单（结构层：把某单元选择器改坏；网络层：指向不可达域名的临时单元），
   落 `reports/health-tickets/` 并 SUBMIT；
2. 观察 STATUS：结构层应经真回合（≤20min 预算）出 `pr-open`，网络层应出代理复测标注、
   不产代码 diff；
3. 核对：profile 生命周期事件齐（submitted/status）、通知到达、PR 仅差目标单元；
4. 人审 merge → 补 schedule 点亮 → 同一 Idempotency-Key 重放验证不产生第二笔账。

## 演练记录

- **2026-10-04 · 5.2 端到端真修复演练（全链通过）**：注入结构层坏源（`drill-heal`，解析面失配）
  与网络层坏源（`drill-net`，保留域真实握手超时）→ agent 结构层回合内完成根因分诊 → 定向修复
  → 验证链全绿（golden/真取数/manifest/gate）→ 开出 **PR #1** → 人审（diff 仅目标单元 +6/-6）
  → squash merge → 合并后 `health_verify` verdict=ok（原红值转绿）；网络层正确判为 `network`：
  代理复测标注、**零代码 diff**、终态 `manual`。夹具已归档 `archive/drill-spider-self-heal-20261004/`；
  工单生命周期（含 `human-review`/`human-merged` 事件）为永久记录。
- 演练当场揪出并修复两个真 bug（已回归）：① 终态工单被反复重投 + 失败判定把回执里
  「引述历史错误」误判为失败；② **门面 Idempotency-Key 重放首答**——首投撞上 agent
  不可用而缓存 `-32032`，此后同键重投永远拿到旧错误 → 幂等键改按次递增（`<ticket>-tryN`），
  工单级去重由 agent inbox（按路径）兜底。

## 已知限制

- 平台滚动窗口内 bot relay 503 时会丢一条通知（单次纪律不重试）——重要工单避开部署窗口。
- 门面在上游忙时返回 JSON-RPC `-32032 fetch failed`（投递侧忙语义）：我方留痕并下轮重试即可；
  已闭环工单的重投会拿到「已见过」回执后停止。
- 终态枚举已含 `closed`（已关闭：人工合并或人工裁决后闭环，**仅人工路径可置入**）；巡检器自动把
  agent 汇报的 `merged` 映射为 `closed`（`TERMINAL_MAP`）；`done` 仍映射 `fixed-pending-human`
  （修复完成待人工）。首个 closed 实例 = 演练单 `20261004-drill-structure-00000001`。
- Console 设置页（配置面板化）属 panel 仓改动，另行安排；当前配置优先级=文件 > 中央库 > 默认。

## 权限边界（不可协商）

永不 merge、永不写 schedule、不改工单目标单元以外文件、不逆向反爬；
密钥全链路脱敏（工单/PR/日志至多尾四位）。