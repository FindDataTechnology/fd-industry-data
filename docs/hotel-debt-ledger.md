# 酒店垂直线 — 挂账台账

> change：`add-hotel-vertical` · 任务 7.4 · 清点日期 2026-11-08
> 口径：每条挂账必须写「阻塞原因（实测）」与「解锁条件」；没有解锁条件的项记为**永久不可得**，不许留空。

## 一、数据面挂账（口径不可得，非工程欠账）

| # | 挂账 | 阻塞原因（实测） | 解锁条件 / 处置 |
|---|---|---|---|
| D1 | 中国住宿业增加值 / 住宿业 CPI 分项 | `nbs-stats` 的 `INDICATOR_CATEGORIES` 只有 gdp / price_indices / industrial / population / employment，无住宿业类目；本机 easyquery 403 `UrlACL` | 新增 `accommodation` 类目并实测 zbcode（属新任务） |
| D2 | 美国住宿业月度销售（NAICS 721） | `census-eits` 只声明 `mrts/44X72` 与 `mwts/42`，无 `721`；`CENSUS_API_KEY` 未配置 | 拿 key + 加类目；即便接入也只是零售口径销售额，不是 RevPAR/出租率 |
| D3 | 日本宿泊旅行统计 | 无 `ESTAT_APP_ID`；单元实现只取 `TABLE_INF[0]`，无「按统计表 ID 定向」参数 | 拿 appId + 给单元加定向参数（两个独立阻塞） |
| D4 | BLS 住宿业 CPI 系列 | 单元是 scrapling 索引页爬虫（`START_URLS=["https://www.bls.gov/data/"]`），无系列 ID 参数；`api.bls.gov` 对本机/集群 403 | 加公共 API v1 通道（或走 DBnomics 镜像并接受冻结） |
| D5 | 欧盟住宿价格指数 | HICP `prc_hicp_midx` 的 `coicop=CP11` 与 `CP112` 实测 **404 not available for dissemination**（只有 CP111 餐饮 301 值） | **永久不可得**（上游未发布）；已用 `provenance.pairing` 在概念上声明缺失侧 |
| D6 | 企业侧趋势线（RevPAR/ADR/OCC） | 85 条企业概念实测 1–11 期，全部 <12 期 → 只能 `level_only` 水平对照 | 等 8-K / 港股公告再滚 1–2 季；<24 期不得画趋势 |
| D7 | 民航客运量 | 库内无该口径序列（`pwr.co2.*.aviation` 是碳排，不是客运量）；任何既有车道都不供 | 接民航局/CAAC 数据口（属新源） |
| D8 | 星级饭店客房数 | 唯一载体 `ctha-hotel-report`（协会年报）索引面登录墙，尚未抽取；`mct-star-hotel` 只有「饭店数」没有「客房数」 | 协会站点凭据 或 索引面重开 |
| D9 | 欧盟「纯酒店」口径（NACE I551） | `sts_setu_m` 的 `nace_r2=I551` 实测 **0 值**，只有 I55 住宿业聚合有数（139 值） | **永久不可得**；E1 概念名已固化「住宿业营业额指数」，不许写成酒店营业额 |
| D10 | 2024Q4 之后星级饭店数据 | 系列停更：最新期 2024Q3（2024-12-26），其后精确检索 `totalHits=0` | **永久不可得**（除非上游复更 —— 月探针 CronJob 会捕获） |

## 二、工程性挂账（可做，排期问题）

| # | 挂账 | 现状与影响 | 处置 |
|---|---|---|---|
| E1 | 复合阈值规则不可表达 | 共享评估器只支持 `lt/lte/gt/gte/abs_gte`；「ADR 环比跌幅 / 出游人次连续两期下滑 / 供需缺口扩大」三条写不进 | 已登记在 `config/hotel-alerts.json` 的 `debt`，先用上游已发布同比字段覆盖同类风险；评估器扩展放后续 change（改共享原语会影响餐饮/花卉） |
| E2 | 美股 RevPAR 同比字段缺位 | 8-K 通道只落了 RevPAR 水平值，未落同比 → `hotel_revpar_yoy_decline_us` 规则评估为 `n/a`（不静默通过） | 抽取器补同比列后启用该规则 |
| E3 | registry tool_list 快照滞后 | 五个 hotel 工具已部署（live `tools/list` 82 工具含之），但 registry 快照仍是旧的 → `supply-annotations/validate.py` 报 5 条「不在上游 tool_list」 | **已关闭（2026-11-08）**：真因不是「等刷新」，是 E9（门在读死快照）；按 E9 修好后快照补写 82 工具，五条转绿 |
| E4 | 城市实体编码用上游标签 | `CN-CITY-杭州` 这类编码是「上游行标签」，不是 GB adcode —— 为的是零臆造，但跨源 join 不便 | 拿到可信 adcode 源后统一重编码（一次性迁移脚本） |
| E5 | 舰队首轮报告缺证 | 舰队 `active`、三岗 `on_duty`，首轮 `budget_exhausted`（`MODEL_BUDGET_DAILY=8,000,000`，UTC 00:00 重置） | 预算重置后 `single_shot` + `wait_reports` 取首份值守记录 |
| E6 | escalate 投递未演练 | 复用 `health.inspector.submit_new_tickets`，需 `WANXING_SPIDER_HEAL_KEY` + 已落盘工单目录；本线当前无未终态健康工单 | 造一条健康工单后跑 `--escalate`，验投递与回读 |
| E7 | `ctha-hotel-report` 不可点亮 | 索引面 `/cat/35/pN.html`、`/art/<id>.html`、`/robots.txt`、`/sitemap.xml` 一律 302 → `/account/login`（多 UA / http(s) / cookie jar 全一样），静态 PDF 仍 206 | 索引面重开或拿到站点凭据；**不可**硬编码 PDF 哈希（下一版必变，会静默取旧版） |
| E8 | 188 行 (FOR, PCH_SM) 未注册概念 | `tour_occ_nim` 上游有「外国居民同比」，本波未注册该概念 → 抽取时显式跳过并留 note | 过命名门后补概念，再回填 |
| E9 | **注释门一直在读死快照（已修）** | `supply-annotations/validate.py` 把 pod 名（`registry-5776df447d-c8pxg`）与 context（`cheap`）写死，两处都过期 → exec 失败 → **静默退回 `_sources` 快照**，于是「hotel_* 不在上游」的红其实是 11-04 旧快照的红。E3 原先对它「等 registry 刷新」的判读是错的 | 已修：pod 按 label 现取 + 读面优先 spine 投影（`SPINE_REPLICA_MODE=1` 后 registry 实际服务的存储）+ 回退时打印原因；`apply_tool_surface` 补写快照 82 工具。**残留**：任何仍写死 `cheap` 上下文的脚本都会同样静默降级（`cheap` 已彻底失联） |
| E10 | **数据面按 owner 分组过滤**，跨 owner 建舰会看不见工具 | `fd-open-data-mcp` 在 `extension_configs` 带 `required_groups=["analysts","sl63fy08ruh9"]`；dsh 合成 profile 时按 cell 的 `owner-groups.json` 过滤 → `platform-test@findmail.com`（groups `[]`）的 cell 只挂 21 个 server、无数据面，轨迹里 `hotel_*` 命中 0、模型只能 bash/tool_search 空转 | 已按餐饮线先例迁到 `aloadtree@gmail.com` 名下重铸（`fleet_1794098779663_gnofzb`）；**未做成能力** —— 建舰前应校验「owner 的 cell 挂得上该 pack 声明的每个 MCP server」，建议在构造闸里加一条。**2026-11-09 已补一半**：pack v3 把 `hotel_*` 写进 `allowTools` 并装进 cell（`POST /api/mypacks/install {packId, version:3}`），另在 `fleet-packs/tooling/validate_manifest.py` 加了 allowTools 实探闸（声明名必须存在于 registry 实测工具面，否则点名失败） |
| E11 | **cell 的 cron 串行链会被僵尸任务永久堵死** | `executionQueue = executionQueue.then(...)` 是进程内串行 promise；cell 重启/长回合中断会留下 `state=running` 不落地的僵尸（实测 `task_1794080873057_w6cy37` 自 08:07 起不结束，日志 `session/delete … unknown DeepSeek Harness SDK runtime method` 清理失败），链上 29 个 job（含跨境/园区/餐饮生产舰队）全排队；`removeJob` 不能解链（promise 仍悬着） | 平台侧问题，本线只能等重启该 cell/整 pod；建议平台侧：加载时把超期 `running` 也当 `queued` 重入队，或给执行加可取消的 abort 通道 |
| E12 | **平台内存闸已到顶** | `memory.current 3.38G / memory.max 4G`（46 个 cell 常驻），新 cell 唤醒被拒：`cell residency refused: container memory headroom below floor` → 无法用「另起隔离 cell」绕开 E11 | 抬限额或收紧常驻/驱逐策略；本线验收据此只能落在既有 cell 上 |
| E13 | **在用的 registry 凭据已过期（实测 401）→ 已续期关闭** | `user_registry_credentials` 里 `aloadtree@gmail.com` / `machine-owner` 两行 `expires_at=2026-09-29T12:10:20Z`（早过期约 40 天）而 `stale=0`；拿该 token 打 `https://mcp.finddatatech.cloud/fd-open-data-mcp/mcp` 的 `initialize` → **HTTP 401 `{"error":"Authentication required"}`**。dsh-profile 仍把它当「live credential」写进 cell patch（它只看有没有行，不判过期） | **2026-11-08 12:30 已关闭**：`paas/scripts/renew-registry-credential.mjs` 用口令走 Logto Experience API 续期成功（`✓ mint token exp=2026-11-15T03:22:14Z` / `✓ write credential connected=true` / `✓ handshake 3/3`），256→656 字符新 token 落在 **cell 自己的库**（`/data/cells/<id>/data/data/app.db`，pod 级 `/data/data/app.db` 不会同步，别在 pod 级判「没续上」）。续后实证：`initialize` 82 工具、`hotel_series` 354 真实点、`hotel_universe{region:CN}` 6 家 |
| E15 | **平台 pod 在 sha-6da050d 上崩溃循环（当日真正元凶）** | 2026-11-08 13:20 并发会话推的镜像启动即崩：`Last State: Terminated / Reason: Error / Exit Code: 1`，前容器日志 `[FetchTimeoutError: outbound fetch timed out after 10000ms: https://auth.finddatatech.cloud/oidc/.well-known/openid-configuration]`；`platform-56799f9657-r74gt 2/2 Running 3 (27s ago) 71s`。后果：cell 每轮被腰斩（turn 记 `interrupted`）、每次重启把 queued 积压按原序重排 → 队尾（含酒店舰验收单发）结构性饿死 | 13:27 已由并发会话滚成 `platform-5bc7667f9d-66clv`（0 重启 / `healthz 200`）后自愈；该 Deployment 不归 Argo（无 Application、`strategy: Recreate`、`hostNetwork`、nodeSelector cheap-7），**镜像谁推谁修**，别用 kubectl 回卷当解法 |
| E16 | **概念声明的 entity_type 与数据落点不一致** | `hotel.cn.star_rated.occupancy.all.quarterly` 声明 `entity_type=region`，而全国聚合行实际挂在 `entity_type='country'`、`entity_id=1`；通用 `read_series` 按声明校验 → 传 `region`+id=1 得 `count:0`（note 写「coverage gap」），只有 `hotel_series` 能读到该行 | 省级行不受影响（`entity 301333` → count 3：63.14/69.23），故不阻塞 5.6；修法二选一：改概念声明为 country，或把全国行按 region 落库。属数据模型小缺陷，排后续 change。**2026-11-09 已闭合（第三种修法）**：不改声明也不搬数据，`read_series` 在窗口为空时按实体**真实类**再读一次（仅当实体类≠请求类），返回 `landed_entity_type` + 说明，真正空窗口仍报 coverage gap。`fd-open-data-mcp` 提交 `7588e44` → 镜像 `sha-7588e44`；线上实测 `read_series{2854, region, 1, 2023-01-01..2024-12-31}` = **count 3**（52.41/50.02/52.96）、`landed=country`。真值订正：全国是 **3 期不是 5 期** |
| E17 | **窄组 cookie 会覆盖 cell 的组快照（自己踩坏能力面）** | `server/owner-groups.js noteOwnerGroups(user)` 把**请求身份**的 groups 写入 `owner-groups.json`（该 cell 的 profile 合成依据）。2026-11-08 实测：用只带 `['creators','platform','sl63fy08ruh9']` 的合成 cookie 调平台 API，把 aloadtree 原本 8 个组覆盖成 3 个，随后周期性 profile 重写把所有带 `requiredGroups` 的 registry 源整批滤掉（35 → 30 个 server，`fd-open-data-mcp` 消失），舰的回合因此报 "MCP server fd-open-data-mcp not mounted" | 用合成身份调 API 时**必须带该身份的完整组集**；临时快照被踩后，用完整组请求一次即回正。排查口诀：**profile 少 server 时，先比 `owner-groups.json` 与 DB 行的 `required_groups`**，别急着怀疑凭据 |
| E18 | **`mcp.patch.yml` 会陈旧，且没有自愈** | profile 只在特定事件重写；一旦在某次错误状态下写出（如上面的窄组时刻），会长期停在少 server 的版本。`POST /api/users/me/bindings/apply` 是**强制重写**的杠杆；重写后还需运行时重载（`[mcp-delivery] credential change: runtime child restarted with the fresh patch`）才对新回合生效 | 舰队"工具不在我的列表里"类症状，先比对 `grep -c serverName mcp.patch.yml` 与期望数量，再 `bindings/apply` 重写并确认重载日志 |
| E19 | **`QUEUE_MAX_AGE_MS=600000`：排队超 10 分钟即丢弃（非延后）** | cell 的 cron 串行链单轮 5–10 分钟，队列常年 12–22 条 ⇒ 队尾任务必然在轮到自己前超过 10 分钟，被判 `skipped`，`error` 原文 `queue staleness: a scheduled fire waited 31min behind the ce…`。**这是 5.6 反复失败的结构性原因**，与平台崩溃/凭据/profile 全部无关（那三项已分别修复） | 要么平台侧调大 `QUEUE_MAX_AGE_MS` 或启用 worker 并行（现 `TASK_WORKER_MAX=0`），要么在队列刚被批量清空的窗口内、前面 ≤2 条时发单发。舰队类"发了不跑/跑了报旧错"的症状，先查这条 |
| E20 | **重启重放绕过 worker 池（`drainRepairQueue` 直连串行链）** | `paas/task-engine.js:262-282`：`drainRepairQueue()` 重放持久化的 `queued` 任务时直接 `chainExecution(id)`，**不经过 `dispatch()`**，因此 `slotDispatcherFn`（worker 池）根本不参与 → 重启后所有积压只能挤在单串行链上，再叠加 `QUEUE_MAX_AGE_MS=600000` 的陈旧丢弃 ⇒ 队尾任务必然被丢。实测：cell 1da `TASK_WORKER_MAX=2` 已 active、条件全满足（`trigger=schedule`/`targetType=persona`/`preset=hotel-watch-scout`/`carrier=null`）却**从未 spawn 过一个 worker**，日志只有 `[worker-pool] active` 而无 `spawned` | 修复点：`drainRepairQueue` 也走 `dispatch(id)` 而非 `chainExecution(id)`，让重放与新建同权；或把 `QUEUE_MAX_AGE_MS` 与重启重放解耦（重放不应计时）。这是平台侧一行级修复，比调参干净；**2026-11-09 已上产**：release 线镜像 `law-bench/platform:sha-d2c30b2` 起含 `dispatch(id)`，我方镜像 `sha-37f3369` 继承之；pod 内 `sed -n '/function drainRepairQueue/,/^}/p' task-engine.js \| tail -2` = `dispatch(id);`。**但收益暂为零**：生产 `TASK_WORKER_MAX=0`（`fd-infra-deploy` 注释：4Gi 上限下 worker 池与 cell 足迹过不了内存准入，须另立 change），池未开时 `dispatch` 等价于 `chainExecution` |
| E21 | **worker 池通道跑完不记账、且可能根本没起 agent 会话** | 2026-11-08 17:00 实测：全新触发被 worker 池接走（`[worker-pool] spawned worker-2 for persona hotel-watch-scout`），job 记录 `state=done success=true duration=43151ms tokens{in 769,out 652,cacheRead 54016}`，但 ①`member_turn_events` 无新行 ②`trace.db` 里该 session（`cron-job_1794128300056_hwmvbw`）**0 事件**（当时最新记到 16:53）③`/reports` 仍是旧两条。即 worker 通道「跑了但没留痕」，无法作为验收证据 | 追worker 的 `runOnWorker` 记账路径（worker 用独立 bridge/session，可能未挂 `sessionCollectors` / 未写 trace）。在补上之前，**worker 池只能解决吞吐，不能产出可审计的舰队轮次**。2026-11-08 18:13 进一步实测：`/data/cells/<id>/dsh/sessions/--data-cells-…-workspace--/` 下 2181 个会话条目里**找不到** `cron-job_1794128300056_hwmvbw` 对应的会话目录（`grep -rl` 该 job id 零命中，只在 `app.db`/`jobs.json` 有记录）⇒ 那次 43 秒的「成功」可能压根没启动 agent 会话，只是把任务标了 done 并把 token 记在别处。判据：worker 路径要么没有真正执行，要么执行后不留任何可审计痕迹——两者都不能作为验收证据；**2026-11-09 已上产**：release 线提交 `9eb9b2c79`（基于 release 头 `672cf3fec`；首版 `1f542a15d` 曾被并发镜像顶掉，故重放）→ 快照 `sha-37f3369` → 镜像 `law-bench/platform:sha-37f3369`，pod 内 `grep -c recordFleetTurnEvent server/worker-slots.js` = 2。`runOnWorker` 三条退出路径都结算 `ctx.recordFleetTurnEvent` + `settled` 幂等闩；`test-worker-pool` 12/12（含 2 条新验收，突变验证撤掉落账调用→正好这 2 条红）、`test-task-engine` 29/29。**仍未实测生效**：生产 `TASK_WORKER_MAX=0`，worker 通道不走 |
| E22 | **队内 run-now 不升级 `queuedBy`，人工触发仍会被陈旧丢弃** | `POST /api/cron/:id/run` 对**已在队列里**的任务返回 `{ok:true, state:"queued", already:true, queuedForMs:481625}`，但不把该条目的 `queuedBy` 改成 `"human"` ⇒ 人工请求继承 cadence 的陈旧判定，`QUEUE_MAX_AGE_MS=600000` 一到照样被丢。2026-11-09 实测：job `job_1794128300056_hwmvbw` 已排 8 分钟、`queuedBy` 仍为 null | 入队去重时若 `opts.by === "human"` 应**就地升级**既有条目的 `queuedBy`（并记一条 history 说明被人工催办），否则「run now」在长队列里等于没有豁免 。**已闭合（2026-11-09）**：paas 提交 `817f74edb` 在去重分支就地升级 `queuedBy` 并写 history（`upgraded: true`），不动队列位置与 `firstQueuedAt`；`test-task-engine` 30/30 + 突变验证 |
| E23 | **显式空头会清空 cell 的组快照（工具面凭空消失的真因）** | `noteOwnerGroups()` 用**请求身份**的组覆写 `data/owner-groups.json`，而 profile 的组闸把 `[]` 读成「已认证、无任何匹配组」并**删除**整批带 `requiredGroups` 的 server。2026-11-09 实测：快照被写成 `[]` 后 `mcp.patch.yml` 只剩 26 个 server、`grep -c "serverName: fd-open-data-mcp"` = **0**，舰队回合随即报「这三个工具未对当前聚焦模式角色启用」；`bindings/apply` 重写后 30 个 server、数据面回来，同一 prompt 下一轮 13 次直连调用全通。**归因订正**：不是 `fleet-bridge@internal`（该中间件只认 `email === cellUserEmail`），而是**带 owner 邮箱、`x-forwarded-groups` 显式为空的请求**——空头与缺头在请求侧已有 `groupsKnown` 区分，写侧却把两者都当成「确知无组」 | **已闭合（2026-11-09）**：paas 提交 `40b914c3f` 加空集守卫——空组集不覆盖非空快照，只 warn（含旧/新组数）；真实全量撤销走显式 `{ revokeAll: true }`；非空收缩不受影响。线上实证：空组 cookie 打一次 fleet API → 快照仍 7 组、日志 `refused to blank the snapshot … 7 group(s) -> 0`。测试 14/14 + 突变验证。 |
| E24 | **小窗口模型装不下本 cell 的人设上下文（压缩风暴）** | 2026-11-09 实测三轮：①`pi-ai detected context overflow`（CONTEXT_WINDOW_EXCEEDED）②`turn stopped early — context pressure (6 compactions)`。单回合 payload 累计 287–330 KB，最大单条 43 KB 是 `tool/result`（`hotel_series` 全序列 / `list_entities(limit=100)`），另有 27 KB 的 assistant reasoning；cell 全部模型 `contextWindow=128000`。叠加 `skills.patch.yml` 把 **22 个技能包**全目录挂进 platform profile（每回合 60 条 `available_skills` 清单，约 8 KB），以及 31 个 MCP server 的工具面 | 三条可选项：①人设级技能作用域（只挂本岗位需要的包，现在是全 cell 共享）；②限制工具返回体积（`hotel_series` 支持 `limit`/分页，`list_entities` 默认小 limit）；③换更大窗口模型（本 cell 无）。**先做 ②** 代价最小。判据：同一 prompt 下 payload < 60 KB 且回合 `status=ok` 。**已修一半（2026-11-09）**：`hotel_series` 默认只回最近 60 点并带 `truncated{returned,total,how_to_get_more}`，`start`/`end` 真正接到 SQL（fd-open-data-mcp `382bc96`，镜像 `sha-382bc96`，pod 内 `cap=60`）；技能作用域（22 个包全灌）与人设级收窄仍未做 |
| E25 | **高频滚版 + 单 cell 883 个 cron 让长回合几乎无法完成** | 2026-11-09 一天内 `platform` 被并发会话滚版 **8 次**（rev 618→638，镜像 sha-8594fe7→b27fe835 之间多次）；每次重启都把在途回合并发清空（`turn 6089` 跑 274s 但 `model=null`、`traceTurnId=null` 的空壳即此形状），并把队列按 `jobs.json` 文件顺序重排。叠加该 cell **909 个 cron 任务**、共享 dsh 子进程只有一个、`TASK_WORKER_MAX=1`，队列里的定时任务全部在排到 10 分钟时被 `dropped stale queued fire` 丢掉（实测 15:30–15:40 连丢 4 条），日志反复出现 `credential change deferred until the turn boundary (the shared child is busy)`。5.6 的 PASS 能拿到（turn 6080），但**不可重复** | ①滚版要有静默窗口（或把 fleet 回合的 `turnTimeout` 与重启解耦：重启前先把在途回合标记 interrupted 并可重放）；②883 个 cron 挤一个 cell 需要治理（清理/迁 cell/降低频率）；③worker 池要与内存准入配套（现 1，且被别的 persona 占）。**判据**：同一 prompt 连发两轮都能 `status=ok` 且 `traceTurnId` 非空。**2026-11-09 16:07 再实证**：在「队列 4 条、0 条在跑」的空窗发出，回合跑到一半被滚版打断，job 记 `cell restarted during execution`（当日第 9 次重建）——滚版周期（十几分钟）≈ 回合时长（5–10 分钟），命中是概率问题而非巧合。另：`drainRepairQueue` 已改为按 `firstQueuedAt` 排序（paas `7c7c0fc`，镜像 `sha-939cbc4`），重启不再把等得久的任务推回队尾 |


## 二·五、后续 change 承接

本台账的 E16 / E19 / E20 / E21 与 E10 的具体形态（pack `allowTools` 缺 `hotel_*`）已写成正式提案：

**`openspec/changes/fix-fleet-replay-dispatch-and-turn-accounting/`**（`openspec validate --strict` 已通过）

四个能力：
- `fleet-replay-dispatch`（新）——重放必须经过派发缝；重启不得重置全部等待时钟；陈旧丢弃可追溯
- `fleet-turn-accounting`（新）——worker 回合必须落与串行链相同的账、必须真起 agent 会话、记账幂等、报数落库
- `hotel-mcp-surface`（改）——allowTools 可声明具体工具名 + 发布闸校验名字真实存在 + 全国聚合序列不静默返空
- `hotel-vertical-data`（改）——概念 entity_type 与落点一致

**已上产（2026-11-09）**：
- 平台镜像 `law-bench/platform:sha-37f3369`（pod 内实证：`drainRepairQueue` 末行 `dispatch(id);`、`worker-slots.js` 的 `recordFleetTurnEvent` ×2）。生产 `TASK_WORKER_MAX=0`，故 E20/E21 落地但通道未启用。
- 数据面镜像 `finddata/fd-open-data-mcp:sha-7588e44`（E16 按落点读，线上返回 3 期 + `landed=country`）。
- pack `AH5iRdho7xEJIMplpL2BjA` **v3** 已发布并装进 cell（allowTools 含 5 个 `hotel_*`，en 展示面 8/8）。
- 发布闸：`fleet-packs/tooling/validate_manifest.py` 已加 allowTools 实探校验（提交 `d804861`）。

**5.6 判词：PASS（2026-11-09 turn 6080）** —— `status=ok`；trace 里 13 次**直连**数据面调用（`hotel_universe`×1、`hotel_series`×2、`read_series`×4、`read`×2、`list_concepts`×3、`resolve_entity`×1）+ 末次 `report_status` 落报数；报数为真值（CN 6 家逐家具名、`hotel_series` 354 点、并指出 `entity_type="country"` 被上游拒）。取得它需要四个前置同时成立，缺一即空转：①pack v3（`hotel_*` 在 allowTools）②`owner-groups.json` 为全量 7 组③`mcp.patch.yml` 已 `bindings/apply` 重写（陈旧补丁里没有 `fd-open-data-mcp`）④prompt 压到「两次调用即停」（否则压缩风暴）。

**线别铁律（血账）**：生产镜像从 `gitee/release/cell-session-persona` 出（`/app/BUILD-INFO.json` 的 `baseRef` 可验）；main 系落后该线 128 个提交，**从 main 出片会让线上整批回退**。且该线被多会话高频写，升 tag 前先 `git fetch gitee` 看有没有新头，推必须显式 `git push gitee`（本仓 `gitee` 的 pushurl 指向 github，Argo 看不见）。


## 三、跨线风险（不属本 change，但会咬到本线）

| # | 项 | 事实 | 谁负责 |
|---|---|---|---|
| X1 | `fd_open_data_mcp/flower_tools.py` 未入库 | `server.py` 曾引用它（我误提交），导致镜像 `ModuleNotFoundError` → 生产 `CrashLoopBackOff` → 已 `rollout undo`；撤掉导入后重新出片恢复 | 花线会话须自己提交该文件，否则下次构建复现 |
| X2 | 并发会话共用工作树 | 暂存整个文件会串味（本次是**我**污染了别人） | 纪律：只暂存自己新增文件或逐 hunk 暂存 |
| X3 | registry 上游缺花线五个工具 | 2026-11-08 实测：spine 的 `/fd-open-data-mcp` 是 **82** 工具（含 5 个 hotel_*），fd-prod pod 是 **87**（含 5 个 flower_*）；`supply-annotations` 里 `flower_company_financials / flower_planted_area / flower_price_series / flower_trade / flower_universe` 五条因此仍红 | 花线自己出片后按同样方式刷面（`apply_tool_surface`）；本线不动 |

## 四、清点结论

- **永久不可得 3 条**（D5 / D9 / D10）：上游不存在或未发布，已在概念/板面显式声明，不再追。
- **需外部凭据 4 条**（D1 类目 / D2 key / D3 appId / D8 凭据）。
- **可自行推进 8 条**（E1–E8）。
- **今日新增 13 条工程债（E9–E21）与 1 条跨线（X3）**，其中：
  - **E9 已修**（注释门读死快照 → 动态 pod + spine 优先 + 回退点名）；
  - **E10 已绕开**（迁 owner 重铸，但「构造闸校验数据面可达」这条能力没做）；
  - **E11 / E12 未解**（平台侧 cron 链堵死、内存闸到顶），**5.6 与 7.3 第 6 段因此未验收**，等平台窗口；
  - **E13 已关闭**（凭据续期成功，实证 82 工具 / hotel_series 354 点 / hotel_universe 6 家）；
  - **E14 新增（hotel_universe 恒空）**：工具按 `metadata_json->>'registry'` 过滤而注册脚本只写了 `list` → 已修（两键同写），复核 `registry=hotel-universe: 33` 且工具返回 6 家；
  - E3（原「等 registry 刷新」）判读作废，真因是 E9，已随 E9 一并关闭。
- 无「原因空着」的条目。
