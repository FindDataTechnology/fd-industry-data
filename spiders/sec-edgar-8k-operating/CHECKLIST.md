# sec-edgar-8k-operating 接入检查清单（add-hotel-vertical 任务 4.3）

- [x] 目录名 = manifest `name`（`sec-edgar-8k-operating`；入口 `run_sec_edgar_8k_operating`）
- [x] `spider.py` 提供 `run_sec_edgar_8k_operating(limit=80, tickers=None, filings_back=4) -> list[dict]`
      ——只依赖标准库 `json/urllib/gzip/re/datetime/time`
- [x] manifest 字段齐全：version/name/label/source_url/data_period/scanner_mode/ranking_seed/
      functions/columns/entities/fetch（`data_period: irregular`：各家用 4 周内错开日期发布，
      词表无 quarter，`month` 会在两季之间假超期）
- [x] manifest `columns` 与实际产出字段逐一对应（15 列，已核对）
- [x] `site` 未写——本机直连 `data.sec.gov` / `www.sec.gov` 实测可用（走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] 参数可达性：三个参数（`limit` / `tickers` / `filings_back`）都在入口签名里具名
      （`**kwargs` 不算），conformance gate `declared-param-reachability` 通过
- [x] golden 样本 `golden/001-sec-edgar-8k-operating.json`：`params {limit:20, filings_back:2}`，
      `min_rows 5`，`rows_contains` 只用了 `{field, equals}`（本仓契约只支持这一种），
      锚 `source` / `status=ok` / `ticker MAR` / `ticker CHH` / `items="2.02,9.01"`；
      波动字段进 `whitelist_fields`
- [x] exhibit 选件**不猜文件名**：正文锚点 + 排除补充演示 + 显式「Exhibit 99.1」唯一，
      否则返回 `ambiguous-*` 状态；`pick_evidence` 随行可审计
- [x] 期间不从 `submissions.reportDate` 取（实测那是申报日：MAR 2026Q3 = 2026-11-02），
      改从 8-K 正文「quarter ended …」解析成 `period_end`，原值另存 `event_date`
- [x] 网络姿势：强制 `Accept-Encoding: gzip`（不带会卡死在 1.27 MB exhibit）、
      UA 带应用名+联系邮箱（否则 403）、请求间隔 0.15s、单次传输重试 1 次、
      失败即整次运行报错（绝不 0 行冒充「没有数据」）
- [x] 覆盖率矩阵实测入 `AVAILABILITY`（14 家：5 家 parsed / 5 家 6-K 申报人 /
      1 家无 RevPAR 披露 / 3 家 OTA），任务书「HTHT」一项证伪并留证
- [x] 抽取器 `scripts/hotel_extractors/sec_8k_operating.py`：39 个概念
      （3 指标 × 13 序列），逐家 mapping（MAR/HLT/WH/H/CHH 五套表形态）
- [x] 列归属由 `RevPAR = ADR × 出租率` 恒等式裁决，只接受唯一解；同值用表头列序旁证；
      篡改任一值即整行丢弃、兄弟行不受影响（测试覆盖）
- [x] 累计期块（Six/Nine/Twelve Months Ended、Year Ended、TTM Ended）**先判掉**
      ——这是本轮实测抓到的真缺陷：五家 FY 稿把累计块接在季度块后面，行名一模一样
- [x] 同一份 exhibit 内 (序列, 指标, 期间) 唯一性有守卫：出现两个不同值即留痕并取先出现者
- [x] 同比位破折号不丢行（H 自有酒店出租率同比「—」），量值缺失仍按形状不符丢弃
- [x] 温德姆「PRE Q2 2025 REPORTING BASIS」旧基准对照块整块不写，取已披露季度最多的那张
- [x] 空值语义：`n/a` / `—` = 未披露，**不写 0、不插值、不拿同业补**
- [x] 落库幂等：首次 `inserted = 297`，重跑 `inserted = 0`；污染值（114.39/185.78/
      150.96/52.08/36.13）在库内计数 = 0
- [x] 验收口径：5 家（MAR/HLT/WH/H/CHH）季度 RevPAR/ADR/出租率与公告表格一致，
      全行满足恒等式；任务书「至少 4 家（MAR/HLT/WH/HTHT）」的 HTHT 经实测证伪，
      以 H + CHH 替补
- [x] 测试：`tests/test_hotel_sec_8k_operating.py` 81 passed（含 6 份年度/半年稿真件回归）
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交——**人工门，建造代理不点亮**
- [ ] HTHT/IHG/ATAT/GHG/TCOM 的 6-K 通道单独立项（外国私人发行人，本车道不覆盖）
