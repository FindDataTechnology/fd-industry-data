# hkex-disclosure 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`hkex-disclosure`；入口 `run_hkex_disclosure`）
- [x] `spider.py` 提供 `run_hkex_disclosure(limit, stocks, days_back, max_pages) -> list[dict]`，
      只依赖镜像预装依赖（scrapling + stdlib；无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 `www1.hkexnews.hk` 公开 HTTPS JSON，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] 数据周期 `data_period: irregular`（公告不规则发布：结果/盈警/月报表节奏不同）
- [x] 两段式取数：① `prefix.do` 解析内部 `stockId`（精确 code 匹配 + 进程内 dict 缓存）
      ② `titleSearchServlet.do` 拉公告（`result` 二次 `json.loads`）
- [x] **与任务单 URL 的有据偏离**：`t1code=40000` 实测只返回 Financial Statements/ESG
      （06862 2026 YTD 3 行 vs 全部 36 行），会静默丢盈警/月报表/一般公告；改用 `-1`（全部），
      证据见 README §1④
- [x] **宇宙校正**：任务单「00965 特海国际」有误；上游 `prefix.do` 只给 **09658 特海國際**
      （stockId 1000179492），本单元按上游事实用 09658
- [x] 分页：无 page offset，按 `rowRange=100*N` 递增重取 + `NEWS_ID` 去重；
      `hasNextRow` 在 `max_pages` 后仍 true → **判红**，不返回截断数据
- [x] `ann_type` 显式规则表（`ANN_TYPE_RULES`，优先级顺序；命中多个取首个并留
      `ann_type_hits`）；含繁体实测形 `盈利預警` / `正面盈利預告`
- [x] 判红清单：stockId 未匹配 / 非 200 / 响应不可解析 / `result` 非数组 /
      `recordCnt` 不符 / 分页未收敛 / 空结果 / `DATE_TIME` 不可解析 / EN 有行而 ZH 全空
- [x] golden `golden/001-hkex-disclosure.json`（锚海底捞 `release_date=2026-08-25` +
      `ann_type=results`；`whitelist_fields: ["scraped_at"]`）
- [x] 头条数字（3.2 验收）：results 类公告走**最小化标签行抽取**
      （`hkex_headline.py` + `fd_industry_data.pdftext`），每 (股票, 期别) 只抽最新
      业绩公告；营收 = `Revenue/Total revenues` 行，利润 = **归母**行
      （`Profit attributable to owners/equity shareholders`，不取含少数股东的
      `Profit for the period`）；期别来自标题并回验 PDF，币种/单位来自表头
- [x] 10/10 港股实测可辩护（8 家中报 6M + 2 家年报 12M），逐家数字/原文行/PDF 入口
      落 `docs/hkex-results-headline-caliber.md` §7
- [x] 歧义一律不可得：原因码表（`no_text_layer` / `unit_not_found` /
      `column_header_not_found` / `revenue_and_profit_not_in_same_table` /
      `ambiguous_revenue|profit` / `conflicting_units` / `not_selected` /
      `pdf_too_large` / `internal_error` …），**不猜数字**
- [x] **明确不写 semantic_observations**：半年粒度（词表无 `half`）+ 每窗 1 期
      （<12 期趋势门）→ 事件 payload 承载；裁决项见 caliber §8/§9
- [x] golden 重放绿：`health.guard.replay_sample` → ok=True, rows=37, diffs=[]
- [x] 真实落库（含头条）：runner `--limit 1000 --params '{"days_back":180}'` → run **4815**
      **success**，rows_written=522，窗口 2026-05-12..2026-11-06，10/10 股票有行；
      90 天配方 run **4814** success rows=226
- [x] `headline_status=ok` 覆盖 **10/10**（8 家中报 6M + 2 家年报 12M），逐家数字见
      `docs/hkex-results-headline-caliber.md` §7；两次独立运行（4814/4815）8 家重叠
      公司头条数字**逐条一致**（确定性）
- [x] `semantic_observations` 中 `source_used='hkex-disclosure'` = **0 行**（设计如此：
      半年粒度无 `half` 词条 + 期数 <12 趋势门；非漏写，裁决项见 caliber §8）
- [x] **细分 KPI（3.5 一期）**：`hkex_segment_kpi.py` 白名单抽取，06862 海底捞 11 项 +
      09922 九毛九 13 项全部带「报告期 + 分部/范围 + 单位 + 逐字标签行 + 公告脚注原文」
      （翻台率 / 人均消费 / 同店销售额 / 同店家数 / 门店数 / 同店日均销售同比）；
      分区标题 + 脚注号 + 行标签三者逐字相等才取，数值按 `-layout` 字符列位落列
      （四列表只取前两组），金额千元归一为原币整数，百分比按百分数存
- [x] 细分 KPI 不可得显式：海底捞 `same_store_sales_growth_overall`
      （`no_labelled_line`：不拿两年同店销售额相除）、`new_opened_restaurants` /
      `closed_restaurants`（`narrative_only`：只在业务回顾叙述段）；其余 8 家
      `kpi_status=not_applicable` + `kpi_reason=no_phase1_kpi_target`，无空值冒充
- [x] 逐家逐项口径档案 `docs/hkex-segment-kpi-caliber.md`（含 PDF 原文片段、
      payload 字段契约、SQL、不可得台账）；README §4.1 同步
- [x] 真实落库（含细分 KPI）：run **4829** **success**，rows_written=226，
      06862 `partial`（11 落地 + 3 不可得）、09922 `ok`（13/13）；`headline_*`
      与 `kpi_*` 共用同一次 PDF 下载/抽文（不重复取数）
- [x] 本地自检全绿：`validate_manifests.py` 0 违例；`conformance_gate.py` 仅既有违例
      （jp-foodservice-trend / sec-edgar-facts 缺 manifest），本单元 0 新增
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——人工门
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身

注（头条数字边界）：本单元对 results 类公告只做**最小化标签行抽取**（营收 + 归母
利润 + 期别/币种/单位 + 原文行），不解析正文全文、不做版面重建/OCR。抽取结果写
payload 的 `headline_*` 字段（事件与数字同一条记录），**不写 semantic_observations**
（半年粒度缺 `half` 词条 + 期数 <12 趋势门）；原因码与逐家覆盖见
`docs/hkex-results-headline-caliber.md`。
