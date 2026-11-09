# shclearing-capital-notes 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`shclearing-capital-notes`；入口 `run_shclearing_capital_notes`）
- [x] `spider.py` 提供 `run_shclearing_capital_notes(limit=, keyword=) -> list[dict]`，纯标准库
- [x] **TRS search 端点实读**：`POST /shchapp/web/disclosureForTrsServer/search`，无需 Cookie
- [x] **两栏目覆盖**：ch166 交易流通要素公告（流通，pageTotal 369,062）/ ch144 发行披露（发行，521,622）
- [x] **分页实读**：`start` + `limit`（ExtJS 口径）；`pageNo/pageSize/page/curPage` 实测被忽略
- [x] **必需 header 实读**：UA + 同栏目 Referer + `X-Requested-With` + 表单 Content-Type
- [x] **检索词口径**：默认「二级资本债券」「无固定期限资本债券」；`keyword` 给定时只抓该词
- [x] **两栏目预算分配**（ch166 `limit//2`，ch144 剩余）——防止 ch166 吃满 limit 使 ch144 永不触达
- [x] **详情页字段抽取**：产品代码/简称/全称、发行（创设）总额、期限、票面年利率、产品评级/主体评级/评级机构、四类日期、计息方式
- [x] **单位与百分号清理**：`1.72000000` → `1.72`（`coupon_unit=%`）；`5.000000` → `5.0`（`amount_unit=亿元`）；期限 `10年` → `term_years=10.0`
- [x] 缺失口径一致：数值/文本缺失一律 `null`，上游 `-`/`--`/`—`/`…` 视为缺测，**不写 0**
- [x] HTML 表格走共享层 `fd_industry_data.tabular`（`read_grid(payload, table=i)`），无内联表格解析
- [x] **ch144 形态留痕**：发行披露详情页 8/8 抽样无 label-value 要素表 → 出公告级行（数值 null），不伪造要素
- [x] 分页有界：每个（栏目, 关键词）≤ `MAX_PAGES=12` 页（修复无匹配时遍历整库的问题）
- [x] 网络礼节：请求间隔 `SLEEP_S=1.0`（≥1s）
- [x] 失败策略：检索失败留痕续跑、详情失败跳过、全空 `RuntimeError`、`limit` 真实截断
- [x] golden 锚「26无锡农商行二级资本债01」**实测可得**（代码 232680049，检索第 1 条），未替换
- [x] manifest 字段齐全；`data_period: day`；`schedule: "26 14 * * 1-5"`；两个声明参数均可达
- [x] golden schema 通过；replay 实测 `ok=True rows=5 diffs=[]`
- [x] 本地自检：`validate_manifests`（143 manifests，0 violations）；`conformance_gate` 对本单元 0 违规
      （全仓 FAIL 的 2 条来自同期其它未完成单元 `cba-wealth-index` / `ratings-ccxi` 缺 manifest，非本单元）
- [ ] push 后 CI gate 绿
- [ ] 点亮后试跑（确认 crawl_runs 记录与首爬行数；`limit=20` 预期 20 行，14:26 工作日）
