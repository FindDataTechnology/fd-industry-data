# spiders/jp-yahoo-events —— 日本个股定期披露事件（无键）

## 这个单元解决什么

`add-restaurant-vertical` 3.4 的验收是「**泉盛/萨莉亚最新年度披露事件可查**」。两条既有路都不够：

- **TDNet**（`spiders/jp-tdnet`）只保留约 31 天 → 覆盖不到"最新年度"；
- **EDINET API v2** 需要 `Subscription-Key`（`data-sources@` 申请）。

本单元走 **Yahoo Finance 公开端点（与 `yfinance` 同源）**，无键取到：
已披露各期实际 EPS（`earningsHistory`）+ 下次決算発表日（`calendarEvents.earnings`）。

## 访问契约（2026-11-09 实测，经集群认证代理）

| 步 | 请求 | 判据 |
|---|---|---|
| 1 | `GET https://finance.yahoo.com` | **必须用首页**——`/quote/<tk>` 不发 cookie，0 cookie 时 crumb 必 401 |
| 2 | `GET https://query1.finance.yahoo.com/v1/test/getcrumb` | 返回 11 字符 crumb（不带 `<`） |
| 3 | `GET .../v10/finance/quoteSummary/<tk>?modules=earningsHistory,calendarEvents&crumb=<crumb>` | JSON |

首页 seed **偶发 401 / 不发 cookie**，故内部重试 4 次（实测第 1 次失败、第 2 次成功）。

## 实测锚（2026-11-09，经代理）

```
7550.T 泉盛   earnings_result 2025-12-31 eps 72.42
7550.T 泉盛   earnings_result 2026-06-30 eps 91.2
7550.T 泉盛   earnings_date   2026-11-12 (非预估)
7581.T 萨莉亚 earnings_result 2025-11-30 eps 62.95 surp 9.71%
7581.T 萨莉亚 earnings_result 2026-08-31 eps 47.64 surp -29.48%
7581.T 萨莉亚 earnings_date   2027-01-13 (预估 isEarningsDateEstimate=true)
```

## 口坑

1. **crumb 与 cookie 绑定**：换 jar 必重取 crumb；只取 cookie 不取 crumb 必 401。
2. **首页才是有效 seed**：`/quote/<tk>` 不发 cookie（实测 0 个）。
3. **上游空对象 `{}` 表示缺字段**：`_num()` 返回 `None`，**不补零、不推断**。
4. **`isEarningsDateEstimate` 必须透出**（`date_is_estimate`）——预估日期不能当既成事实。
5. 单标的失败记 stderr 继续；**全部失败**才抛 `RuntimeError`（失败即红）。
6. 夹具放 `fixtures/`（**不能放 `golden/`**）：`conformance_gate.py` 会把 `golden/*.json`
   当期望描述解析，原始响应放进去会报 `golden-schema-invalid`。

## 用法

```bash
run_jp_yahoo_events()                                   # 4 家日股，真网络
run_jp_yahoo_events(tickers="7550.T", limit=10)          # 单标的
run_jp_yahoo_events(fixture="spiders/jp-yahoo-events/fixtures/001-quotesummary.json")  # 零网络重放
```

## 与 jp-tdnet 的分工

- `jp-tdnet`：**近 31 天**的适时应报（決算短信 / 業績予想修正 / 自己株式…），按 TSE 代码过滤；
- `jp-yahoo-events`：**定期披露事件**（各期业绩实际 + 下次決算発表日），补足历史与前瞻。
两者互补，缺一不可；均无键、均经调度环境的 `HTTP(S)_PROXY`。
