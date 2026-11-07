# 花卉垂直线 — 挂账清点（add-flower-vertical 7.4 真源）

> change `add-flower-vertical` · 清点日 2026-11-08（UTC 2026-11-07T20:xxZ）· 逐条带实测证据
> 处置三分类：**已修**（本轮落地）/ **待裁**（需口径裁决）/ **待授权**（需点头动产线或仓）
> 每条带「验收条件」——能判绿判红的才算挂账

## 1. 挂账表

| # | 级别 | 项 | 证据（可回指） | 处置 / 去向 |
|---|---|---|---|---|
| F1 | **P0** | 两个新源**在集群里还不存在**（manifest 未入库） | `git status --short spiders/{dounan-flower-price,aiph-stats}` → 两条 `??`；`kubectl --context xinru get cronjobs -A \| grep -c fd-industry` = **0**；`kubectl --context cheap-ts get cronjobs -A` 9 条无花卉。库内两次 success 是 2026-11-07T17:50 的**手动试跑**（`rows_written` 932 / 81） | **待授权**：提交 manifest → 打进 `fd-scripts` ConfigMap → `crawl-reconciler` 下一轮（每 15 分钟）才会创建 CronJob。验收：`kubectl -n fd-master get cronjobs \| grep -E "dounan-flower-price\|aiph-stats"` 有两条，且首轮按点触发后 `crawl_runs` 新增 success |
| F2 | **P1** | KIFA（`flower-auction`）被雷池 WAF 全站拦 | 2026-11-08 带完整 Chrome 头 + `--http2` 复测四 URL（含首页）全 `HTTP/2 468`、`server: Tengine`、`content-length: 14863`、`set-cookie: sl-session=…`、正文含 `challenge.rivers.chaitin.cn/challenge/v2/challenge.js` | **待裁**：三条解锁条件已写进 `spiders/flower-auction/manifest.yaml` 头注与 README —— ①JS 渲染会话（playwright）②稳定出口 + `sl-session` 24h 复用 ③挑战失效双路反馈。验收：任一条件下取到真表行且 golden 绿。**注意：2026-10-04 旧记录的「需非云出口」已被本次实测推翻** |
| F3 | ~~P1~~ → **已修（6.6）** | 「成功但查出 0 行」在健康面不可见 | 实证：`flower-auction` **4 次 success / rows_written 合计 0**。已加 manifest 可选字段 `min_rows_per_run` + 分诊**规则 1c**（先于健康短路；未声明即不生效=零回归）+ 10 条测试；真实巡检 `117 源 / 93 healthy` 且 AIPH 不误伤。**剩**：斗南只有一次回补样本（932 行），日常运行行数未测 → 故意不声明下限，等一个日常样本再定 | 验收：斗南日常运行样本到手后声明 `min_rows_per_run`，连续 N 轮低于下限能出单 |

| F4 | **P1** | 公开板**没有发布去处** | `publish_flower_boards.py` 只产出 `*-public.spec.json` + html（本机文件）；`fd-official-web/public/` 下只有 `research/papers`，全站无任何行业板入口（`grep -rl "agriculture-prosperity-public\|astock-trading" fd-official-web` 为空） | **待裁**：交付通道三选一 —— 官网 `research/boards/`、柏讯客户端内嵌、或仅作离线交付物。验收：选定后公网可访问或客户端可打开，且 `content-manifest.json` 有对应条目 |
| F5 | **P1** | 美股**分部维度**抽不出来 | `companyfacts` 已剥维度 → 分部图 `chart-segments` 空（带原因占位）；A股 `002200` 的 `stock-zygc` 该实体零条 | **转 change `awaken-flower-report-ingest`**（已存在于 `openspec/changes/`）：走 10-K instance 的分部轴。验收：分部图有真数且与合并口径分图不混算 |
| F6 | ~~P1~~ → **已修（3.2 完成）** | UN Comtrade 花卉三档已入库 | 密钥取自集群 `tencent/fd-industry/fd-industry-source-keys`（未落盘）；**57 点入库**（6 组合 × 近 10 年），贸易板 4 图出数（10 序列 / 97 点），规则 `hs0603_export_yoy_negative` 已启用（2025 同比 +147.93% → quiet）。接入过程修掉 4 个真缺陷：snake_case 字段名、`partner2_code` 明细行（2016 曾少算 21 倍）、看板派生序列 `dict(s)` 丢属性、`period` 截断把季度冒充年度 | 验收：已完成（2023 三档与官方免 key 预览逐位一致） |

| F7 | ~~P1~~ → **Eurostat 腿已修** | Eurostat 花卉口径已接入并真跑通（3.3）；剩 `usda-quickstats` / `jp-estat` 两项未勘察 | 实测（2026-11-08）：`apro_cpsh1` 花卉码 `N0000` 只有 `MAR_THS_HA`（主面积·千公顷）有值，产量/单产口径均 0 值；已加 `run_eurostat_flower_area` 命令 + provider 分发 + 抽取器（千公顷→公顷）+ 概念 `area.flower.all.planted` + 4 个国家实体，**77 点入库**、板面图 ⑤ 上线、规则 `eu_flower_area_yoy_negative` 启用（2024 同比 −2.26%）。**剩余**：`usda-quickstats`（花卉口径）未勘察；`jp-estat` 需 e-Stat 应用 ID（同 `1377.T` 的 EDINET key 一族）。验收：两项各出一个单元或用 P1 结论落档 | 
| F8 | P2 | 斗南**滚动窗 ~2 个月** | 板面 caveat 已声明「上游栏目只保留滚动约 2 个月（10 个列表页），历史自接入日起累积，不宣称更长历史」；观测首日 2026-09-09 | **已记录**（上游限制，不可补）。验收：无需动作；若上游放开分页深度再改 `lookback` |
| F9 | P2 | `nfga-floriculture` 无法结构化接入 | `nfga.gov.cn` 已废（HTTP 000），实站年鉴为**扫描件**（25 MB / 146 页 / `/Font` 对象 0 / `pdftotext` 每页 0 字符） | **待裁**：OCR（成本与准确率未知）或等上游改发结构化表。验收：OCR 后抽取表头与样本行人工核对通过 |
| F10 | P2 | 欧股**无纯花卉上市标的** | 侦察簿结论：欧股无上市花卉/园艺公司（Royal FloraHolland 是合作社、非上市；CBS 无当代花卉产量表）→ 欧洲只有贸易/产量面，没有公司财务面 | **已记录**（宇宙事实）。验收：无需动作；若发现新上市标的按 `docs/flower-universe.yaml` 增行 |
| F11 | P2 | HYFM 营收序列**停更** | `semantic_observations`：HYFM `financials.revenue` 最新 2025-09-30（18 期），而 `operating_profit` / `net_income` 走到 2026-06-30（20 期） | **已记录（口径决定）**：自 2026 起改用 `RevenueFromContractWithCustomerIncludingAssessedTax`，抽取端**刻意不并入**（含代收税，并入等于凭空造营收跳升）。预警侧已用 `max_period_age_days=200` 显式跳过；看板营收图少一期，未补 0 |
| F12 | P2 | 花卉 5 个 MCP 工具**未上活面** | 本地 `fd_open_data_mcp/flower_tools.py` 5 个 `@mcp.tool`：`flower_universe` / `flower_price_series` / `flower_planted_area` / `flower_company_financials` / `flower_trade`，契约测试绿（`tests/test_flower_tools.py`）；线上镜像早于该提交（**上一轮实测**活面 71 工具 vs 本地 82；本轮因 F13 无法重读活面，数字待滚版后复核） | **待授权**：commit→push→CI→滚版→registry `tool_list` 重同步。验收：深度页 tools 数 = 本地上牌数，且 5 个花卉名在列 |
| F13 | P3 | 注册表 live readback 的旧 context 已死 | `kubectl --context cheap get cronjobs` → `dial tcp 127.0.0.1:16443: connect: connection refused`（context 指向本机已不存在的端口）；`get-contexts` 里 `cheap` 也无 CURRENT 标记。今天能通的是 **`cheap-ts`**（9 个 CronJob 可读）与 **`xinru`**（20 个，含 `fd-master/crawl-reconciler`） | **已记录**：探针配方改用 `--context cheap-ts`（`docs/` 里写 `cheap` 的陈旧说法以本条为准）。验收：readback 探针在某 context 下稳定返回 |
| F14 | P2 | AIPH 年频源的停跑**不会自动出单** | `absent_source_policy` 默认 `report`：`aiph-stats` 容忍窗 370 天 → 整窗零运行只进 `plan.absent`，不出工单（这是 6.1 的零回归设计，不是缺陷） | **待裁**：是否给年频源单独接发布节奏级（`period_days=365`）以便「该发没发」也能判。验收：接上后年频源能区分「未到发布点」与「已过点没发」 |
| F15 | P3 | 事件数曾虚报、台账曾重写 | 90 天窗原始 56 行 → 真申报 28（`crawl_items` 逐次运行追加）；看板去重键漏 `accession`（488 vs 495）；`append_ledger` 只跟已存文件去重、不跟批内去重 → 台账文件实际 **56 行 / 28 键**（已收敛回 28 行，重跑 0 新增） | **已修**：`scan_events` 按业务键收敛 + 看板键补 accession + `append_ledger` 批内去重，三处口径一致，各带测试（6.5/7.3 留痕） |
| F16 | P3 | 空图曾无占位 | 公网板 5 张空图只有坐标轴（贸易 4 + 分部 1），`no_data` 的「板面出无数据（原因）」承诺未实现 | **已修**：`industry_common.empty_state()` + 两个 builder 挂占位 + 产物级回归测试（7.3 留痕） |

| F17 | P2 | 预警引擎**没有调度** | 全仓无任何 `flower_alerts`/`restaurant_alerts` 的 CronJob 定义（`grep -rn "restaurant_alerts\|flower_alerts" --include=*.yaml .` 只命中 config 描述）；`k8s/cronjob-template` 只渲染 `spiders/*/manifest.yaml` 的源，不含脚本级任务。故四级预警目前只能手动跑 | **待裁**：与餐饮线同一现状，两条线一起定「预警跑哪儿」——①并入 `crawl-watcher-scan`（已每 15 分钟跑）②新增一个 alert CronJob ③外部定时器。验收：连续 N 天有 `reports/flower-alerts/<date>/rules-*.json` 且状态机按边缘触发收敛 |

| F18 | P2 | EU 花卉主面积**没有刷新调度** | 该序列靠 `scripts/extract_flower_wave1.py --only eurostat-api` 从 provider 按需取数写入；全仓没有任何定时任务跑这个抽取器（同 F17 家族）。上游年频、每年一次更新，静默过期不易被发现 | **待裁**：与 F17 一起定「抽取器跑哪儿」——放进 `crawl-watcher-scan` 的批次，或单开年频 job（建议每年 1 月，跟 AIPH 同窗口）。验收：连续两年有新年份点入库 |

## 2. 与侦察簿/宇宙清单的对齐（真查）

| 检查 | 结果 |
|---|---|
| `docs/flower-universe.yaml` 条目 | 7（纯花卉 2：FLWS / 1377.T；邻接 5：CENT / SMG / HYFM / EDBL / 002200） |
| 库内实体命中 | 7/7 注册；**另发现 `002200` 同时存在 `entity_type='fund'` 行（基金同码）**——预警与看板均按 `entity_type` 过滤，不误取 |
| 概念注册 | 斗南 3（最高/均价/最低）、AIPH 9（面积）、SEC 3（营收/营业利润/净利） |
| 观测自洽 | 斗南 932 行 × 3 概念 = 2,796 点（一致） |

## 3. 验收基线（本日快照）

- `conformance_gate`: **PASS — 162 units, 0 violations**
- `openspec validate add-flower-vertical --strict`: 绿
- 测试：`tests/test_flower_alerts.py` 17 / `test_flower_publication.py` 9 / `test_health_absent_sources.py` 7 / `scripts` 侧 SEC 抽取器 8 / `dashboards/tests` 31 passed 1 skipped
- 板面：价格与供给 29 序列 / 493 点 / as-of 2026-11-05；公司财务 9 序列 / 287 点 / as-of 2026-11-08；贸易链 0/4（口径事实，带原因）
