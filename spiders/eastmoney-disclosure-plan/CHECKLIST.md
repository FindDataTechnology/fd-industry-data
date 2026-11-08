# eastmoney-disclosure-plan 接入检查清单（自建单元，2026-11-07）

- [x] 目录名 = manifest `name`（`eastmoney-disclosure-plan`；入口
      `run_eastmoney_disclosure_plan`）
- [x] `spider.py` 提供 `run_eastmoney_disclosure_plan(limit: int = 200) -> list[dict]`，
      只依赖标准库 `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`datacenter-web.eastmoney.com/api/data/v1/get`
      `reportName=RPT_PUBLIC_BS_APPOIN` 本机 HTTP 200（免 key、免鉴权），
      落地页 `data.eastmoney.com/bbsj/` HTTP 200（见 README §4 证据表）
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定
      https 官方主机）
- [x] manifest 字段齐全：version/name/label/source_url/schedule/data_period/
      scanner_mode/functions（command/frequency/parameters/columns）/entities/fetch；
      columns 10 项与输出行 10 键一一对应
- [x] `schedule: "32 12 * * *"` 逐字按批次六排期；`data_period: day`
- [x] **filter 整体 urlencode**（头号坑）：`urlencode(query)` 一次编码，
      代码内无手工拼接/二次编码；README §3 有四种编码形态的实测对照
- [x] 银行筛选用 `in` 批量表达式，42 家 A 股银行代码内嵌 `BANK_CODES`
      （来源侦察簿 791 行 / 东财 BK0475 total=42，2026-11-07 复核 42/42 命中）
- [x] `REPORT_TYPE` 1/2/3/4 → 一季报/半年报/三季报/年报；未知值抛
      `RuntimeError`（不猜）
- [x] 窗口：首爬 `FIRST_APPOINT_DATE >= 2025-01-01`（实测 336 行）；
      `limit` 超过窗口自动放宽到 2007 全史（实测 2,080 行）；回溯下限
      `HISTORY_START=2007-01-01`（上游最早 2007-03-14）
- [x] 输出按 `first_appoint_date` 倒序、同日按代码/报告类型升序，确定性；
      `limit` 真实生效（limit=200 → 200 行，limit=400 → 400 行）
- [x] `plan_status` 本地派生：已披露（实际日非空）/ 已变更（变更日或
      `APPOINT_CHANGE` 非空）/ 预约中；`actual_publish_date` 空值保留 null
- [x] golden 样本 `golden/001-eastmoney-disclosure-plan.json`（`limit=200`
      重放）：锚工行 FY2025 年报三元组（预约 2026-03-31 / 变更 2026-03-28 /
      实际 2026-03-28）+ source/stock_code/fiscal_year/report_type；
      `scraped_at` 进 whitelist；rows_contains 只用 `{field, equals}`
- [x] 分页按 `result.pages` 翻页；`len(rows) != count` 即抛错（不返回残缺数据）
- [x] 请求失败/非 JSON/`success!=true`/缺结构/缺字段/未知 REPORT_TYPE/0 观测行
      均抛 `RuntimeError`（失败即红，绝不静默回空）；单请求单尝试
- [x] README 记端点/字段映射/42 家清单来源/filter 编码坑/`and` 静默 0 行坑/
      实测证据（HTTP 码、行数、工行三元组实取值）/失败策略/消费方注记
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .`（不新增本单元违规）/ golden
      validate+replay 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：看 crawl_runs 记录与产出（日频，单次约 1 请求；limit > 336
      时 5 请求）
