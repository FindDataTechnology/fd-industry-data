# un-comtrade 接入档案

UN Comtrade（联合国商品贸易统计数据库）年度 HS 贸易流。入口：
`run_un_comtrade(limit: int = 100, reporter_code=156, period="2023", cmd_code="854142", flow_code="X", partner_code=0) -> list[dict]`，
返回行 schema 与 manifest `functions[].columns` 对齐，每行带 `source` / `source_url` / `scraped_at`，`limit` 生效。

- 网络：海外源，直连即可（2026-10-07 实测 200，无反爬）。
- 依赖：**仅标准库**（`urllib.request` / `json` / `os`），无 scrapling、无新增依赖。

## 端点与鉴权

- 生产端点：`https://comtradeapi.un.org/data/v1/get/C/A/HS?reporterCode=...&period=...&cmdCode=...&flowCode=...&partnerCode=...&maxRecords=...`
  （路径段 `C/A/HS` = 商品型/年度/HS 分类，已实测锚定）
- 鉴权：**请求头 `Ocp-Apim-Subscription-Key: $COMTRADE_API_KEY`**（env 注入；代码兼容
  `COMTRADE_SUBSCRIPTION_KEY` 回退以适配 Azure APIM 双 key primary/secondary 轮换）。
  key 只从 `os.environ` 读取，绝不硬编码/入文件/入 URL。
- 免费订阅层 **500 次/天**——本爬虫每次调用只发 **1 个请求**，控频由调用方负责。
- **preview 端点（`/public/v1/preview`，免 key）仅开发期对照**：单次 ≤500 行、中国月度止于
  2024-12（实测）——生产取数一律走 `data/v1/get` + 订阅 key。

## 行字段口径

`count` / `data[]` / `error` 三键。`data[]` 记录字段 snake_case 透传：
`period`（年度为字符串 "2023"）、`ref_year`、`reporter_code`、`flow_code`（X 出口/M 进口）、
`partner_code`（0=世界）、`classification_code`（上游实际使用的 HS 修订版，2023 年为 H6）、
`cmd_code`、`qty` / `net_wgt` / `gross_wgt` / `fob_value` / `cif_value` / `primary_value`
（上游 JSON number 原样透传，null → None 绝不造数）、`is_reported` / `is_aggregate`。

## 容错规则（失败即红）

- env key 缺失 → 抛 `RuntimeError`（拒绝裸跑）。
- 非 200（含 HTTPError 响应体摘要）/ 连接层异常 → 抛 `RuntimeError`，不重试放大。
- 上游 `error` 字段非空或 `data` 为空 → 抛 `RuntimeError`（转人工，不静默返回空列表）。

## golden 锚（冻结值，2026-10-07 实况）

锚行 156（中国）/ 2023 / 854142（锂离子蓄电池）/ X（出口）/ partner 0（世界），
年度定稿历史值不再变化：

| 字段 | 冻结值 |
|---|---|
| `primary_value` | 4154890471.0 |
| `net_wgt` | 92282836.464 |

`golden/001-un-comtrade.json` 另锚 `reporter_code=156`、`period="2023"`、`flow_code="X"`、
`cmd_code="854142"`、`partner_code=0`、`min_rows=1`。`maxRecords` 跟随 `limit`（钳 100000）。

## 真实取数证据（2026-10-07，直连）

`GET /data/v1/get/C/A/HS?reporterCode=156&period=2023&cmdCode=854142&flowCode=X&partnerCode=0&maxRecords=2`
→ `count=1`，唯一记录：`primaryValue=4154890471.0`、`fobvalue=4154890471.0`、`qty=5209311702.0`、
`netWgt=92282836.464`（`isNetWgtEstimated=true`）、`customsCode=C00`、`classificationCode=H6`。

冒烟（构建时实况）：`python3 spiders/un-comtrade/spider.py` → 1 行，
`period="2023"` / `reporter_code=156` / `cmd_code="854142"` / `primary_value=4154890471.0`。

## 本地自检

```bash
source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env
cd <仓根> && python3 spiders/un-comtrade/spider.py                 # 冒烟
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-un-comtrade-d0c86639.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py \
  && python3 scripts/conformance_gate.py --roots .
```
