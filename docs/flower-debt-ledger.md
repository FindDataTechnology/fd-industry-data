# 花卉垂直线 — 挂账清点（add-flower-vertical 7.4 真源）

> change `add-flower-vertical` · 清点日 2026-11-08（UTC 2026-11-07T20:xxZ）· 逐条带实测证据
> 处置三分类：**已修**（本轮落地）/ **待裁**（需口径裁决）/ **待授权**（需点头动产线或仓）
> 每条带「验收条件」——能判绿判红的才算挂账

## 1. 挂账表

| # | 级别 | 项 | 证据（可回指） | 处置 / 去向 |
|---|---|---|---|---|
| F1 | ~~P0~~ → **已修（上产）** | 两个新源在集群里不存在 | 已推 Gitee main（commit `8f0457b`）→ ArgoCD AppSet 生成 Application → **集群出现两条 CronJob**（`fd-industry-dounan-flower-price` `15 0 * * *`、`fd-industry-aiph-stats` `42 0 12 1 *`，CronJob 总数 76→78）；手动触发一次 `flower-verify-*` Job → Pod **Completed**，`crawl_runs` 新增 success `rows_written=100`、`image_tag=…runner:sha-750da3f` | 验收：已完成（真实调度链路端到端跑通） |

| F2 | **已定谳（不接）** | KIFA（`flower-auction`）被雷池 WAF 拦 | **两次实测拼出完整结论**：①应用内真实 Chrome 打开 → 挑战约 **15 秒自动放行**，落到真实站点；真实入口是 `*.do`（`kIFAPriceIndex.do` / `breedQuotation.do?method=index`），行情 JSON = `breedQuotation.do?method=getInfo&breed=<id>`（玫瑰 111 / 康乃馨 112 / 非洲菊 120，传中文名返回 `[]`），日频 6 天。②**但 playwright 三种配置（headless / 有头 / `channel=chrome`，含抹 `navigator.webdriver`、去 `--enable-automation`、反节流）全部失败**，60 秒后仍在挑战页，正文明确写「雷池 WAF 驱动　确认**客户端异常**，请确认您是合法用户」——**它主动拒绝自动化客户端**，带 `sl-session` 的裸 curl 也仍 468 | **裁决：不接**（不是「做不到」而是「不该做」）。硬闯 WAF 属检测规避，违反本项目「不逆向反爬」红线，且已证明无效；数据增量也窄（3 大类 × 均价/最高价 × 6 天），斗南日价已覆盖同类信息。如将来要，只能走真实桌面浏览器会话产出的数据（人工导出/扩展/半自动）。留痕已写进 manifest 头注与 README，**明确写了「别再用 playwright 硬闯」**，避免下一个人重跑我今天这三次 | 验收：结案，无需动作 |


| F3 | ~~P1~~ → **已修（6.6）** | 「成功但查出 0 行」在健康面不可见 | 实证：`flower-auction` **4 次 success / rows_written 合计 0**。已加 manifest 可选字段 `min_rows_per_run` + 分诊**规则 1c**（先于健康短路；未声明即不生效=零回归）+ 10 条测试；真实巡检 `117 源 / 93 healthy` 且 AIPH 不误伤。**剩**：斗南只有一次回补样本（932 行），日常运行行数未测 → 故意不声明下限，等一个日常样本再定 | 验收：斗南日常运行样本到手后声明 `min_rows_per_run`，连续 N 轮低于下限能出单 |

| F4 | ~~P1~~ → **草稿已落库，发布待确认** | 公开板没有发布去处 | **平台有现成通道（不必自造）**：`fd-wire` 的 `import_showcase.py` 就是「行业看板批量导入（平台自营）」，读的正是 `dashboards/*.spec.json` 形状（title/charts/kpi）。**执行结果**：在 `fd-wire-api` pod 内直连库，把三块板建为柏讯产物草稿 —— id **194 公司财务板 (v3)** / **195 价格与供给板 (v2)** / **196 全球贸易链板 (v2)**，均 `status=draft` / `visibility=private` / `customer_id=finddata-official`（194 是 `updated_draft`，说明历史上已有一版，本次是按 slug 幂等更新）。| **为什么停在草稿**：官方导入器把 create→publish→market 串成一步，而 publish 要过「缺证据的强断言」门（面板里有「4.28 亿元」这类金额，无 `evidence` 块即被拦：`InvalidArtifactSpec`）。草稿不过该门，且柏讯纪律本就是「写入落草稿、发布独立且需明确同意」。**下一步二选一**：①人工在柏讯控制台确认后点发布；②补 `evidence` 块（需 fd-info info-stream 可用，现未部署）后再自动发布。验收：控制台能看到三块花卉板草稿 |



| F5 | **不做（复杂度判定）** | 美股分部维度抽不出来 | 调查结论：①平台内**没有**现成分部抽取工具——`companyfacts` 端点本身不含维度（这是抽取端刻意剥掉的），`fd-cn-report` 做的是中文财报 PDF 抽取，与本需求不同构；②开源有 `arelle-release 2.47.4`（专业级 XBRL 平台）与 `python-xbrl 1.1.1`（简陋），但真正的工作量不在解析器，而在**逐家人工映射分部轴成员名**（各公司自定义命名，禁止按名字相似度匹配）——属「很复杂」档 | **决策：不做**。已立项的 `awaken-flower-report-ingest` 保留为将来入口；分部图继续显示「无数据 · 待年报分部附注抽取落地」占位（不是空白、不补 0）。验收：结案 |

| F6 | ~~P1~~ → **已修（3.2 完成）** | UN Comtrade 花卉三档已入库 | 密钥取自集群 `tencent/fd-industry/fd-industry-source-keys`（未落盘）；**57 点入库**（6 组合 × 近 10 年），贸易板 4 图出数（10 序列 / 97 点），规则 `hs0603_export_yoy_negative` 已启用（2025 同比 +147.93% → quiet）。接入过程修掉 4 个真缺陷：snake_case 字段名、`partner2_code` 明细行（2016 曾少算 21 倍）、看板派生序列 `dict(s)` 丢属性、`period` 截断把季度冒充年度 | 验收：已完成（2023 三档与官方免 key 预览逐位一致） |

| F7 | ~~P1~~ → **Eurostat 腿已修** | Eurostat 花卉口径已接入并真跑通（3.3）；剩 `usda-quickstats` / `jp-estat` 两项未勘察 | 实测（2026-11-08）：`apro_cpsh1` 花卉码 `N0000` 只有 `MAR_THS_HA`（主面积·千公顷）有值，产量/单产口径均 0 值；已加 `run_eurostat_flower_area` 命令 + provider 分发 + 抽取器（千公顷→公顷）+ 概念 `area.flower.all.planted` + 4 个国家实体，**77 点入库**、板面图 ⑤ 上线、规则 `eu_flower_area_yoy_negative` 启用（2024 同比 −2.26%）。**剩余**：`usda-quickstats`（花卉口径）未勘察；`jp-estat` 需 e-Stat 应用 ID（同 `1377.T` 的 EDINET key 一族）。验收：两项各出一个单元或用 P1 结论落档 | 
| F8 | P2 | 斗南**滚动窗 ~2 个月** | 板面 caveat 已声明「上游栏目只保留滚动约 2 个月（10 个列表页），历史自接入日起累积，不宣称更长历史」；观测首日 2026-09-09 | **已记录**（上游限制，不可补）。验收：无需动作；若上游放开分页深度再改 `lookback` |
| F9 | **不做（投入产出不划算）** | `nfga-floriculture` 年鉴是扫描件 | 环境里有 `tesseract` 与 `pdftotext`，但该年鉴 146 页全是**统计表格**（`/Font` 对象 0、每页 0 字符），表格 OCR 需要版面对齐 + 人工校对，准确率不可控；且年频一次、增量价值有限（面积数据已由 AIPH + Eurostat 两条结构化源覆盖） | **决策：不做**。留痕保留在侦察簿（含 contentId 与附件 URL）。若上游改发结构化表再启动。验收：结案 |

| F10 | P2 | 欧股**无纯花卉上市标的** | 侦察簿结论：欧股无上市花卉/园艺公司（Royal FloraHolland 是合作社、非上市；CBS 无当代花卉产量表）→ 欧洲只有贸易/产量面，没有公司财务面 | **已记录**（宇宙事实）。验收：无需动作；若发现新上市标的按 `docs/flower-universe.yaml` 增行 |
| F11 | P2 | HYFM 营收序列**停更** | `semantic_observations`：HYFM `financials.revenue` 最新 2025-09-30（18 期），而 `operating_profit` / `net_income` 走到 2026-06-30（20 期） | **已记录（口径决定）**：自 2026 起改用 `RevenueFromContractWithCustomerIncludingAssessedTax`，抽取端**刻意不并入**（含代收税，并入等于凭空造营收跳升）。预警侧已用 `max_period_age_days=200` 显式跳过；看板营收图少一期，未补 0 |
| F12 | ~~P2~~ → **已修（活面 87 张牌）** | 花卉 5 个 MCP 工具未上活面 | **根因不是镜像旧，是 `server.py` 从未 import `flower_tools`**（工具/测试/注释全就绪却漏在门外）。已补 `register_flower_tools(mcp)` → 本地 82→87；出片 `sha-95e8138`（GHA success）→ cheap3 relay `08:37:18 OK finddata/fd-open-data-mcp:sha-95e8138` → 改 `fd-infra-deploy` 并推 Gitee → ArgoCD 滚版，新 Pod 1/1 Running。**三处一致**：活面 pod `tools/list`=**87**、注册处 `num_tools`=**87**、facet 目录=**87**，`flower_universe/price_series/planted_area/company_financials/trade` 全部在列；公网端点复验亦 87 | 验收：已完成 |


| F13 | P3 | 注册表 live readback 的旧 context 已死 | `kubectl --context cheap get cronjobs` → `dial tcp 127.0.0.1:16443: connect: connection refused`（context 指向本机已不存在的端口）；`get-contexts` 里 `cheap` 也无 CURRENT 标记。今天能通的是 **`cheap-ts`**（9 个 CronJob 可读）与 **`xinru`**（20 个，含 `fd-master/crawl-reconciler`） | **已记录**：探针配方改用 `--context cheap-ts`（`docs/` 里写 `cheap` 的陈旧说法以本条为准）。验收：readback 探针在某 context 下稳定返回 |
| F14 | **不做（改为既有机制覆盖）** | AIPH 年频源停跑不自动出单 | 调查结论：publication 级（学「发布滞后」）对 AIPH **不适用**——年鉴是混合参考年的截面（2005–2024 混装，见 `docs/flower-series-curation.md` §3.1），学出来的滞后没有业务含义；而健康级已有两条覆盖：`min_rows_per_run: 20`（规则 1c，成功但行数不足要出单）+ `reconcile_absent` 的 `period-longer-than-window` 登记 | **决策：不新增 publication 条目**，维持 `absent_source_policy: report`（零回归）。验收：结案 |

| F15 | P3 | 事件数曾虚报、台账曾重写 | 90 天窗原始 56 行 → 真申报 28（`crawl_items` 逐次运行追加）；看板去重键漏 `accession`（488 vs 495）；`append_ledger` 只跟已存文件去重、不跟批内去重 → 台账文件实际 **56 行 / 28 键**（已收敛回 28 行，重跑 0 新增） | **已修**：`scan_events` 按业务键收敛 + 看板键补 accession + `append_ledger` 批内去重，三处口径一致，各带测试（6.5/7.3 留痕） |
| F16 | P3 | 空图曾无占位 | 公网板 5 张空图只有坐标轴（贸易 4 + 分部 1），`no_data` 的「板面出无数据（原因）」承诺未实现 | **已修**：`industry_common.empty_state()` + 两个 builder 挂占位 + 产物级回归测试（7.3 留痕） |

| F17a | ~~P2~~ → **已修（调度）** | 预警引擎没有调度 | 新增 `.github/workflows/flower-alerts.yml`：每日 **01:05 UTC**（斗南 00:15 UTC 跑完后 50 分钟）；状态机与报告 commit 回 main（cooldown/episode 必须跨运行持久）；退出码 **0/1/2/3 分段**：0=未越阈、1=有越阈（预期）、2=脚本自身失败（判红）、3=环境不满足（warning，不判红）。**已真实触发 3 次验证**，其中第 2 次抓出假绿（见 F17b） | 验收：已完成 |

| F17b | ~~P1~~ → **已修（授权 + 端到端实证）** | 定时任务读不到业务表 | 已授予只读账号 `fd_health_ro` 的 SELECT：`semantic_observations` / `concepts` / `entities` / `crawl_items`（另有既有的 `crawl_runs` / `health_config`），共 **6 张表**；用 `fd`（表 owner）执行，可 `REVOKE` 回滚。**CI 端到端实证**（不是本地模拟）：工作日流第 4 次运行 → `success`，日志里四条规则全部执行——斗南日价 `window_learning`、周环比 quiet `4.3675%`、邻接利润率 crossed `−89.493%`（EDBL）、Comtrade 花卉出口 quiet `+147.93%`、EU 花卉面积 crossed `−2.263%`、事件台账 `28 条已入账 0 新增`，`flower_alerts exit=1`（有越阈，预期）。**踩坑记录**：授予是一张表一张表暴露的（观测表→concepts→crawl_items），每补一张重跑一次 CI 才算确认——本地凭据是 `fd` 超级用户，看不出这种缺口 | 验收：已完成 |




| F18 | ~~P2~~ → **已处理（手动触发）** | EU 花卉主面积没有刷新调度 | 已并入同一工作流的 `workflow_dispatch` 步骤（`refresh=true`）：Eurostat 面积 / AIPH / Comtrade 花卉三档都是**年频**，每日空跑会白打 6 次外部 API，故不挂 cron；写库需要 `FD_OPEN_DATA_DSN_WRITE`（现有 `FD_CENTRAL_PG_DSN` 是只读账号 `fd_health_ro`），未配置时**明确 SKIP 不假装跑过** | 验收：配好该 secret 后，每年点一次 `Run workflow` 即可。**残余**：完全自动化需在集群侧加写库 CronJob（镜像 entrypoint 是 fd-runner，跑脚本需全量 checkout + 依赖），另立即可 |


| F19 | ~~P1~~ → **已修（建仓并推送）** | 看板代码不在任何 git 仓 | 本地建仓（`19ae3a7`，**104 个源文件**，忽略 `*.html`/`*.spec.json` 生成物）→ **已在 Gitee 建私有仓 `FindDataTechnology/fd-dashboards` 并推送**（默认分支 master 与 main 同提交，Gitee 侧 master 为默认分支、API 删不掉，不影响使用）。仓内 README 写明结构、四条踩坑与运行方式 | 验收：已完成（`git diff` 可复核板面口径变更）|



| F20 | ~~P1~~ → **已修** | 集群运行的 `--limit 100` **静默截断**滚动窗 | 实证：集群首次运行 `rows_written=100`（恰好等于 limit）只覆盖 **5 天**；同一 Job 在声明 limit 后重跑 → **932 行 / 42 天**。一天一版报告最多 46 条报价，所以 100 只够最近约 2 天，上游跳日一多就漏天且**不报错**。修法（照酒店线先例，AppSet 已支持 limit 透传）：`dounan-flower-price: limit 1200`、`aiph-stats: limit 300`；push 后 ArgoCD 同步生效（Application 参数 `limit=1200`，CronJob args `--limit 1200`） | 验收：已完成（真实运行实证 100→932 行） |

## 1bis. 五项挂账的裁决记录（2026-11-08，用户授权自行裁决）

裁决口径（用户原话）：「如果涉及需要组件或者很复杂的需求，先调查平台是否已有工具，再看是否有开源项目，
很复杂的可以不做」。逐项调查结果：

| 项 | 平台是否已有 | 开源方案 | 复杂度 | 结论 |
|---|---|---|---|---|
| F4 板面发布 | **有**（fd-wire `import_showcase.py` 就是行业看板批量导入；`artifacts` 支持 dashboard + 版本链） | — | 低 | **已执行到草稿**（id 194/195/196），发布待人工确认或补 evidence |
| F5 美股分部 | 无（companyfacts 已剥维度；fd-cn-report 是中文 PDF 路线） | arelle 2.47.4 / python-xbrl 1.1.1 | **高**（瓶颈是逐家人工映射分部轴成员） | **不做**，保留 `awaken-flower-report-ingest` |
| F9 年鉴 OCR | 无 | tesseract（本机已有） | **高**（146 页统计表，需版面对齐 + 人工校对） | **不做** |
| F14 年频 SLA | 有（规则 1c `min_rows_per_run` + `reconcile_absent`） | — | 低（已在用） | **不新增 publication**，既有机制覆盖 |
| F19 看板版本控制 | 无 | git（本机已有） | 低 | **已建仓并推送** `fd-dashboards` |

三条「不做」的共同理由：不是做不到，而是**投入产出不划算**——数据面增量窄（分部/OCR）
或既有机制已覆盖（年频），而成本是可维护性（人工映射、OCR 校对）。

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
