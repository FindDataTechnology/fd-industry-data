# spb-express 接入档案（国家邮政局快递月报）

- slug：`spb-express`；入口：`run_spb_express(limit: int = 100) -> list[dict]`
- 频率：monthly；鉴权：无（公开站点，免登录）
- 网络：国内源，**直连**（未走代理）。实测直连全部成功：【直连=全部成功 / 代理=未使用】

## 端点

| 用途 | URL |
| --- | --- |
| 运行情况栏目列表 API | `https://www.spb.gov.cn/common/search/ce2d4d5628314ff7a81553529fb7f6a0?_isJson=true` |
| 快递发展指数栏目列表 API | `https://www.spb.gov.cn/common/search/49357e62dbcc4693aa91b3685619cb88?_isJson=true` |
| 文章正文页 | 列表项 `results[].url` 给出，**为 http，301 到 https**，必须 `follow_redirects=True` |

- 列表响应：`data.total`（运行情况栏目 522 条、发展指数栏目 119 条，2026-10-02 实测）、`data.results[]`（每页固定 20 条，含 `title/url/publishedTimeStr/content` 摘要）。
- 运行情况文章按标题含「邮政行业运行情况」过滤（约 2018-03 至今 100+ 期）；发展指数文章按标题含「快递发展指数」过滤。

## 参数白名单（只发实测有效参数）

| 参数 | 位置 | 实测结论 |
| --- | --- | --- |
| `_isJson=true` | query | 有效，返回 JSON |
| `page=N` | query | 有效，实测 `page=2` 返回第 2 页（响应 `data.page=2`） |
| `pageNo` / `pageSize` / `rows` | query 或 POST form | **实测无效**（服务端忽略，恒返回 20 行/页），一律不发 |

## 容错规则

- HTTP 走 `scrapling.fetchers.FetcherSession(impersonate="chrome120", timeout=25, verify=False, follow_redirects=True, retries=1)`，顺序抓取、每篇间隔 0.2s。
- 5xx/空响应/JSON 解析失败 → 记录 WARNING 后跳过该页/该篇，**不重试风暴、不绕反爬**（scrapling `retries=1` 仅防传输层抖动）。
- 解析失败留空：正文缺 CR8 的期（如 2022-01 期正文确实未刊 CR8）→ `cr8=None`，不写脏行；发展指数类文章仅保留元数据行，数值字段全部 None。
- `period` 无法从标题推导的行丢弃并告警。

## 字段口径

- `period`：标题期号归一为累计截止月 `YYYY-MM`（`2026年1-8月`→`2026-08`；`上半年`→`-06`；`一季度`→`-03`；`N月份`→单月；全年→`-12`）。
- `expr_volume`：正文「快递业务量累计完成X亿件」（亿件）；`revenue`：「快递业务收入累计完成X亿元」（亿元）。仅解析正文明示值；早期（约 2023 年前）月报数值在附表中且单位为万元，不做单位换算抓取，两列留 None。
- `cr8`：**优先业务量口径**「快递业务量品牌集中度指数CR8为X」（新版月报分列收入/业务量两个 CR8）；旧版单一「快递与包裹服务品牌集中度指数CR8为X」直接取用；两者皆无则 None。
- `growth_yoy`：累计同比摘要字符串，如「业务量同比4.6%，收入同比7.4%」。

## 历史深度（limit=100 实测）

- 一次 `run_spb_express(limit=100)` 返回 100 行（全部为运行情况栏目；发展指数栏目在 limit 超出运行情况条数时补充成行）。
- 序列首值：`2026-08`；末值：`2018-03`（标题「国家邮政局公布2018年一季度邮政行业运行情况」，CR8=80.7）。
- CR8 缺失期（实测）：`2022-01`（正文未刊）。

## 真实取数证据（取数时间 2026-10-02 04:15–04:16 CST，直连）

最近 3 期运行情况（验证门槛）：

| 期号 period | 业务量 expr_volume（亿件） | 收入 revenue（亿元） | CR8（业务量口径） | 收入口径 CR8（正文原文，备查） |
| --- | --- | --- | --- | --- |
| 2026-08 | 1340.6 | 10289.2 | 96.2 | 86.9 |
| 2026-07 | 1174.7 | 9017.7 | 96.2 | 87.0 |
| 2026-06 | 1003.8 | 7714.1 | 96.1 | 87.0 |

- 三期 CR8 均解析成功，验证门槛满足；收入口径 CR8 为 README 备查记录，schema 内 `cr8` 统一取业务量口径。
- 深度抽查：`2023-10` vol=108.3 / rev=9643.8 / cr8=84.1（与正文原句「快递与包裹服务品牌集中度指数CR8为84.1」一致）。

样例行（1 条，limit=100 运行首行）：

```json
{"period": "2026-08", "title": "国家邮政局公布2026年1-8月邮政行业运行情况", "url": "https://www.spb.gov.cn/gjyzj/c100015/c100016/202609/5da542159e1449b4ba8334b6054e0718.shtml", "expr_volume": 1340.6, "revenue": 10289.2, "cr8": 96.2, "growth_yoy": "业务量同比4.6%，收入同比7.4%", "channel": "运行情况", "scraped_at": "2026-10-01T20:16:25.342242+00:00", "source_url": "https://www.spb.gov.cn/gjyzj/c100015/c100016/202609/5da542159e1449b4ba8334b6054e0718.shtml"}
```

- 总条数：`limit=100` → 100 行（2026-08 → 2018-03）；`limit=3` → 3 行。
- 校验：`scripts/validate_manifests.py` → 63 manifests, 0 violations；`scripts/conformance_gate.py` → PASS（65 units, 0 violations）。

## 遗留问题

- 早期期数（约 2023 年前）`expr_volume/revenue` 留 None（数值仅在附表且单位为万元，未做换算抓取）；CR8 仍可解析。
- 发展指数栏目行仅为元数据（无对应 schema 数值字段）；正文中的快递发展指数值未采集。
- scrapling 0.4.12 下响应 `.text` 可能为空，已改为从 `.body`（bytes）解码（UTF-8 为主，兼容页面声明 gb 系时按 gb18030 解码）。
