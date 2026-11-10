# 自愈闭环运维手册（L2 · spider-self-heal-l2）

> 契约与决策：openspec change `spider-self-heal-l2`、ADR `docs/adr/0001`、
> 萬星侧契约 `paas/docs/spider-heal-pack.md`。

## 闭环全景

```
GHA health-inspect（xinru-server1，每日 03:17 CST；只读中央库 crawl_runs）
  → 分诊（fd_industry_data/health/triage.py，六类确定性规则）
  → 工单落仓 reports/health-tickets/<date>-<src>-<id>.yaml
  → SUBMIT（Bearer 调用键）→ 萬星 spider-heal Agent Service（30min 节奏，单回合 20min 预算）
  → agent 修复 → 验证链（scripts/health_verify.py）全绿 → 推分支开 PR
  → 人审 merge → 人补 schedule 点亮（自动路径永不 merge/点亮）
  → STATUS 回读 → 工单生命周期与终态同步（巡检尾巴自动做）
```

## 枚举（与萬星契约一致）

- 分诊类别 `category`：`network`（换出口/代理复测，不改代码）/ `source-revision`
  （疑似源改版：源可达但端点结构变化——字段/选择器/golden/schema 漂移；先取证再改 parser）/
  `structure`（本地解析器残余错误，定向修 parser）/
  `contract`（重侦察数据表面；`suspected_anti_bot` 仅此类的强制标记，禁止逆向）/
  `source-dead`（退役评估）/ `fallback`（整段重生成兜底）。证据不足 → `needs_human`（不猜类别）。
  `network` 与 `source-revision` 严格互斥：超时/5xx/DNS/连接拒绝永远判 `network`。
- 工单 evidence：`last_success`（最近成功时间）与 `watermark`（水位=最新数据期 + 口径来源；
  遥测不提供数据期时 `latest: null` 且 basis 如实标 unknown）。
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
- **STATUS 回读同款病与治法（2026-10-12）**：门面按 (caller, agent, key) 重放首答、窗口 **24h**。
  STATUS 原用固定键 `status-<ticket>` → 一次坏答（空文本/思考答，解析不出 state）被重放满窗口：
  实证三张 10-11 单（flower-auction/inmetals/metal-com）自 09:31 起每轮回读都是那份坏答，
  直到次日 09:31 窗口过期才拿到真回合、当轮收敛 `manual`。治法=**键随工单生命周期递增**
  （`status-<ticket>-r{N}`，N = 已有 status/status-failed 事件数 + 1；lifecycle 只追加，
  序号严格递增永不碰撞）。STATUS 是读——每次都要当前状态，好答也不该重放（否则
  pr-open/done 的可见性要等窗口过期）。同时回读超时 **120→300s**（`STATUS_READ_TIMEOUT`；
  实测回合 20~150s+，曾见 180s 超时）。回归：`test_status_idem_key_unique_per_call`、
  `test_status_idem_key_increments_with_lifecycle`、`test_status_read_timeout_relaxed`。
- **预算标定（2026-10-12 同日实证）**：唯一键的代价是每次回读都走真回合。当日一轮
  3 新单（SUBMIT 各 ≈2-5 分钟）+ 3 存量单回读跑满 **19m54s**，撞穿旧的 20 分钟上限被取消
  （回执全丢）。三处配套：①工作流 `timeout-minutes` 20→**45**；②回执提交步骤加
  **`if: always()`**（取消时保住已得进度）；③`STATUS_MAX_PER_RUN=8` 单轮回读上限 +
  **最久未读优先**（`_last_read_at` 排序，不会饿死）——积压超过 8 张时分多轮推进。
  回归：`test_status_budget_cap_prefers_stalest`。

## 生成流（kind=generate，source-generation-flow）

让 agent 按单**从零新建**一个新源单元（greenfield），与修复流共用同一套工单/验证/PR/人工门。

**工单格式**（`scripts/health_new_generation_ticket.py` 生成，或手写）：

```yaml
kind: generate                  # repair（缺省）| generate
unit: spiders/nmc-weather/      # 允许指向尚不存在的新 slug（^[a-z0-9][a-z0-9-]*$）
category: null                  # 生成不属故障分诊，必须为空
brief:
  source_urls: ["https://www.nmc.cn/rest/weather?stationid=Wqsps"]
  expectations: "取气象站实况：温度/湿度/天气描述/风速；建议行字段 station/temperature/..."
  cadence: hourly               # 只写 brief，绝不写 schedule
  notes: "侦察簿 W2-B：/rest/province 枚举站码；9999 为缺测占位；UA 必带"
golden: ["spiders/nmc-weather/golden/"]   # 声明即强制：样本缺失=验证红
verify:
  commands: ["python3 scripts/health_verify.py --ticket reports/health-tickets/<f>.yaml"]
```

**出单工具**：

```bash
python3 scripts/health_new_generation_ticket.py --slug nmc-weather \
  --source-url "https://www.nmc.cn/rest/weather?stationid=Wqsps" \
  --expectations "取气象站实况：温度/湿度/天气描述" --cadence hourly \
  --notes "侦察簿 W2-B：站码内部短码；9999 缺测"
```

**agent 技能（`spider-heal-generate`）应做的步骤**（=给萬星的需求书）：
1. 读单：`brief.source_urls/expectations/notes`；`unit` 是新目录，仓库里读不到现状。
2. 从 `templates/new-source/`（spider.py/manifest.yaml/CHECKLIST.md）起接新单元；参考同类在役单元
   （README 的接入档案、manifest functions/columns、check_manifest_commands 约定 `run_<slug>(limit)`）。
3. 实现取数 + **交付 ≥1 个 golden 样本**（断言必须跨期稳定：避开日期/波动数值，参照
   `spiders/metal-com/golden/` 格式；样本缺失验证链判红）。
4. 跑 `health_verify`（工单 verify 字段声明）直到 `verdict: ok`：golden 重放 + 真取数 ≥1 行 +
   manifest 校验 + conformance gate。
5. 开 PR（分支 `gen/<slug>`），PR 描述附验证链证据；**不写 schedule、不点亮、不 merge**。

**硬边界**：巡检器只产修复单（生成是业务决定，仅人工/工具出单）；产物 PR 前双 lint/gate 必绿；
静默合入与人工门原样适用；首个生成演练源拟定 `nmc.cn`（侦察簿 ★接：国内直连、纯 JSON）。

## 判据增量（2026-11-08，add-flower-vertical）

### 规则 1c：成功但写入行数低于声明下限

- **判据**：源在 `spiders/<slug>/manifest.yaml` 里**显式声明** `min_rows_per_run: N` 时，
  **最近一次** `status=success` 的 `rows_written < N` → `structure` 类工单，
  action=「核对解析是否只读到表头/空表」。**先于规则 1 的健康短路**判（否则「跑成功、
  时间新鲜、只是没写出来」会被判健康）。
- **只看最近一次**（2026-11-09 修，`declare-health-min-rows`）。原实现扫回看窗内**任意一次**
  成功，于是历史上一次低产会让该源天天重出单，直到那条 run 滑出窗口——给 38 个 A 桶源
  声明下限时实测有 10 个会因此上线当天就误报，而它们最近一次都正常。历史低位现在只进
  trace 当诊断信息（"窗口内另有 N 次低于下限"），工单会在下一次正常运行时自然消解。
- **未声明 = 不生效**（零回归）。理由：0 行的含义因源而异——节假日无新增、幂等 upsert
  无改动都可能合法，只有源自己知道「一次至少该写出几行」。
- **实证动机**：`flower-auction` 有 4 次 `success` / `rows_written` 合计 0，健康级此前
  完全无信号（零行成功只在错误文本命中结构异常时才被看见）——板上一直空、巡检全绿。
- **运行上限要记下来**（2026-11-09，任务 7.1）：`runner_cli` 把本轮 `--limit` 写进
  `crawl_runs.metrics.limit`。否则 `rows_written=20` 分不清是"上游只有 20"还是"我们只要了 20"——
  2026-11-06 有 48 个源各跑一次、每次恰好 20 行（全部 `image_tag='unknown'`，即未走 k8s 调度），
  就是靠这个才看清是批量小 limit 而不是数据塌陷。判定侧尚未使用该字段（历史 run 没有）。
- **声明前必须先有该源的日常运行行数样本**。花卉线只给 `aiph-stats` 声明了 20
  （2025 版年鉴实测 81 行）；斗南只有一次回补样本（932 行），日常运行写多少行未测，故不声明。

### 记入册但**不在本 health 层**的三级

`health/` 只判「这条源还活着吗」。花卉线另有三级在同一份真源里，脚本与文档见
`scripts/flower_alerts.py` + `docs/flower-alerts-runbook.md`：

| 级 | 判什么 | 关键区分 |
|---|---|---|
| 发布节奏级（`fd_industry_data/publication.py`） | 上游该发的发了吗 | `awaiting_publication`（我们跑到了、上游没发）vs `stream_broken`（我们断了）；冷启动 `window_learning` 不判 |
| 阈值级 | 读数越过配置阈值 | 边缘触发 + 冷却 + 恢复；`insufficient_data` **不改状态**（否则一次缺数发假恢复） |
| 事件级 | 披露日历 | 一条申报=一个事件，幂等键 `ticker|form|filed|accession` |

新增健康级缺口（排期在册却整窗零运行、跨源缺席对账）见 `inspector.reconcile_absent()` 与
`absent_source_policy`（默认 `report` = 零回归）。

### 已知隐患：`dom≥29` 的 cron 在该月不存在那天时永不触发，而巡检不告警（2026-11-10 实测）

8 个源把 `schedule` 写成"固定 29/30/31 日"。这些日子在某些月份不存在，CronJob
**不补偿**——那个月一次都不跑，且没有任何信号。

口径：按**真实日历**（2026 年 2 月 = 28 天，闰年与否逐个算，不用"每月 30 天"的名义日历）；
时区按 CST（生产 CronJob 未声明 `timeZone`，见 `industry-cron-runs-in-cst-not-utc`）。
复算脚本：`scripts/cron_dom_coverage.py`（`--year`、`--json`）。

#### 2026 年（非闰年）实际触发日期

| dom | 触发日 | 次数 | 缺失月 | 最大间隔 |
|---|---|---|---|---|
| 29 | 01-29 03-29 04-29 05-29 06-29 07-29 08-29 09-29 10-29 11-29 12-29 | 11 | **2 月** | 59 天 |
| 30 | 01-30 03-30 04-30 05-30 06-30 07-30 08-30 09-30 10-30 11-30 12-30 | 11 | **2 月** | 59 天 |
| 31 | 01-31 03-31 05-31 07-31 08-31 10-31 12-31 | **7** | **2/4/6/9/11 月** | 61 天 |

#### 误报率（修正后）

"误报率"定义：把该源按**月频**判超期（容忍窗 336h × 2.5 = 840h = 35 天）时，
一年里"距上次触发 > 35 天"的天数占比。**触发日本身不计入**（当天已经跑了）。

| source | cron | dom | 触发/年 | 2 月 | 最大间隔 | 超窗天数 | 误报率 |
|---|---|---|---|---|---|---|---|
| `jp-estat` | `53 4 31 * *` | 31 | 7 | 0 | 61 天 | 123 | **33.7%** |
| `un-comtrade` | `52 4 30 * *` | 30 | 11 | 0 | 59 天 | 23 | 6.3% |
| `usda-psd` | `8 4 30 * *` | 30 | 11 | 0 | 59 天 | 23 | 6.3% |
| `census-eits` | `32 4 29 * *` | 29 | 11 | 0 | 59 天 | 23 | 6.3% |
| `census-m3` | `48 4 29 * *` | 29 | 11 | 0 | 59 天 | 23 | 6.3% |
| `ecb-exr` | `4 5 29 * *` | 29 | 11 | 0 | 59 天 | 23 | 6.3% |
| `ember` | `1 4 29 * *` | 29 | 11 | 0 | 59 天 | 23 | 6.3% |
| `hf-datasets` | `56 5 29 * *` | 29 | 11 | 0 | 59 天 | 23 | 6.3% |

#### 闰年对照（2028）：dom=29 的 5 个源完全免疫

| dom | 非闰年（2026/2027） | 闰年（2028） |
|---|---|---|
| 29 | 11 次/年，2 月缺，59 天，**6.3%** | **12 次/年**，31 天，**0.0%** |
| 30 | 11 次/年，59 天，6.3% | 11 次/年，60 天，6.6% |
| 31 | 7 次/年，61 天，33.7% | 7 次/年，61 天，33.9% |

**这条对照是重点**：`dom=29` 的 6.3% 是"非闰年才有的病"——闰年完全正常。只看某一年
会得出相反结论，同一个 cron 的错误率**随年份漂移**。这是"判据挂在会漂的刻度上"的又一例
（本场此前四例：日期判时刻、max 当截断点、max 估计 1% 分位、`written: 0` 当没出单）。

#### 为什么巡检现在看不见

这 8 个源 `data_period` 全是 `irregular`，`freshness.cycles_since_last_good` 对 inf
周期直接跳过超期判定。于是「两个月没跑」与「本来就一年跑两次」在巡检面上**完全同形**：
当前误报 0，**检出也 0**。

实测佐证：`jp-estat` 90 天只跑 **3 次**（最近 2026-11-06，属批量探针那批），`rows=87`。

#### 判据建议（不改 cron、不改 freshness.py，只加一道可算的闸）

> **`irregular` 是"数据更新不规则"，不等于"调度可以永不触发"。**
> 把 cron 按真实日历展开到未来 12 个月，逐月统计触发次数；若某月为 **0 次**，
> SHALL 报一条**提示**（不是工单）：「YYYY-MM 按 cron 展开为 0 次」。
> `dom` 不受影响的源天然 12/12 全非零，不会被这条打扰。

实现落点：`scripts/cron_dom_coverage.py` 已能按年展开，扩成 12 个月逐月计数即可。

**本次不改**：cron 的日号可能是业务意图（月末快照），改它要先问清口径；`freshness.py`
是另一会话当天在改的文件，此时插入会撞车。**先把事实与判据记下来。**

### 已知隐患：`freshness_shadow` 按 UTC 解释 cron，生产按 CST（2026-11-10 实测）

`health/freshness.py` 的 `_zone()` 在 `tz` 缺省时返回 **UTC**；而生产 CronJob 未声明
`timeZone` 时按**控制器本地（CST）**解释 cron —— 两者差 8 小时。

实测范围：**123 个有 `schedule` 的源，`schedule_tz` 全部未声明**，所以这个偏差覆盖全部在册源。

**当前不构成危害**，因为它是 shadow（影子）：

| 检查 | 结果 |
|---|---|
| 消费者 | `shadow_freshness()` → `Plan.freshness_shadow` → 摘要 `freshness_shadow` / `freshness_shadow_alerting` |
| 影响出单？ | **否**——`triage.py` 不读它，`emit` 只看 `classify()` |

**转正前必须先修**：一旦 `freshness_shadow` 真的用于判定，"欠了几个周期"会整体偏 8 小时，
那时它会变成真实的误报/漏报。修法二选一——让 `_zone()` 缺省按 CST，或在生成侧把
`schedule_tz: Asia/Shanghai` 显式写进 manifest（后者更彻底，但 123 份要逐个加）。

写在这里是因为它容易被当成"shadow 就没关系"而一路带下去。

### 已知缺陷（待修）：`--dry-run` 没有覆盖 `--sync-status`

`--dry-run` 只挡住 **SUBMIT**，没挡住 **STATUS 回读**，所以"干跑"仍会真的打门面。

判据在 `fd_industry_data/health/inspector.py:1221-1228`（`if not config.master_switch` / `elif dry_run` / `else`）：

```python
if not config.master_switch:
    entry["reason"] = "master switch off (inspect-only)"   # 总闸关 → 跳过
elif dry_run:
    entry["reason"] = "dry-run"                            # dry-run → 跳过 poster
else:
    ... poster(url, key, idem, body)                       # 真投递
```

而 `sync_status()`（定义在 `inspector.py:1344`，函数体内 `dry_run` 出现 **0 次**，已实测 `grep -c` 为 0）——它逐张回读
未终态工单的 STATUS，每张都走网络且受 `STATUS_READ_TIMEOUT` 约束，单轮上限
`STATUS_MAX_PER_RUN`。于是 `--dry-run --sync-status` 的实际效果是"不投递、但仍然逐张
问门面"。

**时长证据（2026-11-10 实测）**：

| 运行 | Deliver 步骤耗时 | 说明 |
|---|---|---|
| run 42799574292（真实巡检，成功） | **999s** | 正常投递 + STATUS 回读 |
| run 42817406652（`dry_run=true` 手动触发） | **1150s 时仍未结束** | 已确认 submit 被挡住（`elif dry_run` 短路 `poster`），时间几乎全花在 STATUS 回读上；读数取自 21:00 CST，Deliver 自 12:41:03Z 起 |

两者同量级，说明**干跑并没有真的"干"**。

**为什么这不是回归**：这两个数字都来自 `--dry-run` 只挡一半的既有行为，
与当天新增的「观察表追加 + 四项指纹」步骤无关——那一步只 `--from-pg` 读库并写一个
markdown 文件，不碰 `poster` / `submit` / 工单，且在 Deliver 之前就已 `success` 结束。

**要修就一句话**：`sync_status()` 加一个 `dry_run` 形参，`--dry-run` 时直接返回
（或只返回"本应回读 N 张"，不真发请求）。

**暂不改**：它影响生产投递路径（`--deliver --submit-new --sync-status` 是每日巡检的
主链路），改错会让 STATUS 回读静默停摆、工单停在旧状态等下一轮——那是比干跑变慢贵得多的
故障。改前需要一次真跑对照。

#### 具体改法（待授权，尚未实施）

三处，全是加法，不改既有请求逻辑：

```python
# 1) inspector.py:1344 — 签名加 dry_run（必须默认 False，理由见下）
def sync_status(outdir: Path, url: str, key: str, fetcher=None,
                max_reads: int = STATUS_MAX_PER_RUN,
                dry_run: bool = False) -> list[dict]:
    ...

    # 2) 收集完 pending、真正发请求之前短路
    if dry_run:
        # pending 的三元组是 (_last_read_at(doc), path, doc)：首元素只是排序键，
        # 第二元素才是 Path。别写成 (rel, p, doc) —— 实测形状见 inspector.py:1356/1361。
        return [{"ticket": path.name, "skipped": True, "reason": "dry-run"}
                for _, path, _ in pending]

    # 3) 原有请求逻辑一行不动
```

```python
# scripts/health_inspect.py:158 — 把已有的 args.dry_run 传下去
summary["status"] = inspector.sync_status(outdir, url, key, dry_run=args.dry_run)
```

**默认值必须是 False**：主链路 `--deliver --submit-new --sync-status` 不带 `--dry-run`，
默认值一变就把日常巡检的 STATUS 回读关掉；而"回读没跑"在步骤结论上仍然是绿的。

#### 对照验证步骤（A/B）

**A 组 · 干跑确实变干（性能侧，容易过）**

1. `gh workflow run health-inspect.yml --ref main -f dry_run=true`
2. 量 Deliver 耗时：
   ```bash
   gh run view <id> --json jobs -q \
     '[.jobs[].steps[]|select(.name|test("Deliver"))|((.completedAt|fromdateiso8601)-(.startedAt|fromdateiso8601))]|.[0]'
   ```
3. 判据：**从 999s 量级降到秒级（≤30s）**。若仍是数百秒，说明短路没生效或还有别的网络路径。

**B 组 · 真跑的回读没被关掉（功能侧，比 A 重要）**

1. 改后跑一次**不带** `dry_run` 的巡检（等自然 cron，或直接 `gh workflow run health-inspect.yml --ref main`）
2. **判据不能只看步骤绿**——"探针通过"与"探针根本没跑"在步骤结论上完全同形（本仓
   2026-11-08 栽过一次）。要看实物，`sync_status` 的唯一产物是工单 lifecycle 里的
   `status` 事件：
   ```bash
   # 改前先记基线
   grep -l "$(date -u +%Y-%m-%d)" reports/health-tickets/*.yaml \
     | xargs grep -l "event: status" 2>/dev/null | wc -l
   ```
3. 改后该数字 **≥ 基线且不为 0**；为 0 说明回读停了，必须回滚。
   **改前基线（2026-11-10 实测）= 24**（当天有 `status` 事件的工单数），命令已验证可跑。

**红线**：A 绿 + B 红 = 修坏了，回滚。宁可干跑慢，不可回读停。

### 同一事实的三处信号：`rows < min_rows_per_run` 会发生什么

一个源写了行数低于声明的下限，会同时惊动三层。**它们不是重复机制，但关系一直没人写下来**——
新接手的人看到 dispatcher 说 `done`、巡检却出 `structure` 单，很容易以为中间有 bug。

```
crawl 写出 rows=20，manifest 声明 min_rows_per_run: 25
                    │
     ┌──────────────┼──────────────────────────┐
     ▼              ▼                          ▼
① runner       ② dispatcher               ③ 巡检器
   status=        pending=done            rule1c → structure 工单
   partial        （不罚身份租借）          "核对解析是否只读到表头/空表"
   metrics.accept.ok=false
```

三者的语义各不相同，合起来不矛盾：

| 层 | 它在回答什么 | 结论 |
|---|---|---|
| runner | 这次爬取是否兑现了源自己声明的承诺 | 没兑现 → `partial` |
| dispatcher | 这个任务执行完了吗、这个账号还能用吗 | 执行完了（`done`）、账号没问题 |
| 巡检器 | 这条源现在还能不能出数 | 不能 → 要人看 |

**为什么要三处都留**：runner 侧让"承诺"在**提交状态前**就生效（否则只是事后记账）；
dispatcher 侧避免因一次数据不足就判任务失败、连带罚掉共享账号；巡检侧是**事后审计**——
它能看见那些没有经过 runner 预检的历史记录，以及其他产品写进同一张表的行。

**唯一需要守的纪律**：三处必须读同一个键、同一个口径。历史上正是这里漂过——
`accept.min_rows` 一度与 `min_rows_per_run` 并存，两边可以给出不同结论。
现在 runner 的 `_min_rows_per_run()` 与巡检的 `min_rows_floor()` 有测试
（`test_same_value_as_the_inspector`）逐字比对 8 种 manifest 写法。

### 三读者三来源：改动"生效"到什么程度，取决于谁在读（2026-11-09 实测）

同一份 manifest，三个消费者从**三个不同的地方**读，所以"改完就生效"这句话必须拆开说：

| 读者 | 代码来源 | manifest 来源 | 备注 |
|---|---|---|---|
| 巡检器（GHA，每晚 03:17） | 每次 `git clone` **GitHub** `main` | 同左（仓库内 `spiders/`） | 推到 GitHub 即生效 |
| 爬虫 runner（每条源的 k8s Job） | **镜像**（`ENTRYPOINT fd-runner` 是 `pip install .` 装的 console script；实测 `os.getcwd() in sys.path` 为 **False**，所以 `/content` 下克隆的那份不会被 import） | initContainer 每 Job `git clone` **gitee**，`sparse-checkout spiders/ fd_industry_data/ universes/`，`FD_CONTENT_DIR=/content/spiders` | manifest 走 gitee；**代码要重建镜像** |
| 本地人工 | 工作树 | 工作树 | — |

判据（2026-11-09 核对 `k8s/cronjob-template/templates/cronjob.yaml`）：

* `_manifest_of()` / `_accept_block()` 的 `roots` 里 **`FD_CONTENT_DIR` 排第一**，
  所以爬虫读的是 Job 启动时从 gitee 克隆的那份 → 声明类改动**推到 gitee 即对爬虫生效**；
* 但 `fd_industry_data` 的**行为**（runner 代码）来自镜像 → 代码类改动必须重建 runner 镜像；
  `/content/fd_industry_data` 存在只是为了少数单元在模块作用域 `import fd_industry_data.universes`
  时能解析到 `universes/`，不是让整体代码走克隆。

据此，"某个改动生效了没有"要分别回答：**巡检视角**（GitHub）、**爬虫数据视角**（gitee）、
**爬虫行为视角**（镜像）。三者可以同时不同步。

### 巡检摘要 `last-run.json` 的字段契约（2026-11-09 扩充）

下游读者（`scripts/alert_selfcheck.py`、人）只看几项，扩字段是安全的，但要知道现在有哪些：

| 字段 | 含义 |
|---|---|
| `healthy` / `written` / `deferred` | 判健康数 / 本轮落单 / 顺延 |
| `deferred[]` | 每条含 `source`、`reason`、**`category`、`rule`**（2026-11-09 加：只记 `source+reason` 时"顺延 7 个"看不出严重程度，上限挡住的东西等于不可见） |
| `skipped_unknown_period` | 有 manifest 但定不出周期的源（**不含**没 manifest 的） |
| `unregistered_sources` | **有运行记录、但本仓没有 manifest、也不在 `excluded_sources`** 的源（2026-11-09 加，原先混在 `skipped_unknown_period` 里静默跳过） |
| `absent_sources` | 在册但遥测窗内零运行 |
| `master_switch` / `config_source` / `at` / `source` | 总闸、配置来源、时间、读取方式 |

**2026-11-10 变更**：`alert_selfcheck.py` **不再读 `last-run.json`**（该文件已移出 git，
见 `.gitignore`）。它改为从**工单 YAML 的 `lifecycle`** 里取 `submitted` / `submit-failed`
事件的 `http_status`——那才是同一事实的原始记录，而工单仍在 git 里。

判定口径不变：**最近一批**投递（相邻 SUBMIT 间隔 ≤30 分钟）里整批 401/403 才算"链断了"；
个别单失败属业务问题不报。取"最近一批"而不是全部历史，是因为 2026-11-07 那次整批 401
已定性为路由搬迁，把历史算进来会永久报警。

它仍然不看 `deferred`，所以上面两次扩字段没有改变它的判断。

### 巡检面 ≠ 交付面（2026-11-09 实测，务必记住）

巡检每晚 `git clone --depth=1` **GitHub** 的 `main`（`36d5836`，127 个 `spiders/` 目录）；
而本仓的交付落在 **gitee**（`7d94f1b`，163 个目录）。GitHub 是目录子集，差 36 个，
其中 34 个来自单个提交 `c1daa91`「批次六交付（34 个新单元）」——**从未推上 GitHub**。

后果与判据：

- 只在 gitee 的源在巡检眼里**没有 manifest** → 归入 `unregistered_sources`（以前混在
  `skipped_unknown_period` 里静默跳过，谁也不知道）。实测 11 个，逐个算 verdict 后**今天全部健康**，
  所以代价是覆盖率而不是信号。
- 以 manifest 为载体的声明（`min_rows_per_run`、`data_period`）**只有在 GitHub 上才生效**。
  同一窗口对照：GitHub 面 healthy=84、gitee 面 healthy=89，84 与生产 `last-run.json` 完全一致。
- `config/health-scope.json` 另外手工排除了 17 个跨仓特殊 Job（law 线/适配器/observer），
  它们走 `report_special_run` 兜底面——**那是有意识的边界，不是漏配**。
  新源若既无 manifest 又未排除，会出现在 `unregistered_sources` 里，需要人登记或排除。

## 已知限制

- 平台滚动窗口内 bot relay 503 时会丢一条通知（单次纪律不重试）——重要工单避开部署窗口。
- 门面在上游忙时返回 JSON-RPC `-32032 fetch failed`（投递侧忙语义）：我方留痕并下轮重试即可；
  已闭环工单的重投会拿到「已见过」回执后停止。
- 终态枚举已含 `closed`（已关闭：人工合并或人工裁决后闭环，**仅人工路径可置入**）；巡检器自动把
  agent 汇报的 `merged` 映射为 `closed`（`TERMINAL_MAP`）；`done` 仍映射 `fixed-pending-human`
  （修复完成待人工）。首个 closed 实例 = 演练单 `20261004-drill-structure-00000001`。
- Console 设置页（配置面板化）属 panel 仓改动，另行安排；当前配置优先级=文件 > 中央库 > 默认。
- **年频源在遥测 30 天窗内可能「整窗零运行」**：`inspector.reconcile_absent()` 已把这种源
  与「该跑却没跑」分开（`period-longer-than-window` vs `no-run-in-window`），
  但 `absent_source_policy` 默认 `report` —— 只登记、不出单。要出单须显式改 `ticket`。
- **阈值级/发布节奏级/事件级不在本层**：`health/` 只回答「源活着吗」，另三级见
  `scripts/flower_alerts.py` 与 `docs/flower-alerts-runbook.md`；**没有任何调度**在跑它们（挂账）。

## 权限边界（不可协商）

永不 merge、永不写 schedule、不改工单目标单元以外文件、不逆向反爬；
密钥全链路脱敏（工单/PR/日志至多尾四位）。