# jp-yahoo-events 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`jp-yahoo-events`）；入口 `run_jp_yahoo_events`，
      与 `run_<slug>`（连字符转下划线）契约一致
- [x] `spider.py` 只依赖标准库（`json/time/urllib/http.cookiejar`），无第三方依赖
      （因此**不需要装 `yfinance`**；端点与它同源）
- [x] manifest 字段齐全：version/name/label/source_url/data_period/functions
      （command/frequency/parameters/columns）/entities/fetch；`columns` 8 项与输出行 8 键一一对应
- [x] `schedule` **不写**（静默合入，点亮留人工门）
- [x] `site` 未写（只访问 finance.yahoo.com 与 query1.finance.yahoo.com 公开端点）
- [x] 参数声明与入口签名一致：`limit`/`tickers`/`fixture` 均在
      `run_jp_yahoo_events(limit=50, tickers=None, fixture=None)` 内
- [x] **cookie+crumb 契约**：首页 seed → `/v1/test/getcrumb` → `quoteSummary&crumb=…`；
      `/quote/<tk>` 作 seed 不发 cookie（实测 0 个 → crumb 401），故 seed 固定用首页
- [x] **重试**：首页 seed 偶发 401/无 cookie，内部重试 4 次（实测第 2 次成功）
- [x] **不造数**：上游 `{}` 空对象 → `None`（`_num()`），不补零不推断；
      `isEarningsDateEstimate` 透出为 `date_is_estimate`
- [x] 失败即红：会话建立失败 / quoteSummary 无 result / 全部标的失败 → `RuntimeError`；
      单标的失败记 stderr 后继续
- [x] golden `golden/expected.json`（gate schema）：`min_rows=3` + 4 锚
      （`ticker=7550.T`、两种 `event_kind`、`source=jp-yahoo-events`）；
      只锚结构，**不锚日期/EPS**（波动值不入断言）
- [x] 夹具 `fixtures/001-quotesummary.json`（真实响应，1,955 B）——
      **必须放 `fixtures/` 而非 `golden/`**：`conformance_gate.py` 会把 `golden/*.json`
      当期望描述解析，原始响应放进去会报 `golden-schema-invalid`
- [x] 验证：`python3 scripts/conformance_gate.py` → `PASS — 164 units, 0 violations`
- [x] 离线重放：`fixture=…` → 3 行（2 期业绩 + 1 个下次披露日），字段齐全
- [x] 真网络实测（2026-11-09，经认证代理）：`7550.T` 2 期 EPS + 下次 2026-11-12；
      `7581.T` 4 期（含超预期 %）+ 下次 2027-01-13（预估）
