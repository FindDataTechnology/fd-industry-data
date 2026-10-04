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
4. **通知通道**（2026-10-04 核）：spider-heal 绑定通道当前 `test-channel`；换正式有硬前置——
   正式接收方需先在群里/微信向目标 bot（qinfa 或选定 bot）**发一条任意消息**（bot 见过会话才能绑定），
   之后萬星侧一条命令完成绑定+重部署（配方在 paas `docs/spider-heal-pack.md`）。
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
   部署键 sub2api id 31 逐请求记账）；git PAT 实测权限超出契约（可见 12 仓 admin）——
   建议重新签发仅 fd-industry-data、Contents RW + Pull requests RW 的 fine-grained PAT，
   萬星一条命令轮换（SECRET_GIT_PAT 省略其余即保留）。

## 演练配方（task 5.2，避开平台滚动窗口）

1. 构造两张工单（结构层：把某单元选择器改坏；网络层：指向不可达域名的临时单元），
   落 `reports/health-tickets/` 并 SUBMIT；
2. 观察 STATUS：结构层应经真回合（≤20min 预算）出 `pr-open`，网络层应出代理复测标注、
   不产代码 diff；
3. 核对：profile 生命周期事件齐（submitted/status）、通知到达、PR 仅差目标单元；
4. 人审 merge → 补 schedule 点亮 → 同一 Idempotency-Key 重放验证不产生第二笔账。

## 已知限制

- 平台滚动窗口内 bot relay 503 时会丢一条通知（单次纪律不重试）——重要工单避开部署窗口。
- 一致性判定所需 `crawl_runs` 列名以运维实况为准：默认 SQL 见 `config.py.telemetry_sql`，
  可用 `FD_HEALTH_CONFIG` 覆盖（如 `rows_written`/`error_summary`/`http_status` 命名不同）。
- Console 设置页（配置面板化）属 panel 仓改动，另行安排；当前配置经 vars/config 文件生效。

## 权限边界（不可协商）

永不 merge、永不写 schedule、不改工单目标单元以外文件、不逆向反爬；
密钥全链路脱敏（工单/PR/日志至多尾四位）。