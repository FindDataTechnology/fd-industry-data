# sec-edgar-facts — 美股车道（SEC EDGAR XBRL + filings）

> 餐饮垂直线波2 · 任务 3.3 · `kind=generate` 工单：`reports/health-tickets/20261107-sec-edgar-facts-*.yaml`

## 车道属性与边界注记（spec 强制）

**本车道属性：金融车道扩展。2026-10-07 侦察簿边界注记**：裁决理由 = 防重叠；美股（SEC EDGAR）在平台此前**无既有财报车道**，本扩展不构成与既有财报线的重叠。本单元只取 XBRL 事实与 filings 元数据（事件面 + 白名单字段），**不解析 10-Q/10-K 正文全文**。

## 数据面

| 项 | 值 |
|---|---|
| 事实面 | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` |
| 事件面 | `https://data.sec.gov/submissions/CIK##########.json`（`filings.recent`） |
| CIK 映射 | `https://www.sec.gov/files/company_tickers.json` |
| 鉴权 | 无 key；SEC 要求 `User-Agent` 带应用名 + 联系邮箱（常量 `USER_AGENT`） |
| 限速 | 串行 + 0.15s（SEC 明示 ≤10 req/s） |
| 通道实测 | 本机直连可用；首次 CIK 映射握手超时一次，重试即通（spider 内有重试） |

## 宇宙（跨垂直线，`universes/us.yaml`）

| 垂直线 | 家数 | 票符 |
|---|---|---|
| 餐饮（`docs/restaurant-universe.yaml`） | 12 | MCD / SBUX / YUM / CMG / QSR / DRI / DPZ / WEN / TXRH / BROS / WING / EAT |
| 花卉（`docs/flower-universe.yaml`） | 5 | FLWS / CENT / SMG / HYFM / EDBL |

缺省参数取 `universes/us.yaml` 全量（17 家）。**新增垂直线后若不显式传 `tickers`，请确认底账里真有目标公司的
fact 行**：2026-11-08 曾出现「车道声明了花卉 5 家、底账只有餐饮 12 家」的假绿灯——单元跑过 ≠ 这几家跑过。
按垂直线分批抓（`--params '{"tickers":["FLWS","CENT","SMG","HYFM","EDBL"],"quarters_back":26}'`）
比全量重抓省请求，也让底账的归属可追。

## 输出两态（`kind`）

- `fact`：白名单 tag 的季度值（`fy/fp/start/end/filed/tag/unit/value`）
- `event`：10-Q/10-K/8-K 申报事件（`filed/report_date/accession/primary_doc_url`）——**财报日历**的原料

白名单 tag 与逐家可用性见 `docs/sec-edgar-tag-map.md`，那里也记了 **分部数据不可得**的真实结论，
以及花卉线 2026-11-08 的分部断点（HYFM 换用含代收税 tag：留证、不拼接）。

## 口径纪律

1. **精确 tag**，不做前缀/模糊匹配（模糊匹配会把 `RevenuesNetOfInterestExpense` 之类混进来）；
2. **只取单季窗**（跨度 70–110 天）：XBRL 同一 tag 同时报单季与 fiscal-YTD，混写会撞自然键并造成量级误读；
3. **重述取最新 `filed`**；
4. **只收 USD 金额**（`USD/shares`、`shares` 不进金额概念）；
5. **不插值不补季**：未申报的季度缺席。

## 实测（2026-11-08）

| run | 参数 | 结果 |
|---|---|---|
| 4804 | `quarters_back=8` | success 693 行（事实 272 + 事件 421） |
| 4806 | `quarters_back=24` | success 2,098 行 |
| 4807 | `quarters_back=26` | success 2,257 行 |
| 抽取（餐饮） | — | 796 行过门 / 172 新增（累计）；81 行按 <24 期显式剔除（BROS 19 期、QSR 净利 8 期、TXRH 净利 16 期） |
| 4836 | `quarters_back=26` + 花卉 5 票符 | success 831 行（花卉线上线前，白名单尚无 Including tag） |
| 4837 | 同上，白名单加 `…IncludingAssessedTax` | success 835 行；抽取 **329 点**，不套 24 期门（短史如实保留） |

## 用法

```bash
source ~/.config/finddata/dashboards.env
FD_CRAWL_DB_URL="$FD_OPEN_DATA_DSN" FD_WRITER_MAX_ITEMS=80000 \
  ./.venv/bin/python -m fd_industry_data.runner_cli sec-edgar-facts \
  --limit 40000 --params '{"quarters_back":26}'
```

**无 `schedule`**：静默合入，点亮走人工门。
