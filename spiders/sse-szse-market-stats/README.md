# sse-szse-market-stats —— 沪深交易所每日成交概况（成交量/成交额/估值概览）

自建单元（沪深市场统计）。**免 key、免鉴权、直连可达**（无需代理出口，manifest
不声明 `egress_secret`）。每所最新交易日一行、共 2 行：
`(source, exchange, trade_date, turnover_amount, turnover_volume, avg_pe, source_url, scraped_at)`。

## 1. 数据表面

### 1.1 SSE（上交所）「每日股票情况」

| 项 | 值 |
|---|---|
| 页面 | `https://www.sse.com.cn/market/stockdata/overview/day/` |
| 端点 | `GET http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&PRODUCT_CODE=01%2C02%2C03%2C11%2C17&type=inParams&SEARCH_DATE=` |
| 鉴权 | **免 key、免鉴权**；但**必须带 `Referer: http://www.sse.com.cn/`**（实测缺失时返回空壳 `result:null`） |
| 频度 | 日更；`SEARCH_DATE` 空 = 最新交易日，也可传 `YYYY-MM-DD` 查历史单日 |
| 参数来源 | 页面脚本 `/xhtml/home/2021public/querySearch/search_stockData_2021.js` 的 `overviewDay` 块（sqlId/PRODUCT_CODE/type 四件套） |

响应（实测 2026-11-07，数据日 2026-11-06）：

```json
{"result": [
  {"PRODUCT_CODE": "01", "TRADE_DATE": "20261106", "TRADE_AMT": "5152.27",
   "TRADE_VOL": "427.16", "AVG_PE_RATE": "13.45", "LIST_NUM": "1700",
   "TOTAL_VALUE": "504453.19", "NEGO_VALUE": "484990.95", ...},
  {"PRODUCT_CODE": "02", ...}, {"PRODUCT_CODE": "03", ...},
  {"PRODUCT_CODE": "11", ...}, {"PRODUCT_CODE": "17", "TRADE_AMT": "7732.82",
   "TRADE_VOL": "477.45", "AVG_PE_RATE": "15.98", "LIST_NUM": "2359", ...}
]}
```

`PRODUCT_CODE`（页面表头顺序：股票/主板A/主板B/科创板/股票回购）：
`17`=股票合计（不含回购，LIST_NUM=01+02+03 之和）、`01`=主板A、`02`=主板B、
`03`=科创板、`11`=股票回购。**本单元固定取 17（市场股票合计）**。
字段单位（页面 JS 注释原文）：`TRADE_AMT` 成交金额(亿元)、`TRADE_VOL`
成交量(亿股/亿份)、`AVG_PE_RATE` 平均市盈率(倍)、`TOTAL_VALUE` 市价总值(亿元)。

### 1.2 SZSE（深交所）「股票日度概况」+「基本指标」

| 项 | 值 |
|---|---|
| 页面 | `https://www.szse.cn/market/stock/situation/daily/index.html`（日度概况，`curCatalogId='scsj_gprdgk_after'`）；PE 页 `https://www.szse.cn/market/stock/indicator/index.html`（`1803_after`） |
| 端点（量/额） | `GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=scsj_gprdgk_after&TABKEY=tab1&txtQueryDate=YYYY-MM-DD&tjzqlb=D&bkbz=GP%7CXZ%7CCY&cjbz=cjl%7Ccjje` |
| 端点（估值） | `GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1803_after&TABKEY=tab5&txtQueryDate=YYYY-MM-DD` |
| 鉴权 | **免 key、免鉴权**（常规 UA + 页面 Referer 即可） |
| 频度 | 日更；`txtQueryDate` 必填（报表 metadata `conditions` 声明 required） |
| 报表元数据 | 同一 `ShowReport/data` 端点不带日期请求即返回 `metadata`（cols/conditions/footer）——conditions 的参数名与默认值以此为准 |

日度概况响应是**转置表**（实测 2026-11-06）：

```json
{"metadata": {"cols": {"zbmc": "指标名称", "xz": "主板", "cy": "创业板", "gp": "深市合计"},
              "footer": "数据统计范围包含存托凭证。"},
 "data": [
  {"zbmc": "成交量（亿）",     "xz": "380.64",   "cy": "189.12",   "gp": "569.76"},
  {"zbmc": "成交金额（亿元）", "xz": "4,414.95", "cy": "4,442.21", "gp": "8,857.16"}
 ]}
```

本单元取 `gp`（深市合计）列。基本指标 tab5 为「指标名称 × 本日数值/比上日增减/
幅度%」行式，含 `股票平均市盈率`（倍，2026-11-06 实测 25.65）与
`股票成交金额（亿元）`（8,857.15，与日度概况 8,857.16 属不同报表的四舍五入差）。

## 2. 通道选择理由（brief 线索 → 实测修正）

| 候选通道 | 实测 | 判定 |
|---|---|---|
| SZSE `CATALOGID=1803_scxw`（brief 原文） | 200 但 `error: "noalert@显示报表数据失败"` | ❌ catalogid 失效 |
| SZSE `CATALOGID=1803`（去尾码） | 200，但内容是指数行情（深证成指等） | ❌ 不是成交概况 |
| SZSE 页面 `data-s-catalog-id` → `scsj_gprdgk_after` + metadata conditions | 200，成交量/成交金额齐 | ✅ **选中（量/额）** |
| SSE `sqlId=COMMON_SSE_SJ_GPSJ_CJGK_DAYMD_C`（brief 原文） | 200 但 `result:null`（带日期参数同样 null） | ❌ 坏通道 |
| SSE 页面 JS `overviewDay` 块 → `COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C` + `PRODUCT_CODE` + `type=inParams` | 200，5 板块全字段 | ✅ **选中（SSE）** |
| SZSE `1803_sczm`（市场总貌 tab1） | 需 `txtQueryDate`；只有证券类别 × 成交金额/市值，**无 PE 无成交量** | ❌ 列不齐 |
| SZSE `1803_after` tab5（基本指标） | 200，含 `股票平均市盈率` | ✅ **选中（估值列）** |

## 3. 直连证据（2026-11-07 实测，本机）

| 请求 | 结果 |
|---|---|
| `GET http://query.sse.com.cn/commonQuery.do?...MRGK_C...`（带 Referer） | HTTP 200，`result` 5 行（2026-11-06） |
| `GET https://www.szse.cn/api/report/ShowReport/data?...scsj_gprdgk_after...txtQueryDate=2026-11-06` | HTTP 200，2 行指标 |
| `GET https://www.szse.cn/api/report/ShowReport/data?...1803_after&tab5&txtQueryDate=2026-11-06` | HTTP 200，13 行（含 PE 25.65） |
| 周六实测（2026-11-07 运行） | 两所均回溯到 2026-11-06（周五），spider 冒烟出 2 行 |

→ 免 key 免鉴权直连全绿，**无需 fx01 代理出口**；manifest 不写 `egress_secret`、
不写 `site`。（tencent 站点出口复测待合入后由 CI/人工补齐。）

## 4. 单位换算

| 上游列 | 上游单位 | 换算 | 输出列 |
|---|---|---|---|
| SSE `TRADE_AMT` / SZSE `成交金额（亿元）` | 亿元 | × 1e8 | `turnover_amount`（元） |
| SSE `TRADE_VOL` / SZSE `成交量（亿）` | 亿股 | × 1e8 | `turnover_volume`（股） |
| SSE `AVG_PE_RATE` / SZSE `股票平均市盈率` | 倍 | 原值透传 | `avg_pe` |
| SSE `TRADE_DATE` | YYYYMMDD | 插横线 | `trade_date`（YYYY-MM-DD） |

量级 sanity（2026-11-06）：SSE 7,732.82 亿元 / 477.45 亿股；SZSE 8,857.16 亿元 /
569.76 亿股——与两所公开报道的当日成交同量级。

## 5. 口坑

- **brief 两个端点都不能直接用**：SZSE `1803_scxw` 报
  `noalert@显示报表数据失败`；SSE `COMMON_SSE_SJ_GPSJ_CJGK_DAYMD_C` 恒
  `result:null`。真通道分别是 `scsj_gprdgk_after`（藏在日度概况页面
  `data-s-catalog-id` 属性里）与 `COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C`（藏在
  `search_stockData_2021.js` 的 `overviewDay` 块里），都要带配套参数。
- **SZSE 报表条件名不可猜**：日期参数叫 `txtQueryDate`（不是 txtDate/rq），
  来自报表 metadata `conditions[].name`；不带它直接报
  `日期不合法，日期不能为空。`。日度概况还有三个隐藏条件
  `tjzqlb=D`、`bkbz=GP|XZ|CY`、`cjbz=cjl|cjje`，同样从 conditions 默认值读出。
- **SZSE 非交易日静默回空**：`err=None` 但 `data=[]`（周六实测），既不报错也
  不回最近交易日——spider 从今天（UTC+8）逐日回溯（上限 10 日，跨最长假期），
  第一个有数据的交易日即最新交易日。
- **SZSE 报表是转置表**：指标是行（`zbmc`）不是列，且不带日期列——
  `trade_date` 就是查询参数本身。中文指标名（`成交量（亿）`/
  `成交金额（亿元）`）漂移即抛错并列出实际指标清单。
- **SZSE 数值带千分位逗号**（`"8,857.16"`），解析前必须去逗号；`-` → null。
- **SSE 必须 Referer**：`query.sse.com.cn` 校验 `Referer: http://www.sse.com.cn/`。
- **SSE 17=股票合计（不含回购）**：11 是股票回购，勿混入合计；无 PE 板块
  `AVG_PE_RATE="-"` → null；核心列（TRADE_DATE/TRADE_AMT/TRADE_VOL）缺失即抛错。
- **估值列是 best-effort**：SZSE PE 行当日未发布/报表失败 → `avg_pe=null`
  （不编造），量/额列缺失则失败即红——两条纪律的分工见 spider.py docstring。
- **仅常规 UA**：免鉴权站点，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 报表 error 字段 / 结构缺失 / 中文列名
  漂移 / 回溯 10 日仍 0 观测 → `RuntimeError`，不静默回空。

## 6. golden 断言口径（跨期稳定）

`golden/001-sse-szse-market-stats.json` 用 `{"limit": 20}` 重放（每所最新交易
日一行，共 2 行），只锚结构常量：`source=sse-szse-market-stats` /
`exchange=SSE` / `exchange=SZSE` + `min_rows=2`。**绝不锚** `trade_date`
（滚动）与任何当期成交额/成交量/PE 值（每日变动）；`scraped_at` /
`upstream_updated` 进 `whitelist_fields`。

## 7. 自检

```bash
python3 spiders/sse-szse-market-stats/spider.py   # 冒烟：打印两所最新交易日 2 行
python3 scripts/validate_manifests.py             # manifest 契约校验
python3 scripts/check_manifest_commands.py        # manifest 命令漂移 lint
python3 scripts/conformance_gate.py --roots .     # 仓级机械闸
```
